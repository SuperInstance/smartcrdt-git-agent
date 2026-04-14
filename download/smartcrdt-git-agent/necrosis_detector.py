"""Necrosis Detector — CRDT-based fleet health monitoring subsystem.

Detects agent failures and fleet health degradation using lightweight CRDT
primitives (LWW-Register for heartbeats, PN-Counter for test attrition) and
a circuit-breaker state machine that transitions agents through four states:

    healthy -> degraded -> critical -> necrotic

Designed based on roundtable simulation findings for the SmartCRDT git-agent
fleet.  When an agent goes necrotic the *tow protocol* identifies the nearest
healthy agent to take over its workload.

Usage::

    from necrosis_detector import create_necrosis_detector

    nd = create_necrosis_detector()
    nd.record_heartbeat({
        "agent_id": "agent-42",
        "timestamp": 1714000000.0,
        "test_count": 150,
        "tasks_completed": 37,
        "repo_count": 5,
        "status": "active",
        "metadata": {"region": "us-east"},
    })
    pulse = nd.get_fleet_pulse()
    anomalies = nd.beachcomb_scan()
    report = nd.export_report()

Python 3.9+ stdlib only — zero external dependencies.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD_DEGRADED: float = 30 * 60        # 30 minutes in seconds
_DEFAULT_THRESHOLD_CRITICAL: float = 2 * 3600       # 2 hours in seconds
_DEFAULT_THRESHOLD_NECROTIC: float = 6 * 3600       # 6 hours in seconds
_DEFAULT_THRESHOLD_TEST_DROP_PCT: float = 10.0       # 10 %
_FLEET_PULSE_HEALTHY_WEIGHT: float = 1.0
_FLEET_PULSE_DEGRADED_WEIGHT: float = 0.6
_FLEET_PULSE_CRITICAL_WEIGHT: float = 0.25
_FLEET_PULSE_NECROTIC_WEIGHT: float = 0.0
_MAX_FORENSICS_PER_AGENT: int = 500


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AgentState(str, Enum):
    """Circuit-breaker states for a fleet agent."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    NECROTIC = "necrotic"


class AlertSeverity(str, Enum):
    """Severity levels for generated alerts."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    FATAL = "fatal"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AgentHeartbeat:
    """Heartbeat payload received from a fleet agent.

    Attributes
    ----------
    agent_id :
        Unique identifier for the agent.
    timestamp :
        Epoch seconds when the heartbeat was emitted.
    test_count :
        Number of tests the agent is tracking / has executed.
    tasks_completed :
        Cumulative tasks completed by the agent.
    repo_count :
        Number of repositories the agent is managing.
    status :
        Agent-reported status — ``"active"``, ``"idle"``, or ``"error"``.
    metadata :
        Arbitrary key-value pairs for extensibility.
    """
    agent_id: str
    timestamp: float
    test_count: int = 0
    tasks_completed: int = 0
    repo_count: int = 0
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "test_count": self.test_count,
            "tasks_completed": self.tasks_completed,
            "repo_count": self.repo_count,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentHeartbeat:
        """Deserialise from a plain dict."""
        return cls(
            agent_id=str(data["agent_id"]),
            timestamp=float(data["timestamp"]),
            test_count=int(data.get("test_count", 0)),
            tasks_completed=int(data.get("tasks_completed", 0)),
            repo_count=int(data.get("repo_count", 0)),
            status=str(data.get("status", "active")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class _StateTransition:
    """Single entry in the forensic state-transition log."""
    agent_id: str
    from_state: str
    to_state: str
    timestamp: float
    reason: str


# ---------------------------------------------------------------------------
# Internal CRDT primitives
# ---------------------------------------------------------------------------

class _LWWRegister:
    """Last-Writer-Wins Register keyed on a monotonically increasing timestamp.

    Merge rule: the value with the greater timestamp wins.  On a tie the
    lexicographically larger ``agent_id`` wins, guaranteeing determinism
    without requiring distributed consensus.

    Parameters
    ----------
    key :
        Logical identifier for this register (e.g. ``"agent-42:status"``).
    """

    __slots__ = ("_key", "_timestamp", "_value", "_node_id")

    def __init__(self, key: str) -> None:
        self._key = key
        self._timestamp: float = 0.0
        self._value: Any = None
        self._node_id: str = ""

    # -- operations ----------------------------------------------------------

    def set(self, value: Any, timestamp: float, node_id: str = "") -> None:
        """Write *value* only if *timestamp* is >= the stored timestamp."""
        if timestamp >= self._timestamp:
            if timestamp == self._timestamp and node_id < self._node_id:
                return  # Existing write from a "higher" node wins on tie.
            self._timestamp = timestamp
            self._value = value
            self._node_id = node_id

    def get(self) -> Any:
        """Return the current value."""
        return self._value

    def merge(self, other: _LWWRegister) -> None:
        """Merge a remote replica into this register (LWW semantics)."""
        if (other._timestamp, other._node_id) >= (self._timestamp, self._node_id):
            self._timestamp = other._timestamp
            self._value = other._value
            self._node_id = other._node_id

    # -- introspection -------------------------------------------------------

    @property
    def timestamp(self) -> float:  # pragma: no cover
        return self._timestamp


class _PNCounter:
    """Positive-Negative Counter for tracking net test-count changes.

    Uses two internal G-Counters (one for increments, one for decrements).
    Merge sums the positive and negative halves independently.  The
    resolved value is ``positive - negative``.

    Parameters
    ----------
    key :
        Logical identifier (e.g. ``"agent-42:tests"``).
    """

    __slots__ = ("_key", "_positive", "_negative")

    def __init__(self, key: str) -> None:
        self._key = key
        self._positive: int = 0
        self._negative: int = 0

    # -- operations ----------------------------------------------------------

    def increment(self, delta: int = 1) -> None:
        """Record a positive change (tests added)."""
        if delta > 0:
            self._positive += delta

    def decrement(self, delta: int = 1) -> None:
        """Record a negative change (tests removed)."""
        if delta > 0:
            self._negative += delta

    def value(self) -> int:
        """Return the net value (positive minus negative)."""
        return self._positive - self._negative

    def merge(self, other: _PNCounter) -> None:
        """Merge a remote replica by summing halves independently."""
        self._positive += other._positive
        self._negative += other._negative


# ---------------------------------------------------------------------------
# Per-agent internal record
# ---------------------------------------------------------------------------

@dataclass
class _AgentRecord:
    """Internal bookkeeping for a single tracked agent."""
    agent_id: str
    state: AgentState = AgentState.HEALTHY
    last_heartbeat_ts: float = 0.0
    last_seen_ts: float = 0.0
    test_count: int = 0
    tasks_completed: int = 0
    repo_count: int = 0
    agent_status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)
    forensics: List[_StateTransition] = field(default_factory=list)
    status_register: _LWWRegister = field(
        default_factory=lambda: _LWWRegister(""),
    )
    test_counter: _PNCounter = field(
        default_factory=lambda: _PNCounter(""),
    )


# ---------------------------------------------------------------------------
# NecrosisDetector
# ---------------------------------------------------------------------------

class NecrosisDetector:
    """CRDT-based fleet health monitor with circuit-breaker state machine.

    Tracks agent heartbeats via LWW-Registers and test-count changes via
    PN-Counters.  Agents are classified into four circuit-breaker states
    (healthy / degraded / critical / necrotic) based on configurable
    heartbeat-timeout thresholds.

    Parameters
    ----------
    thresholds :
        Optional dict overriding the default detection thresholds.  Accepted
        keys:

        * ``threshold_heartbeat_degraded`` — seconds of silence before an
          agent moves to *degraded* (default 1800).
        * ``threshold_heartbeat_critical`` — seconds before *critical*
          (default 7200).
        * ``threshold_heartbeat_necrotic`` — seconds before *necrotic*
          (default 21600).
        * ``threshold_test_drop_percent`` — percentage test-count decline
          that triggers an alert (default 10.0).

    Attributes
    ----------
    thresholds :
        The active threshold configuration (dict copy).
    """

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None) -> None:
        self._thresholds: Dict[str, float] = {
            "threshold_heartbeat_degraded": _DEFAULT_THRESHOLD_DEGRADED,
            "threshold_heartbeat_critical": _DEFAULT_THRESHOLD_CRITICAL,
            "threshold_heartbeat_necrotic": _DEFAULT_THRESHOLD_NECROTIC,
            "threshold_test_drop_percent": _DEFAULT_THRESHOLD_TEST_DROP_PCT,
        }
        if thresholds:
            self._thresholds.update(thresholds)

        self._agents: Dict[str, _AgentRecord] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def thresholds(self) -> Dict[str, float]:
        """Return a shallow copy of the active threshold configuration."""
        return dict(self._thresholds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_heartbeat(self, heartbeat: Dict[str, Any]) -> Dict[str, Any]:
        """Record an agent heartbeat and evaluate state transitions.

        Parameters
        ----------
        heartbeat :
            Dict matching the :class:`AgentHeartbeat` schema (must contain
            at least ``agent_id`` and ``timestamp``).

        Returns
        -------
        dict
            Result with keys ``agent_id``, ``previous_state``,
            ``current_state``, ``transitioned`` (bool), ``alerts_fired``
            (list), and ``test_attrition_detected`` (bool).
        """
        hb = AgentHeartbeat.from_dict(heartbeat)
        now = time.time()

        # Retrieve or create the agent record.
        rec = self._agents.get(hb.agent_id)
        if rec is None:
            rec = _AgentRecord(
                agent_id=hb.agent_id,
                status_register=_LWWRegister(f"{hb.agent_id}:status"),
                test_counter=_PNCounter(f"{hb.agent_id}:tests"),
            )
            self._agents[hb.agent_id] = rec

        previous_state = rec.state
        alerts_fired: List[Dict[str, Any]] = []
        test_attrition_detected = False

        # --- LWW-Register: update agent status ---
        rec.status_register.set(hb.status, hb.timestamp, hb.agent_id)
        rec.agent_status = rec.status_register.get() or hb.status

        # --- PN-Counter: track test-count delta ---
        if rec.test_count > 0:
            delta = hb.test_count - rec.test_count
            if delta > 0:
                rec.test_counter.increment(delta)
            elif delta < 0:
                rec.test_counter.decrement(abs(delta))
                # Check test attrition threshold.
                if rec.test_count > 0:
                    drop_pct = (abs(delta) / rec.test_count) * 100
                    if drop_pct > self._thresholds["threshold_test_drop_percent"]:
                        test_attrition_detected = True
                        alert = self._emit_alert(
                            agent_id=hb.agent_id,
                            severity=AlertSeverity.WARNING,
                            alert_type="test_attrition",
                            message=(
                                f"Agent {hb.agent_id}: test count dropped by "
                                f"{drop_pct:.1f}% (from {rec.test_count} to "
                                f"{hb.test_count})"
                            ),
                        )
                        alerts_fired.append(alert)
        rec.test_count = hb.test_count

        # --- Heartbeat timestamp (monotonic) ---
        if hb.timestamp > rec.last_heartbeat_ts:
            rec.last_heartbeat_ts = hb.timestamp

        rec.last_seen_ts = now
        rec.tasks_completed = hb.tasks_completed
        rec.repo_count = hb.repo_count
        rec.metadata = hb.metadata

        # --- Circuit-breaker state evaluation ---
        silence = now - rec.last_seen_ts

        # Recovery: any heartbeat resets to healthy.
        rec.state = AgentState.HEALTHY

        # Re-evaluate based on silence (from perspective of *now*).
        # Only degrade if silence exceeds thresholds.  Since we just
        # received a heartbeat, silence == 0, so the agent stays healthy.
        # The degradation is handled lazily in get_agent_state /
        # beachcomb_scan / get_fleet_pulse.

        # Record state transition if it changed.
        if rec.state != previous_state:
            rec.forensics.append(_StateTransition(
                agent_id=hb.agent_id,
                from_state=previous_state.value,
                to_state=rec.state.value,
                timestamp=now,
                reason="heartbeat_received_recovery",
            ))
            self._trim_forensics(rec)

        return {
            "agent_id": hb.agent_id,
            "previous_state": previous_state.value,
            "current_state": rec.state.value,
            "transitioned": rec.state != previous_state,
            "alerts_fired": alerts_fired,
            "test_attrition_detected": test_attrition_detected,
            "recorded_at": now,
        }

    def get_agent_state(self, agent_id: str) -> Dict[str, Any]:
        """Return the current state and metrics for a single agent.

        Parameters
        ----------
        agent_id :
            Unique identifier of the agent.

        Returns
        -------
        dict
            Agent state snapshot with ``agent_id``, ``state``, ``silence``
            (seconds since last activity), ``last_heartbeat_ts``,
            ``last_seen_ts``, ``test_count``, ``tasks_completed``,
            ``repo_count``, ``status``, ``metadata``, and
            ``test_counter_value``.  If the agent is unknown, returns a
            dict with ``state: "unknown"``.
        """
        rec = self._agents.get(agent_id)
        if rec is None:
            return {"agent_id": agent_id, "state": "unknown"}

        now = time.time()
        effective_state = self._effective_state(rec, now)
        silence = now - rec.last_seen_ts

        return {
            "agent_id": rec.agent_id,
            "state": effective_state.value,
            "silence_seconds": round(silence, 2),
            "last_heartbeat_ts": rec.last_heartbeat_ts,
            "last_seen_ts": rec.last_seen_ts,
            "test_count": rec.test_count,
            "tasks_completed": rec.tasks_completed,
            "repo_count": rec.repo_count,
            "status": rec.agent_status,
            "metadata": dict(rec.metadata),
            "test_counter_value": rec.test_counter.value(),
            "pn_counter_positive": rec.test_counter._positive,
            "pn_counter_negative": rec.test_counter._negative,
        }

    def get_fleet_pulse(self) -> Dict[str, Any]:
        """Compute the aggregate fleet health score.

        The *fleet pulse* is a weighted average where healthy agents
        contribute 1.0, degraded 0.6, critical 0.25, and necrotic 0.0.
        The result is a value between 0.0 and 1.0.

        Returns
        -------
        dict
            Keys: ``fleet_health_score`` (0–1), ``total_agents``,
            ``healthy_count``, ``degraded_count``, ``critical_count``,
            ``necrotic_count``, ``unknown_count``, ``score_breakdown``
            (list of per-agent contributions), ``computed_at``.
        """
        now = time.time()
        counts: Dict[str, int] = {
            "healthy": 0, "degraded": 0, "critical": 0,
            "necrotic": 0, "unknown": 0,
        }
        weight_map = {
            AgentState.HEALTHY: _FLEET_PULSE_HEALTHY_WEIGHT,
            AgentState.DEGRADED: _FLEET_PULSE_DEGRADED_WEIGHT,
            AgentState.CRITICAL: _FLEET_PULSE_CRITICAL_WEIGHT,
            AgentState.NECROTIC: _FLEET_PULSE_NECROTIC_WEIGHT,
        }
        breakdown: List[Dict[str, Any]] = []
        total_weighted = 0.0
        total_agents = len(self._agents)

        for rec in self._agents.values():
            state = self._effective_state(rec, now)
            w = weight_map.get(state, 0.0)
            total_weighted += w
            counts[state.value] += 1
            breakdown.append({
                "agent_id": rec.agent_id,
                "state": state.value,
                "weight": w,
            })

        score = round(total_weighted / total_agents, 4) if total_agents > 0 else 0.0

        return {
            "fleet_health_score": score,
            "total_agents": total_agents,
            "healthy_count": counts["healthy"],
            "degraded_count": counts["degraded"],
            "critical_count": counts["critical"],
            "necrotic_count": counts["necrotic"],
            "unknown_count": counts["unknown"],
            "score_breakdown": breakdown,
            "computed_at": now,
        }

    def beachcomb_scan(self) -> List[Dict[str, Any]]:
        """Perform a periodic anomaly scan across all tracked agents.

        The *beachcomb* evaluates every agent's effective state against
        the heartbeat-timeout thresholds and fires alerts for any agents
        that have silently transitioned into degraded, critical, or
        necrotic states since their last recorded heartbeat.

        Returns
        -------
        list[dict]
            Anomaly records with ``agent_id``, ``anomaly_type``,
            ``severity``, ``effective_state``, ``silence_seconds``,
            ``details``, ``detected_at``.
        """
        now = time.time()
        anomalies: List[Dict[str, Any]] = []

        for rec in self._agents.values():
            effective = self._effective_state(rec, now)
            silence = now - rec.last_seen_ts

            if effective == AgentState.HEALTHY:
                continue

            anomaly_type: str
            severity: AlertSeverity
            details: str

            if effective == AgentState.NECROTIC:
                anomaly_type = "agent_necrotic"
                severity = AlertSeverity.FATAL
                details = (
                    f"No heartbeat for {self._fmt_duration(silence)} — "
                    f"exceeds necrotic threshold of "
                    f"{self._fmt_duration(self._thresholds['threshold_heartbeat_necrotic'])}"
                )
            elif effective == AgentState.CRITICAL:
                anomaly_type = "agent_critical"
                severity = AlertSeverity.CRITICAL
                details = (
                    f"No heartbeat for {self._fmt_duration(silence)} — "
                    f"exceeds critical threshold of "
                    f"{self._fmt_duration(self._thresholds['threshold_heartbeat_critical'])}"
                )
            else:
                anomaly_type = "agent_degraded"
                severity = AlertSeverity.WARNING
                details = (
                    f"No heartbeat for {self._fmt_duration(silence)} — "
                    f"exceeds degraded threshold of "
                    f"{self._fmt_duration(self._thresholds['threshold_heartbeat_degraded'])}"
                )

            # Fire alert for newly-detected anomalies.
            self._emit_alert(
                agent_id=rec.agent_id,
                severity=severity,
                alert_type=anomaly_type,
                message=details,
            )

            # Transition the record if it differs from last-known state.
            if effective != rec.state:
                rec.forensics.append(_StateTransition(
                    agent_id=rec.agent_id,
                    from_state=rec.state.value,
                    to_state=effective.value,
                    timestamp=now,
                    reason=f"beachcomb_scan: {anomaly_type}",
                ))
                self._trim_forensics(rec)
                rec.state = effective

            anomalies.append({
                "agent_id": rec.agent_id,
                "anomaly_type": anomaly_type,
                "severity": severity.value,
                "effective_state": effective.value,
                "silence_seconds": round(silence, 2),
                "details": details,
                "detected_at": now,
            })

        # Sort by severity (worst first).
        _severity_order = {
            AlertSeverity.FATAL: 0, AlertSeverity.CRITICAL: 1,
            AlertSeverity.WARNING: 2, AlertSeverity.INFO: 3,
        }
        anomalies.sort(key=lambda a: _severity_order.get(
            AlertSeverity(a["severity"]), 99))

        return anomalies

    def get_alerts(
        self,
        since: Optional[float] = None,
        severity: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve generated alerts, optionally filtered.

        Parameters
        ----------
        since :
            Epoch timestamp floor; only alerts at or after this time are
            returned.  ``None`` means all-time.
        severity :
            If given, only alerts matching this severity string (e.g.
            ``"warning"``, ``"critical"``) are returned.

        Returns
        -------
        list[dict]
            Matching alerts ordered newest-first.  Each alert has
            ``alert_id``, ``agent_id``, ``severity``, ``alert_type``,
            ``message``, ``timestamp``.
        """
        results: List[Dict[str, Any]] = []
        for alert in self._alerts:
            if since is not None and alert["timestamp"] < since:
                continue
            if severity is not None and alert["severity"] != severity.lower():
                continue
            results.append(alert)
        results.sort(key=lambda a: a["timestamp"], reverse=True)
        return results

    def suggest_tow(self, agent_id: str) -> Dict[str, Any]:
        """Suggest the nearest healthy agent to take over for *agent_id*.

        The *tow protocol* finds the agent with the most recent heartbeat
        among all agents currently in the healthy state, excluding the
        failing agent itself.  Proximity is approximated by heartbeat
        recency.

        Parameters
        ----------
        agent_id :
            The failing agent that needs to be towed.

        Returns
        -------
        dict
            ``tow_candidate`` (agent_id or ``None``), ``failing_agent``,
            ``failing_state``, ``reason``, ``suggested_at``.  When no
            healthy candidate exists the reason explains why.
        """
        now = time.time()
        rec = self._agents.get(agent_id)
        if rec is None:
            return {
                "tow_candidate": None,
                "failing_agent": agent_id,
                "failing_state": "unknown",
                "reason": "Agent not tracked by the detector.",
                "suggested_at": now,
            }

        effective_state = self._effective_state(rec, now)
        if effective_state == AgentState.HEALTHY:
            return {
                "tow_candidate": None,
                "failing_agent": agent_id,
                "failing_state": effective_state.value,
                "reason": "Agent is healthy — no tow required.",
                "suggested_at": now,
            }

        # Find the healthy agent with the most recent heartbeat.
        best_id: Optional[str] = None
        best_ts: float = 0.0

        for other in self._agents.values():
            if other.agent_id == agent_id:
                continue
            if self._effective_state(other, now) != AgentState.HEALTHY:
                continue
            if other.last_heartbeat_ts > best_ts:
                best_ts = other.last_heartbeat_ts
                best_id = other.agent_id

        if best_id is None:
            return {
                "tow_candidate": None,
                "failing_agent": agent_id,
                "failing_state": effective_state.value,
                "reason": (
                    "No healthy agents available in the fleet to "
                    "perform the tow."
                ),
                "suggested_at": now,
            }

        best_rec = self._agents[best_id]
        return {
            "tow_candidate": best_id,
            "failing_agent": agent_id,
            "failing_state": effective_state.value,
            "reason": (
                f"Agent {best_id} is the nearest healthy candidate "
                f"(last heartbeat {self._fmt_duration(now - best_rec.last_seen_ts)} ago, "
                f"managing {best_rec.repo_count} repos)."
            ),
            "candidate_details": {
                "agent_id": best_id,
                "last_heartbeat_ts": best_rec.last_heartbeat_ts,
                "repo_count": best_rec.repo_count,
                "tasks_completed": best_rec.tasks_completed,
                "test_count": best_rec.test_count,
            },
            "suggested_at": now,
        }

    def get_forensics(self, agent_id: str) -> Dict[str, Any]:
        """Return the state-transition history for forensic analysis.

        Parameters
        ----------
        agent_id :
            Agent whose history is requested.

        Returns
        -------
        dict
            ``agent_id``, ``current_state``, ``transition_count``,
            ``transitions`` (list of ``from_state``, ``to_state``,
            ``timestamp``, ``reason`` dicts), ``agent_first_seen``,
            ``agent_last_seen``.
        """
        rec = self._agents.get(agent_id)
        if rec is None:
            return {
                "agent_id": agent_id,
                "current_state": "unknown",
                "transition_count": 0,
                "transitions": [],
                "agent_first_seen": None,
                "agent_last_seen": None,
            }

        now = time.time()
        first_seen = min(
            (t.timestamp for t in rec.forensics), default=rec.last_seen_ts
        )

        return {
            "agent_id": rec.agent_id,
            "current_state": self._effective_state(rec, now).value,
            "transition_count": len(rec.forensics),
            "transitions": [
                {
                    "from_state": t.from_state,
                    "to_state": t.to_state,
                    "timestamp": t.timestamp,
                    "reason": t.reason,
                }
                for t in rec.forensics
            ],
            "agent_first_seen": first_seen,
            "agent_last_seen": rec.last_seen_ts,
        }

    def get_all_agent_states(self) -> Dict[str, Dict[str, Any]]:
        """Return a snapshot of every tracked agent's current state.

        Returns
        -------
        dict[str, dict]
            Mapping from ``agent_id`` to the output of
            :meth:`get_agent_state`.
        """
        return {aid: self.get_agent_state(aid) for aid in self._agents}

    def configure(self, thresholds: Dict[str, Any]) -> Dict[str, Any]:
        """Update detection thresholds at runtime.

        Parameters
        ----------
        thresholds :
            Dict of threshold keys to new values.  Unrecognised keys are
            silently ignored.

        Returns
        -------
        dict
            ``updated_keys`` (list of keys that changed), ``current_thresholds``
            (full snapshot after update).
        """
        updated: List[str] = []
        for key, value in thresholds.items():
            if key in self._thresholds and value != self._thresholds.get(key):
                self._thresholds[key] = float(value)
                updated.append(key)

        return {
            "updated_keys": updated,
            "current_thresholds": dict(self._thresholds),
        }

    def export_report(self) -> str:
        """Generate a Markdown report of fleet health.

        Includes a summary table, per-agent breakdown, anomaly list,
        alert summary, and forensic notes.

        Returns
        -------
        str
            Multi-line Markdown string.
        """
        now = time.time()
        pulse = self.get_fleet_pulse()
        all_states = self.get_all_agent_states()
        anomalies = self.beachcomb_scan()
        recent_alerts = self.get_alerts(since=now - 3600)
        lines: List[str] = []

        lines.append("# Fleet Health Report")
        lines.append("")
        lines.append(f"**Generated:** {_iso(now)}")
        lines.append("")

        # --- Summary ---
        score = pulse["fleet_health_score"]
        score_label = (
            "Excellent" if score >= 0.9 else
            "Good" if score >= 0.7 else
            "Fair" if score >= 0.5 else
            "Poor" if score >= 0.25 else
            "Critical"
        )
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Fleet Health Score | {score:.2%} ({score_label}) |")
        lines.append(f"| Total Agents | {pulse['total_agents']} |")
        lines.append(f"| Healthy | {pulse['healthy_count']} |")
        lines.append(f"| Degraded | {pulse['degraded_count']} |")
        lines.append(f"| Critical | {pulse['critical_count']} |")
        lines.append(f"| Necrotic | {pulse['necrotic_count']} |")
        lines.append("")

        # --- Per-agent breakdown ---
        if all_states:
            lines.append("## Agent Details")
            lines.append("")
            lines.append(
                "| Agent | State | Silence | Tests | Tasks | Repos | Status |"
            )
            lines.append(
                "|-------|-------|---------|-------|-------|-------|--------|"
            )
            for aid in sorted(all_states):
                s = all_states[aid]
                if s["state"] == "unknown":
                    lines.append(f"| {aid} | unknown | — | — | — | — | — |")
                    continue
                silence = self._fmt_duration(s.get("silence_seconds", 0))
                lines.append(
                    f"| {aid} | {s['state']} | {silence} | "
                    f"{s['test_count']} | {s['tasks_completed']} | "
                    f"{s['repo_count']} | {s['status']} |"
                )
            lines.append("")

        # --- Anomalies ---
        if anomalies:
            lines.append("## Anomalies Detected")
            lines.append("")
            lines.append("| Agent | Type | Severity | Silence | Details |")
            lines.append("|-------|------|----------|---------|---------|")
            for a in anomalies:
                lines.append(
                    f"| {a['agent_id']} | {a['anomaly_type']} | "
                    f"{a['severity']} | {self._fmt_duration(a['silence_seconds'])} | "
                    f"{a['details']} |"
                )
            lines.append("")

        # --- Alerts ---
        if recent_alerts:
            lines.append("## Recent Alerts (last hour)")
            lines.append("")
            lines.append("| Time | Agent | Severity | Type | Message |")
            lines.append("|------|-------|----------|------|---------|")
            for alert in recent_alerts:
                lines.append(
                    f"| {_iso(alert['timestamp'])} | {alert['agent_id']} | "
                    f"{alert['severity']} | {alert['alert_type']} | "
                    f"{alert['message']} |"
                )
            lines.append("")

        # --- Thresholds ---
        lines.append("## Active Thresholds")
        lines.append("")
        lines.append("| Threshold | Value |")
        lines.append("|-----------|-------|")
        for k, v in self._thresholds.items():
            label = k.replace("threshold_", "").replace("_", " ").title()
            if "percent" in k:
                lines.append(f"| {label} | {v}% |")
            else:
                lines.append(f"| {label} | {self._fmt_duration(v)} |")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_state(self, rec: _AgentRecord, now: float) -> AgentState:
        """Compute the effective state based on silence duration.

        A heartbeat resets to healthy.  As silence grows the agent
        degrades through the circuit-breaker chain.  This is a *lazy*
        evaluation — the stored ``rec.state`` may lag behind the
        effective state until the next beachcomb scan.
        """
        if rec.last_seen_ts == 0.0:
            return AgentState.NECROTIC

        silence = now - rec.last_seen_ts

        if silence > self._thresholds["threshold_heartbeat_necrotic"]:
            return AgentState.NECROTIC
        if silence > self._thresholds["threshold_heartbeat_critical"]:
            return AgentState.CRITICAL
        if silence > self._thresholds["threshold_heartbeat_degraded"]:
            return AgentState.DEGRADED
        return AgentState.HEALTHY

    def _emit_alert(
        self,
        agent_id: str,
        severity: AlertSeverity,
        alert_type: str,
        message: str,
    ) -> Dict[str, Any]:
        """Create and store an alert, returning it for immediate use."""
        ts = time.time()
        alert: Dict[str, Any] = {
            "alert_id": f"{alert_type}:{agent_id}:{int(ts * 1000)}",
            "agent_id": agent_id,
            "severity": severity.value,
            "alert_type": alert_type,
            "message": message,
            "timestamp": ts,
        }
        self._alerts.append(alert)
        # Keep the alert list from growing unbounded (cap at 10 000).
        if len(self._alerts) > 10_000:
            self._alerts = self._alerts[-10_000:]
        return alert

    @staticmethod
    def _trim_forensics(rec: _AgentRecord) -> None:
        """Trim forensic log to the per-agent cap."""
        if len(rec.forensics) > _MAX_FORENSICS_PER_AGENT:
            rec.forensics = rec.forensics[-_MAX_FORENSICS_PER_AGENT:]

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        """Format a duration in seconds to a human-readable string."""
        if seconds < 0:
            seconds = 0.0
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            return f"{seconds / 60:.1f}m"
        if seconds < 86400:
            hours = seconds / 3600
            mins = (seconds % 3600) / 60
            return f"{hours:.1f}h {mins:.0f}m"
        days = seconds / 86400
        hours = (seconds % 86400) / 3600
        return f"{days:.1f}d {hours:.0f}h"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"NecrosisDetector(agents={len(self._agents)}, "
            f"alerts={len(self._alerts)})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(epoch: float) -> str:
    """Format an epoch timestamp as an ISO-8601 UTC string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_necrosis_detector(
    thresholds: Optional[Dict[str, Any]] = None,
) -> NecrosisDetector:
    """Create and return a configured :class:`NecrosisDetector` instance.

    Parameters
    ----------
    thresholds :
        Optional dict of threshold overrides (see
        :class:`NecrosisDetector` for accepted keys).

    Usage::

        from necrosis_detector import create_necrosis_detector
        nd = create_necrosis_detector({
            "threshold_heartbeat_degraded": 600,  # 10 minutes
        })
        nd.record_heartbeat({"agent_id": "a1", "timestamp": time.time(), ...})
    """
    return NecrosisDetector(thresholds=thresholds)
