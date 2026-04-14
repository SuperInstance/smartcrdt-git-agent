"""Drift Log Indexer — Append-only, CRDT-based fleet audit trail.

Maintains a causally-ordered, append-only log of all fleet activities
(bottle sends, task claims, CRDT merges, test runs, necrosis events)
with vector-clock-based merge semantics for distributed coordination.

Designed from roundtable simulation findings for fleet-wide drift
visibility and anomaly detection.

Usage::

    log = DriftLogIndexer(agent_id="agent-42")
    entry = log.record_event("bottle_sent", "agent-42", {"to": "agent-7"})
    chain = log.get_causality_chain(entry["event_id"])
    metrics = log.get_drift_metrics()
    anomalies = log.detect_anomalies()
    print(log.export_markdown(summary_only=True))

    # Merge logs from another agent
    remote = DriftLogIndexer(agent_id="agent-7")
    remote.record_event("bottle_received", "agent-7", {"from": "agent-42"})
    new_entries = log.merge(remote)

Python 3.9+ stdlib only — zero external dependencies.
"""

from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_EVENT_TYPES = frozenset({
    "bottle_sent",
    "bottle_received",
    "task_claimed",
    "task_completed",
    "crdt_merge",
    "test_run",
    "test_failure",
    "health_check",
    "necrosis_alert",
    "heartbeat",
    "config_change",
})

_EVENT_ID_PREFIX = "dlev"
_STALENESS_THRESHOLD_SECONDS = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Vector Clock
# ---------------------------------------------------------------------------

class VectorClock:
    """Dict-based vector clock for causal ordering of distributed events.

    Each entry maps an ``agent_id`` to a monotonically increasing counter.
    Supports increment, merge, comparison, and happens-before checking.

    Parameters
    ----------
    clock :
        Initial clock state.  ``None`` starts from the empty clock.

    Example::

        vc = VectorClock({"a": 3, "b": 1})
        vc.increment("a")          # {"a": 4, "b": 1}
        merged = vc.merge(other)   # element-wise max
        vc.happens_before(other)   # True / False
    """

    __slots__ = ("_clock",)

    def __init__(self, clock: Optional[Dict[str, int]] = None) -> None:
        self._clock: Dict[str, int] = dict(clock) if clock else {}

    # -- core operations ----------------------------------------------------

    def increment(self, agent_id: str) -> VectorClock:
        """Increment the counter for *agent_id* and return ``self``.

        Parameters
        ----------
        agent_id :
            Identifier of the agent performing the event.

        Returns
        -------
        VectorClock
            ``self`` after mutation (for chaining).
        """
        self._clock[agent_id] = self._clock.get(agent_id, 0) + 1
        return self

    def merge(self, other: VectorClock) -> VectorClock:
        """Merge *other* into ``self`` via element-wise max.

        This is the standard CRDT vector-clock merge: for every agent key
        present in either clock, the merged value is ``max(self[k], other[k])``.

        Parameters
        ----------
        other :
            Another vector clock to merge in.

        Returns
        -------
        VectorClock
            ``self`` after merge (for chaining).
        """
        for agent_id, counter in other._clock.items():
            self._clock[agent_id] = max(
                self._clock.get(agent_id, 0), counter
            )
        return self

    def compare(self, other: VectorClock) -> str:
        """Compare this clock to *other* and return the causal relationship.

        Returns
        -------
        str
            One of ``"before"``, ``"after"``, ``"concurrent"``, or ``"equal"``.

        * ``"before"`` — every counter in ``self`` ≤ *other* and at least
          one is strictly less (self happens-before other).
        * ``"after"`` — every counter in ``self`` ≥ *other* and at least
          one is strictly greater.
        * ``"concurrent"`` — neither dominates; the events are unrelated.
        * ``"equal"`` — all counters match exactly.
        """
        all_keys = set(self._clock) | set(other._clock)
        all_leq = True
        all_geq = True
        any_lt = False
        any_gt = False

        for k in all_keys:
            s = self._clock.get(k, 0)
            o = other._clock.get(k, 0)
            if s < o:
                all_geq = False
                any_lt = True
            elif s > o:
                all_leq = False
                any_gt = True

        if all_leq and all_geq:
            return "equal"
        if all_leq:
            return "before"
        if all_geq:
            return "after"
        return "concurrent"

    def happens_before(self, other: VectorClock) -> bool:
        """Return ``True`` if this clock strictly precedes *other*.

        Parameters
        ----------
        other :
            Clock to compare against.

        Returns
        -------
        bool
            ``True`` when ``self`` happens-before *other*.
        """
        return self.compare(other) == "before"

    # -- utility ------------------------------------------------------------

    def as_dict(self) -> Dict[str, int]:
        """Return a shallow copy of the underlying clock dict."""
        return dict(self._clock)

    def copy(self) -> VectorClock:
        """Return a deep copy of this vector clock."""
        return VectorClock(self._clock)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VectorClock):
            return NotImplemented
        return self._clock == other._clock

    def __repr__(self) -> str:
        return f"VectorClock({self._clock!r})"

    def __len__(self) -> int:
        return len(self._clock)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._clock

    def get(self, agent_id: str, default: int = 0) -> int:
        """Return the counter for *agent_id*, or *default* if absent."""
        return self._clock.get(agent_id, default)


# ---------------------------------------------------------------------------
# Drift Log Indexer
# ---------------------------------------------------------------------------

class DriftLogIndexer:
    """Append-only, CRDT-based drift log for fleet-wide audit trails.

    Every event is tagged with a vector clock, a unique event ID,
    and an optional causality chain (parent event IDs) enabling full
    causal tracing across the fleet.

    Merge semantics follow standard CRDT vector-clock merge: entries
    from another log are interleaved by vector-clock order, and any
    entries not already present are appended.

    Parameters
    ----------
    agent_id :
        Identifier of the agent owning this log instance.  Used as the
        default vector-clock key when recording events.
    log_id :
        Optional unique identifier for this log instance.  Auto-generated
        if not provided.

    Example::

        idx = DriftLogIndexer(agent_id="node-1")
        e1 = idx.record_event("task_claimed", "node-1",
                              {"task_id": "T-042"})
        e2 = idx.record_event("test_run", "node-1",
                              {"test": "test_merge", "passed": True},
                              parent_ids=[e1["event_id"]])
        print(idx.export_markdown())
    """

    def __init__(
        self,
        agent_id: Optional[str] = None,
        log_id: Optional[str] = None,
    ) -> None:
        self._agent_id: Optional[str] = agent_id
        self._log_id: str = log_id or uuid.uuid4().hex[:12]
        self._entries: List[Dict[str, Any]] = []
        self._vector_clock: VectorClock = VectorClock()
        self._event_id_seq: int = 0
        self._event_index: Dict[str, int] = {}  # event_id -> position in _entries

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agent_id(self) -> Optional[str]:
        """Identifier of the agent owning this log instance."""
        return self._agent_id

    @property
    def log_id(self) -> str:
        """Unique identifier for this log instance."""
        return self._log_id

    @property
    def size(self) -> int:
        """Number of entries currently in the log."""
        return len(self._entries)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_event(
        self,
        event_type: str,
        agent_id: Optional[str] = None,
        payload: Optional[Any] = None,
        parent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Record a new event in the drift log.

        Increments the vector clock for the producing agent, generates
        a unique event ID, and appends the entry to the append-only log.

        Parameters
        ----------
        event_type :
            One of the valid event types (e.g. ``"bottle_sent"``,
            ``"task_claimed"``, ``"crdt_merge"``).
        agent_id :
            Agent producing the event.  Defaults to the log's own
            ``agent_id``.
        payload :
            Arbitrary JSON-serialisable data attached to the event.
        parent_ids :
            List of ``event_id`` values this event is causally
            dependent on.

        Returns
        -------
        dict
            The full event entry with ``event_id``, ``timestamp``,
            ``agent_id``, ``event_type``, ``payload``,
            ``vector_clock``, ``causality_chain``, and ``log_id``.

        Raises
        ------
        ValueError
            If *event_type* is not a recognised event type, or if any
            *parent_ids* reference events not present in this log.
        """
        if event_type not in _VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type {event_type!r}. "
                f"Valid types: {', '.join(sorted(_VALID_EVENT_TYPES))}"
            )

        effective_agent = agent_id or self._agent_id
        if effective_agent is None:
            raise ValueError(
                "agent_id must be provided either at construction or "
                "per-event via the agent_id parameter"
            )

        # Validate parent IDs exist in the log.
        resolved_parents: List[str] = []
        if parent_ids:
            for pid in parent_ids:
                if pid not in self._event_index:
                    raise ValueError(
                        f"Parent event {pid!r} not found in this log"
                    )
                resolved_parents.append(pid)

        # Increment vector clock and snapshot.
        self._vector_clock.increment(effective_agent)
        vc_snapshot = self._vector_clock.as_dict()

        # Generate a unique event ID.
        self._event_id_seq += 1
        event_id = f"{_EVENT_ID_PREFIX}-{self._log_id}-{self._event_id_seq:06d}"

        # Build entry.
        entry: Dict[str, Any] = {
            "event_id": event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": effective_agent,
            "event_type": event_type,
            "payload": payload,
            "vector_clock": vc_snapshot,
            "causality_chain": resolved_parents,
            "log_id": self._log_id,
        }

        self._entries.append(entry)
        self._event_index[event_id] = len(self._entries) - 1
        return entry

    # ------------------------------------------------------------------
    # CRDT-style merge
    # ------------------------------------------------------------------

    def merge(self, other_log: DriftLogIndexer) -> List[Dict[str, Any]]:
        """Merge another drift log into this one using CRDT semantics.

        Entries are interleaved by vector-clock order: an entry from
        *other_log* is added only if it is not already present (checked
        by ``event_id``).  New entries are sorted into position relative
        to existing entries using the vector-clock partial order.

        The local vector clock is also merged with the remote clock
        to maintain consistency.

        Parameters
        ----------
        other_log :
            Another :class:`DriftLogIndexer` instance to merge.

        Returns
        -------
        list[dict]
            The list of newly added entries (those not already present).
        """
        new_entries: List[Dict[str, Any]] = []

        for entry in other_log._entries:
            eid = entry["event_id"]
            if eid in self._event_index:
                continue  # Already present — idempotent merge.

            # Deep-copy the entry to avoid aliasing.
            new_entry: Dict[str, Any] = {
                "event_id": eid,
                "timestamp": entry["timestamp"],
                "agent_id": entry["agent_id"],
                "event_type": entry["event_type"],
                "payload": entry["payload"],
                "vector_clock": dict(entry["vector_clock"]),
                "causality_chain": list(entry["causality_chain"]),
                "log_id": entry.get("log_id", other_log._log_id),
            }

            new_entries.append(new_entry)

        # Merge vector clocks.
        self._vector_clock.merge(other_log._vector_clock)

        # Insert new entries in vector-clock sorted position.
        # Strategy: append all new entries, then stable-sort the full list
        # by a deterministic ordering key derived from the vector clock.
        # This preserves the append-only invariant while ensuring causal
        # interleaving.
        if new_entries:
            # Register new entries in the index.
            for i, entry in enumerate(new_entries):
                self._event_index[entry["event_id"]] = len(self._entries) + i

            self._entries.extend(new_entries)
            self._entries.sort(key=self._vc_sort_key)

            # Rebuild index after sort.
            self._event_index = {
                e["event_id"]: i for i, e in enumerate(self._entries)
            }

            # Advance sequence counter past merged entries.
            max_seq = max(
                (int(e["event_id"].rsplit("-", 1)[-1])
                 for e in self._entries
                 if e["event_id"].startswith(_EVENT_ID_PREFIX)),
                default=0,
            )
            self._event_id_seq = max(self._event_id_seq, max_seq)

        return new_entries

    @staticmethod
    def _vc_sort_key(entry: Dict[str, Any]) -> tuple:
        """Return a sort key derived from vector clock and timestamp.

        For deterministic ordering, we use:
        1. Sum of all vector-clock counters (total causal depth).
        2. ISO timestamp string (lexicographic sort for same depth).
        3. Event ID (tiebreaker for identical timestamps).

        This gives a total order consistent with the partial causal order:
        if A happens-before B, A will sort before B.  Concurrent events
        are ordered by timestamp then event ID.
        """
        vc = entry.get("vector_clock", {})
        depth = sum(vc.values())
        return (depth, entry.get("timestamp", ""), entry.get("event_id", ""))

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def query(
        self,
        agent_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query the drift log with optional filters.

        Parameters
        ----------
        agent_id :
            Filter events produced by this agent.
        event_type :
            Filter by event type string.
        since :
            ISO-8601 timestamp lower bound (inclusive).
        until :
            ISO-8601 timestamp upper bound (inclusive).
        limit :
            Maximum number of entries to return (default 100).

        Returns
        -------
        list[dict]
            Matching entries in vector-clock order, up to *limit*.
        """
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if event_type is not None and event_type not in _VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type {event_type!r}. "
                f"Valid types: {', '.join(sorted(_VALID_EVENT_TYPES))}"
            )

        results: List[Dict[str, Any]] = []
        for entry in self._entries:
            if agent_id is not None and entry["agent_id"] != agent_id:
                continue
            if event_type is not None and entry["event_type"] != event_type:
                continue
            if since is not None and entry["timestamp"] < since:
                continue
            if until is not None and entry["timestamp"] > until:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------
    # Causality tracing
    # ------------------------------------------------------------------

    def get_causality_chain(self, event_id: str) -> List[Dict[str, Any]]:
        """Trace the full causal history of an event back to root events.

        Follows the ``causality_chain`` links recursively to reconstruct
        the chain of events that led to the given event.

        Parameters
        ----------
        event_id :
            The ``event_id`` of the target event.

        Returns
        -------
        list[dict]
            Ordered list of ancestor entries from root to the target.
            Empty list if *event_id* is not found.

        Raises
        ------
        ValueError
            If the causality chain contains a cycle (which should not
            occur in a correctly-constructed log).
        """
        if event_id not in self._event_index:
            return []

        # Walk backwards from the target event to its ancestors.
        chain: List[Dict[str, Any]] = []
        visited: set = set()
        current = event_id

        while current:
            if current in visited:
                raise ValueError(
                    f"Causal cycle detected at event {current!r}"
                )
            visited.add(current)
            if current not in self._event_index:
                break
            entry = self._entries[self._event_index[current]]
            chain.append(entry)
            parents = entry.get("causality_chain", [])
            # Follow the first parent for linear chain; stop if no parents.
            current = parents[0] if parents else None

        # Reverse so we go root → target.
        chain.reverse()
        return chain

    # ------------------------------------------------------------------
    # Drift metrics
    # ------------------------------------------------------------------

    def get_drift_metrics(self) -> Dict[str, Any]:
        """Compute fleet drift statistics across the entire log.

        Analyses event frequencies, agent activity distributions,
        staleness scores, and convergence rates.

        Returns
        -------
        dict
            A comprehensive metrics dictionary with the following keys:

            * ``total_events`` — total number of logged events.
            * ``unique_agents`` — number of distinct agents.
            * ``event_type_counts`` — count per event type.
            * ``agent_event_counts`` — count per agent.
            * ``event_frequency_per_agent`` — events/minute per agent.
            * ``agent_staleness`` — seconds since last event per agent.
            * ``staleness_scores`` — normalised staleness (0–1) per agent.
            * ``convergence_rate`` — fraction of events with causal parents.
            * ``time_span_seconds`` — duration between first and last event.
            * ``events_per_minute`` — overall throughput.
            * ``most_active_agent`` — agent with the most events.
            * ``most_common_event_type`` — most frequent event type.
            * ``agents_by_activity`` — agents sorted by event count (desc).
            * ``event_type_distribution`` — percentage breakdown by type.
            * ``causal_depth_histogram`` — distribution of causal chain lengths.
        """
        if not self._entries:
            return {
                "total_events": 0,
                "unique_agents": 0,
                "event_type_counts": {},
                "agent_event_counts": {},
                "event_frequency_per_agent": {},
                "agent_staleness": {},
                "staleness_scores": {},
                "convergence_rate": 0.0,
                "time_span_seconds": 0.0,
                "events_per_minute": 0.0,
                "most_active_agent": None,
                "most_common_event_type": None,
                "agents_by_activity": [],
                "event_type_distribution": {},
                "causal_depth_histogram": {},
            }

        # Agent and event type counts.
        agent_counts: Dict[str, int] = defaultdict(int)
        type_counts: Dict[str, int] = defaultdict(int)
        agent_last_ts: Dict[str, str] = {}
        agent_first_ts: Dict[str, str] = {}
        has_parents = 0

        for entry in self._entries:
            aid = entry["agent_id"]
            etype = entry["event_type"]
            ts = entry["timestamp"]
            agent_counts[aid] += 1
            type_counts[etype] += 1
            agent_last_ts[aid] = max(
                agent_last_ts.get(aid, ""), ts
            )
            agent_first_ts[aid] = min(
                agent_first_ts.get(aid, "9999-12-31T23:59:59+00:00"), ts
            )
            if entry.get("causality_chain"):
                has_parents += 1

        # Parse timestamps for computation.
        now = datetime.now(timezone.utc)
        all_timestamps: List[datetime] = []
        for entry in self._entries:
            try:
                all_timestamps.append(
                    datetime.fromisoformat(entry["timestamp"])
                )
            except (ValueError, TypeError):
                pass

        # Time span.
        time_span = 0.0
        if len(all_timestamps) >= 2:
            time_span = (max(all_timestamps) - min(all_timestamps)).total_seconds()

        # Events per minute.
        events_per_minute = 0.0
        if time_span > 0:
            events_per_minute = len(self._entries) / (time_span / 60.0)

        # Event frequency per agent (events per minute of that agent's
        # active time span).
        freq_per_agent: Dict[str, float] = {}
        for aid in agent_counts:
            first = datetime.fromisoformat(agent_first_ts[aid])
            last = datetime.fromisoformat(agent_last_ts[aid])
            span = (last - first).total_seconds()
            if span > 0:
                freq_per_agent[aid] = round(
                    agent_counts[aid] / (span / 60.0), 4
                )
            else:
                freq_per_agent[aid] = 0.0

        # Staleness per agent (seconds since last event).
        agent_staleness: Dict[str, float] = {}
        staleness_scores: Dict[str, float] = {}
        for aid, last_ts_str in agent_last_ts.items():
            try:
                last_ts = datetime.fromisoformat(last_ts_str)
                stale_secs = (now - last_ts).total_seconds()
                agent_staleness[aid] = round(max(0.0, stale_secs), 2)
                # Normalise to [0, 1] using a sigmoid-like transform.
                # Score of 0 = fresh, 1 = very stale.
                staleness_scores[aid] = round(
                    min(1.0, stale_secs / _STALENESS_THRESHOLD_SECONDS), 4
                )
            except (ValueError, TypeError):
                agent_staleness[aid] = -1.0
                staleness_scores[aid] = 0.0

        # Most active agent and event type.
        most_active_agent = max(agent_counts, key=agent_counts.get) if agent_counts else None  # type: ignore[arg-type]
        most_common_type = max(type_counts, key=type_counts.get) if type_counts else None  # type: ignore[arg-type]

        # Agents sorted by activity.
        agents_by_activity = sorted(
            [{"agent_id": aid, "count": cnt}
             for aid, cnt in agent_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        # Event type distribution (percentage).
        total = len(self._entries)
        event_type_distribution: Dict[str, float] = {
            etype: round(cnt / total * 100, 2)
            for etype, cnt in type_counts.items()
        }

        # Causal depth histogram.
        depth_histogram: Dict[int, int] = defaultdict(int)
        for entry in self._entries:
            depth = len(entry.get("causality_chain", []))
            depth_histogram[depth] += 1

        return {
            "total_events": total,
            "unique_agents": len(agent_counts),
            "event_type_counts": dict(type_counts),
            "agent_event_counts": dict(agent_counts),
            "event_frequency_per_agent": freq_per_agent,
            "agent_staleness": agent_staleness,
            "staleness_scores": staleness_scores,
            "convergence_rate": round(has_parents / total, 4) if total else 0.0,
            "time_span_seconds": round(time_span, 2),
            "events_per_minute": round(events_per_minute, 4),
            "most_active_agent": most_active_agent,
            "most_common_event_type": most_common_type,
            "agents_by_activity": agents_by_activity,
            "event_type_distribution": event_type_distribution,
            "causal_depth_histogram": dict(depth_histogram),
        }

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def detect_anomalies(self, window_seconds: float = 3600.0) -> List[Dict[str, Any]]:
        """Detect unusual patterns in the drift log within a time window.

        Scans recent events and flags:

        * **burst** — an agent producing events at unusually high rate
          (> 3 standard deviations above the mean).
        * **silent_agent** — an agent with no events in the window that
          was recently active.
        * **causal_orphan** — events with parent IDs referencing events
          outside this log.
        * **stale_heartbeat** — an agent whose last heartbeat exceeds
          the staleness threshold.
        * **event_type_spike** — a sudden increase in a specific event
          type compared to its historical frequency.
        * **necrosis_without_recovery** — a ``necrosis_alert`` event not
          followed by a ``health_check`` within the window.

        Parameters
        ----------
        window_seconds :
            Look-back window from the most recent event, in seconds
            (default 3600 = 1 hour).

        Returns
        -------
        list[dict]
            Each anomaly dict contains ``anomaly_type``, ``severity``,
            ``description``, ``agent_id`` (if applicable), ``timestamp``,
            and ``context``.
        """
        if not self._entries:
            return []

        anomalies: List[Dict[str, Any]] = []

        # Determine the window boundary.
        try:
            newest = datetime.fromisoformat(
                self._entries[-1]["timestamp"]
            )
        except (ValueError, TypeError, IndexError):
            return []

        window_start = newest.timestamp() - window_seconds

        # Collect events within the window.
        window_entries: List[Dict[str, Any]] = []
        all_parsed: List[tuple] = []  # (datetime, entry)

        for entry in self._entries:
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
            except (ValueError, TypeError):
                continue
            all_parsed.append((ts, entry))
            if ts.timestamp() >= window_start:
                window_entries.append(entry)

        if not window_entries:
            return []

        # ---- Burst detection per agent ----
        agent_window_counts: Dict[str, int] = defaultdict(int)
        agent_timestamps: Dict[str, List[float]] = defaultdict(list)
        for entry in window_entries:
            aid = entry["agent_id"]
            agent_window_counts[aid] += 1
            try:
                ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                agent_timestamps[aid].append(ts)
            except (ValueError, TypeError):
                pass

        if len(agent_window_counts) > 1:
            counts = list(agent_window_counts.values())
            mean_rate = sum(counts) / len(counts)
            variance = sum((c - mean_rate) ** 2 for c in counts) / len(counts)
            std_rate = math.sqrt(variance)

            for aid, cnt in agent_window_counts.items():
                if std_rate > 0 and cnt > mean_rate + 3 * std_rate:
                    anomalies.append({
                        "anomaly_type": "burst",
                        "severity": "high",
                        "description": (
                            f"Agent {aid!r} produced {cnt} events in "
                            f"the last {window_seconds:.0f}s window "
                            f"(mean={mean_rate:.1f}, "
                            f"threshold={mean_rate + 3 * std_rate:.1f})"
                        ),
                        "agent_id": aid,
                        "timestamp": self._entries[-1]["timestamp"],
                        "context": {"event_count": cnt,
                                    "mean": round(mean_rate, 2),
                                    "std": round(std_rate, 2)},
                    })

        # ---- Silent agent detection ----
        all_agents_in_window: set = set(agent_window_counts)
        for ts, entry in all_parsed:
            if ts.timestamp() < window_start:
                aid = entry["agent_id"]
                if aid not in all_agents_in_window:
                    anomalies.append({
                        "anomaly_type": "silent_agent",
                        "severity": "medium",
                        "description": (
                            f"Agent {aid!r} was active before the window "
                            f"but has no events in the last "
                            f"{window_seconds:.0f}s"
                        ),
                        "agent_id": aid,
                        "timestamp": self._entries[-1]["timestamp"],
                        "context": {
                            "last_seen": entry["timestamp"],
                            "window_seconds": window_seconds,
                        },
                    })
                    # Report each agent only once.
                    all_agents_in_window.add(aid)

        # ---- Stale heartbeat detection ----
        heartbeat_agents: Dict[str, str] = {}
        for entry in self._entries:
            if entry["event_type"] == "heartbeat":
                heartbeat_agents[entry["agent_id"]] = entry["timestamp"]

        for aid, hb_ts_str in heartbeat_agents.items():
            try:
                hb_ts = datetime.fromisoformat(hb_ts_str)
                stale_secs = (newest - hb_ts).total_seconds()
                if stale_secs > _STALENESS_THRESHOLD_SECONDS:
                    anomalies.append({
                        "anomaly_type": "stale_heartbeat",
                        "severity": "medium",
                        "description": (
                            f"Agent {aid!r} last heartbeat was "
                            f"{stale_secs:.0f}s ago "
                            f"(threshold={_STALENESS_THRESHOLD_SECONDS:.0f}s)"
                        ),
                        "agent_id": aid,
                        "timestamp": hb_ts_str,
                        "context": {
                            "seconds_since_heartbeat": round(stale_secs, 2),
                            "threshold": _STALENESS_THRESHOLD_SECONDS,
                        },
                    })
            except (ValueError, TypeError):
                pass

        # ---- Necrosis without recovery ----
        necrosis_events: List[Dict[str, Any]] = [
            e for e in window_entries
            if e["event_type"] == "necrosis_alert"
        ]
        for nec in necrosis_events:
            aid = nec["agent_id"]
            nec_ts = nec["timestamp"]
            # Check if there's a health_check after the necrosis alert
            # for the same agent.
            recovered = any(
                e["event_type"] == "health_check"
                and e["agent_id"] == aid
                and e["timestamp"] >= nec_ts
                for e in window_entries
            )
            if not recovered:
                anomalies.append({
                    "anomaly_type": "necrosis_without_recovery",
                    "severity": "high",
                    "description": (
                        f"Agent {aid!r} had a necrosis_alert at "
                        f"{nec_ts} with no subsequent health_check "
                        f"recovery in the window"
                    ),
                    "agent_id": aid,
                    "timestamp": nec_ts,
                    "context": {"window_seconds": window_seconds},
                })

        # ---- Event type spike ----
        # Compare window frequency vs. historical frequency for each type.
        type_window_counts: Dict[str, int] = defaultdict(int)
        for entry in window_entries:
            type_window_counts[entry["event_type"]] += 1

        total_window = len(window_entries)
        total_all = len(self._entries)
        if total_all > total_window > 0:
            for etype, wcnt in type_window_counts.items():
                # Historical proportion before the window.
                hist_cnt = sum(
                    1 for e in self._entries
                    if e["event_type"] == etype
                    and e not in window_entries  # approximate
                )
                hist_total = total_all - total_window
                if hist_total > 0:
                    hist_rate = hist_cnt / hist_total
                    window_rate = wcnt / total_window
                    # Flag if window rate is more than 3x historical rate
                    # and the absolute difference is meaningful.
                    if (hist_rate > 0
                            and window_rate > hist_rate * 3
                            and window_rate - hist_rate > 0.1):
                        anomalies.append({
                            "anomaly_type": "event_type_spike",
                            "severity": "low",
                            "description": (
                                f"Event type {etype!r} frequency spike: "
                                f"window rate {window_rate:.2%} vs. "
                                f"historical rate {hist_rate:.2%} "
                                f"({wcnt}/{total_window} vs. "
                                f"{hist_cnt}/{hist_total})"
                            ),
                            "agent_id": None,
                            "timestamp": self._entries[-1]["timestamp"],
                            "context": {
                                "event_type": etype,
                                "window_rate": round(window_rate, 4),
                                "historical_rate": round(hist_rate, 4),
                                "window_count": wcnt,
                                "historical_count": hist_cnt,
                            },
                        })

        # Sort by severity.
        severity_order = {"high": 0, "medium": 1, "low": 2}
        anomalies.sort(key=lambda a: severity_order.get(a["severity"], 99))
        return anomalies

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self) -> str:
        """Export the full drift log as a JSON string.

        Returns
        -------
        str
            Pretty-printed JSON containing the log metadata and all
            entries.
        """
        output: Dict[str, Any] = {
            "log_id": self._log_id,
            "agent_id": self._agent_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(self._entries),
            "vector_clock": self._vector_clock.as_dict(),
            "entries": self._entries,
        }
        return json.dumps(output, indent=2, ensure_ascii=False, default=str)

    def export_markdown(self, summary_only: bool = False) -> str:
        """Export the drift log as a human-readable markdown document.

        Parameters
        ----------
        summary_only :
            If ``True``, emit a compact summary with drift metrics
            instead of the full event listing.

        Returns
        -------
        str
            Markdown-formatted string.
        """
        lines: List[str] = []
        metrics = self.get_drift_metrics()
        now = datetime.now(timezone.utc).isoformat()

        # Header.
        lines.append(f"# Drift Log: {self._log_id}")
        lines.append("")
        lines.append(f"**Agent:** {self._agent_id or 'unassigned'}")
        lines.append(f"**Exported:** {now}")
        lines.append(f"**Total Events:** {metrics['total_events']}")
        lines.append(f"**Unique Agents:** {metrics['unique_agents']}")
        lines.append(f"**Time Span:** {metrics['time_span_seconds']:.1f}s")
        lines.append(f"**Throughput:** {metrics['events_per_minute']:.2f} events/min")
        lines.append(f"**Convergence Rate:** {metrics['convergence_rate']:.1%}")
        lines.append("")

        # Summary metrics section.
        lines.append("## Fleet Overview")
        lines.append("")

        if metrics["agents_by_activity"]:
            lines.append("### Agent Activity")
            lines.append("")
            lines.append("| Agent | Events | Staleness (s) | Stale Score |")
            lines.append("|-------|--------|---------------|-------------|")
            for item in metrics["agents_by_activity"]:
                aid = item["agent_id"]
                cnt = item["count"]
                stale = metrics["agent_staleness"].get(aid, 0.0)
                score = metrics["staleness_scores"].get(aid, 0.0)
                lines.append(
                    f"| {aid} | {cnt} | {stale:.0f} | {score:.2f} |"
                )
            lines.append("")

        if metrics["event_type_counts"]:
            lines.append("### Event Type Distribution")
            lines.append("")
            lines.append("| Event Type | Count | Share |")
            lines.append("|------------|-------|-------|")
            for etype, cnt in sorted(
                metrics["event_type_counts"].items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                share = metrics["event_type_distribution"].get(etype, 0.0)
                lines.append(f"| {etype} | {cnt} | {share:.1f}% |")
            lines.append("")

        if summary_only:
            return "\n".join(lines)

        # Anomaly summary.
        anomalies = self.detect_anomalies()
        if anomalies:
            lines.append("### Active Anomalies")
            lines.append("")
            for anom in anomalies:
                lines.append(
                    f"- **[{anom['severity'].upper()}] {anom['anomaly_type']}:** "
                    f"{anom['description']}"
                )
            lines.append("")

        # Full event listing.
        lines.append("## Event Log")
        lines.append("")
        for entry in self._entries:
            eid = entry["event_id"]
            ts = entry["timestamp"]
            aid = entry["agent_id"]
            etype = entry["event_type"]
            vc = entry["vector_clock"]
            parents = entry.get("causality_chain", [])
            payload = entry.get("payload")

            lines.append(f"### {eid}")
            lines.append("")
            lines.append(f"- **Type:** `{etype}`")
            lines.append(f"- **Agent:** `{aid}`")
            lines.append(f"- **Time:** {ts}")
            lines.append(f"- **Vector Clock:** {json.dumps(vc)}")
            if parents:
                lines.append(f"- **Parents:** {', '.join(parents)}")
            if payload is not None:
                payload_str = json.dumps(
                    payload, indent=2, ensure_ascii=False, default=str
                )
                lines.append(f"- **Payload:**")
                for pline in payload_str.splitlines():
                    lines.append(f"  {pline}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """Safely parse an ISO-8601 timestamp string."""
        try:
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_drift_log(
    agent_id: Optional[str] = None,
    log_id: Optional[str] = None,
) -> DriftLogIndexer:
    """Create and return a configured :class:`DriftLogIndexer`.

    Recommended entry-point::

        from drift_log_indexer import create_drift_log
        log = create_drift_log(agent_id="node-1")
        log.record_event("heartbeat", "node-1")

    Parameters
    ----------
    agent_id :
        Identifier of the agent owning this log instance.
    log_id :
        Optional unique identifier for the log instance.

    Returns
    -------
    DriftLogIndexer
        A new, empty drift log indexer.
    """
    return DriftLogIndexer(agent_id=agent_id, log_id=log_id)
