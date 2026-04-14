"""Tidepool Oracle — Emergent Consensus Engine via CRDT Dream Journals.

Each agent in the fleet maintains a *dream journal*: an append-only CRDT log of
hypothetical actions and predicted outcomes.  Periodically these journals are
merged using vector-clock semantics, and the most persistent patterns — the
dreams that survive the merge — guide real decisions.

Instead of traditional voting, agents **dream** alternative decision paths.
Consensus is *emergent*: it crystallises from the overlapping patterns that
survive CRDT merge, not from explicit ballots.

Core concepts
-------------
- **Dream** — a single hypothetical entry (scenario → action → predicted
  outcome) recorded by one agent.
- **Dream Journal** — an append-only CRDT log owned by one agent or the
  collective fleet.  Merge is commutative, associative, and idempotent.
- **Tidepool Oracle** — the main interface that coordinates journals, runs
  simulations, extracts patterns, and produces guidance.

Usage::

    oracle = create_tidepool_oracle()
    oracle.record_dream(
        agent_id="agent-7",
        scenario="merge-conflict in crdt-coordinator",
        action="apply three-way merge with vector-clock tiebreak",
        predicted_outcome="clean convergence in 2 rounds",
        confidence=0.82,
        tags=["merge-strategy", "crdt"],
    )
    guidance = oracle.consult("merge-conflict in crdt-coordinator")
    print(guidance["recommendation"]["action"])

The file requires **Python 3.9+** and the standard library only — zero
external dependencies.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

#: Common English stop-words used for normalising dream text during
#: pattern extraction.
STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "dare", "ought", "used", "it", "its", "this", "that", "these", "those",
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you",
    "your", "yours", "yourself", "yourselves", "he", "him", "his",
    "himself", "she", "her", "hers", "herself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "when",
    "where", "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "because", "if", "then",
    "else", "about", "up", "out", "into", "through", "during", "before",
    "after", "above", "below", "between", "under", "again", "further",
    "once", "here", "there", "while", "also", "any", "until", "against",
})

#: Deterministic action templates used during simulation rounds when no
#: real agent input is available.
_ACTION_TEMPLATES: List[str] = [
    "apply three-way merge with automatic resolution",
    "escalate to human review for manual resolution",
    "retry with exponential backoff",
    "split the operation into smaller atomic steps",
    "use last-writer-wins with timestamp tiebreak",
    "defer decision until next sync cycle",
    "initiate consensus poll among peers",
    "apply conflict-free replicated data type merge",
    "create a new branch to isolate the change",
    "revert to last known-good state and retry",
    "run integration tests before proceeding",
    "merge with vector-clock ordering",
]

#: Deterministic outcome templates for simulation rounds.
_OUTCOME_TEMPLATES: List[str] = [
    "convergence achieved within acceptable latency",
    "partial merge success — some conflicts remain",
    "operation completed without side effects",
    "significant performance improvement observed",
    "requires additional peer coordination",
    "data integrity maintained across all replicas",
    "new conflict detected — further action needed",
    "clean resolution with no data loss",
]

#: Default configuration for the oracle.
_DEFAULT_CONFIG: Dict[str, Any] = {
    "min_confidence": 0.5,
    "consensus_weight_support": 1.0,
    "consensus_weight_confidence": 1.0,
    "pattern_min_occurrences": 3,
    "density_window_seconds": 3600,
    "anomaly_confidence_gap": 0.3,
    "vivid_dream_limit": 10,
    "top_scenarios_limit": 10,
    "simulation_seed_modifier": 0,
}


# ────────────────────────────────────────────────────────────────────
# Dream Entry
# ────────────────────────────────────────────────────────────────────

@dataclass
class DreamEntry:
    """A single hypothetical decision recorded by an agent.

    Each dream represents one agent's contemplation of a scenario: what they
    would do, what they predict will happen, and how confident they are.

    Attributes:
        dream_id: Unique identifier, formatted ``"drm-<uuid4>"``.
        agent_id: Identifier of the agent that originated this dream.
        timestamp: Unix epoch seconds when the dream was recorded.
        scenario: Free-text description of the hypothetical situation.
        action: The proposed course of action.
        predicted_outcome: What the agent believes will happen.
        confidence: Agent's confidence in the prediction (0.0 – 1.0).
        vector_clock: Causal ordering metadata (agent_id → counter).
        tags: Categorical labels (e.g. ``"merge-strategy"``, ``"testing"``).
        parent_dream_ids: Dreams that causally inspired this one.
    """

    dream_id: str
    agent_id: str
    timestamp: float
    scenario: str
    action: str
    predicted_outcome: str
    confidence: float
    vector_clock: Dict[str, int] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    parent_dream_ids: List[str] = field(default_factory=list)

    # ── helpers ────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate fields after construction."""
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the dream to a plain dictionary."""
        return {
            "dream_id": self.dream_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "scenario": self.scenario,
            "action": self.action,
            "predicted_outcome": self.predicted_outcome,
            "confidence": self.confidence,
            "vector_clock": dict(self.vector_clock),
            "tags": list(self.tags),
            "parent_dream_ids": list(self.parent_dream_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DreamEntry:
        """Deserialise a dream from a plain dictionary."""
        return cls(
            dream_id=data["dream_id"],
            agent_id=data["agent_id"],
            timestamp=data["timestamp"],
            scenario=data["scenario"],
            action=data["action"],
            predicted_outcome=data["predicted_outcome"],
            confidence=data["confidence"],
            vector_clock=dict(data.get("vector_clock", {})),
            tags=list(data.get("tags", [])),
            parent_dream_ids=list(data.get("parent_dream_ids", [])),
        )


# ────────────────────────────────────────────────────────────────────
# Dream Journal (Append-Only CRDT)
# ────────────────────────────────────────────────────────────────────

class DreamJournal:
    """Append-only CRDT journal of :class:`DreamEntry` records.

    Merge semantics follow vector-clock causality:

    * **Happens-before** — if entry *a*'s clock dominates *b*'s clock, *a*
      precedes *b*.
    * **Concurrent** — if neither clock dominates the other, both entries
      are retained (union merge).
    * **Idempotent** — entries with identical ``dream_id`` are deduplicated.

    The journal is commutative (merge order does not matter), associative
    (merges can be grouped arbitrarily), and idempotent (merging twice
    yields the same result as merging once).

    Example::

        j1 = DreamJournal("agent-a")
        j2 = DreamJournal("agent-b")
        j1.append(dream_a1)
        j2.append(dream_b1)
        j1.merge(j2)          # now contains both dreams
    """

    def __init__(self, owner_id: str) -> None:
        """Initialise an empty journal.

        Args:
            owner_id: Identifier of the agent (or ``"fleet"``) that owns
                this journal.
        """
        self._owner_id: str = owner_id
        self._entries: List[DreamEntry] = []
        self._entry_index: Dict[str, int] = {}  # dream_id → position
        self._vector_clock: Dict[str, int] = {owner_id: 0}

    # ── properties ─────────────────────────────────────────────────

    @property
    def owner_id(self) -> str:
        """Identifier of the journal's owner."""
        return self._owner_id

    @property
    def vector_clock(self) -> Dict[str, int]:
        """Current merged vector clock snapshot (read-only copy)."""
        return dict(self._vector_clock)

    # ── core operations ────────────────────────────────────────────

    def append(self, dream: DreamEntry) -> None:
        """Add a dream to the journal.

        Increments the journal's vector clock for the dream's agent and
        merges the dream's causal clock.  Duplicate ``dream_id`` values
        are silently ignored (idempotent append).

        Args:
            dream: The dream entry to record.
        """
        if dream.dream_id in self._entry_index:
            return  # idempotent — already present

        # Merge the incoming vector clock into the journal clock.
        self._merge_clock(dream.vector_clock)
        # Increment this journal's owner counter.
        self._vector_clock[self._owner_id] = (
            self._vector_clock.get(self._owner_id, 0) + 1
        )
        self._entries.append(dream)
        self._entry_index[dream.dream_id] = len(self._entries) - 1

    def merge(self, other: DreamJournal) -> Dict[str, Any]:
        """CRDT-merge another journal into this one.

        Entries are interleaved by vector-clock partial ordering.  Concurrent
        entries (neither dominates the other) are both retained.  The merge
        is commutative, associative, and idempotent.

        Args:
            other: The journal to merge in.

        Returns:
            A summary dict with ``entries_added``, ``entries_existing``,
            ``merged_clock``, ``total_size``.
        """
        added = 0
        existing = 0

        for entry in other._entries:
            if entry.dream_id in self._entry_index:
                existing += 1
            else:
                self._entries.append(entry)
                self._entry_index[entry.dream_id] = len(self._entries) - 1
                added += 1

        # Merge the other journal's clock.
        self._merge_clock(other._vector_clock)

        # Re-sort entries by vector-clock order (stable sort preserves
        # insertion order for truly concurrent entries).
        self._entries.sort(key=self._entry_sort_key)
        # Rebuild index after sort.
        self._entry_index = {
            e.dream_id: i for i, e in enumerate(self._entries)
        }

        return {
            "entries_added": added,
            "entries_existing": existing,
            "merged_clock": dict(self._vector_clock),
            "total_size": len(self._entries),
        }

    # ── consensus & analysis ───────────────────────────────────────

    def get_consensus(
        self,
        scenario_prefix: Optional[str] = None,
        min_confidence: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Find the most agreed-upon actions for a scenario.

        For every matching dream the actions are grouped and ranked by a
        *consensus score* — ``support_count × avg_confidence`` — giving
        higher weight to actions proposed by many agents with high
        confidence.

        Args:
            scenario_prefix: If given, only dreams whose scenario starts
                with this prefix are considered.
            min_confidence: Minimum confidence threshold.

        Returns:
            A list of consensus descriptors sorted by descending
            ``consensus_score``.  Each element contains ``action``,
            ``support_count``, ``avg_confidence``, ``consensus_score``,
            ``diversity``, ``agents``, ``sample_outcomes``.
        """
        dreams = self._filter(scenario_prefix, min_confidence)
        action_groups: Dict[str, List[DreamEntry]] = defaultdict(list)
        for d in dreams:
            action_groups[d.action].append(d)

        results: List[Dict[str, Any]] = []
        for action, entries in action_groups.items():
            agents = list({e.agent_id for e in entries})
            confidences = [e.confidence for e in entries]
            outcomes = list({e.predicted_outcome for e in entries})
            support = len(entries)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            results.append({
                "action": action,
                "support_count": support,
                "avg_confidence": round(avg_conf, 4),
                "consensus_score": round(support * avg_conf, 4),
                "diversity": len(agents),
                "agents": agents,
                "sample_outcomes": outcomes[:5],
            })

        results.sort(key=lambda r: r["consensus_score"], reverse=True)
        return results

    def get_vivid_dreams(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the highest-confidence dreams (the most vivid).

        Args:
            limit: Maximum number of dreams to return.

        Returns:
            Dreams sorted by descending confidence, each serialised as a
            dict.
        """
        sorted_entries = sorted(
            self._entries, key=lambda e: e.confidence, reverse=True
        )
        return [e.to_dict() for e in sorted_entries[:limit]]

    def get_dream_density(self, window_seconds: float = 3600) -> Dict[str, Any]:
        """Compute how many dreams fall within a recent time window.

        Args:
            window_seconds: Width of the rolling window in seconds.

        Returns:
            A dict with ``count``, ``window_seconds``, ``window_start``,
            ``window_end``, ``dreams_per_minute``, ``agent_breakdown``.
        """
        now = time.time()
        window_start = now - window_seconds
        window_dreams = [
            e for e in self._entries if e.timestamp >= window_start
        ]
        agent_counts: Counter = Counter(e.agent_id for e in window_dreams)
        minutes = window_seconds / 60.0
        return {
            "count": len(window_dreams),
            "window_seconds": window_seconds,
            "window_start": window_start,
            "window_end": now,
            "dreams_per_minute": round(len(window_dreams) / minutes, 2)
            if minutes > 0 else 0.0,
            "agent_breakdown": dict(agent_counts),
        }

    def extract_patterns(
        self, min_occurrences: int = 3
    ) -> List[Dict[str, Any]]:
        """Find recurring keyword patterns across all dreams.

        Dreams are tokenised (lowercased, stop-words removed) and their
        keyword co-occurrences are counted.  Patterns that exceed
        *min_occurrences* are returned.

        Args:
            min_occurrences: Minimum frequency threshold.

        Returns:
            A list of pattern descriptors sorted by descending frequency.
            Each has ``keywords``, ``frequency``, ``agents_contributing``,
            ``avg_confidence``, ``sample_dream_ids``.
        """
        pattern_map: Dict[
            Tuple[str, ...], List[DreamEntry]
        ] = defaultdict(list)

        for entry in self._entries:
            s_tokens = self._tokenise(entry.scenario)
            a_tokens = self._tokenise(entry.action)
            o_tokens = self._tokenise(entry.predicted_outcome)
            if not s_tokens and not a_tokens and not o_tokens:
                continue
            # Build a sorted keyword tuple for stable grouping.
            all_kw = sorted(set(s_tokens + a_tokens + o_tokens))
            pattern_map[tuple(all_kw)].append(entry)

        results: List[Dict[str, Any]] = []
        for keywords, entries in pattern_map.items():
            if len(entries) < min_occurrences:
                continue
            agents = list({e.agent_id for e in entries})
            confs = [e.confidence for e in entries]
            avg_c = sum(confs) / len(confs) if confs else 0.0
            results.append({
                "keywords": list(keywords),
                "frequency": len(entries),
                "agents_contributing": agents,
                "avg_confidence": round(avg_c, 4),
                "sample_dream_ids": [e.dream_id for e in entries[:5]],
            })

        results.sort(key=lambda r: r["frequency"], reverse=True)
        return results

    def get_scenario_summary(
        self, scenario_prefix: str
    ) -> Dict[str, Any]:
        """Aggregate all predictions for dreams matching *scenario_prefix*.

        Args:
            scenario_prefix: Prefix filter on the scenario field.

        Returns:
            A dict with ``scenario_prefix``, ``total_dreams``,
            ``unique_agents``, ``actions`` (list of action summaries),
            ``outcomes`` (list of predicted-outcome summaries), and
            ``avg_confidence``.
        """
        dreams = [
            e for e in self._entries
            if e.scenario.startswith(scenario_prefix)
        ]
        if not dreams:
            return {
                "scenario_prefix": scenario_prefix,
                "total_dreams": 0,
                "unique_agents": [],
                "actions": [],
                "outcomes": [],
                "avg_confidence": 0.0,
            }

        agents = list({e.agent_id for e in dreams})
        confs = [e.confidence for e in dreams]

        action_summary: Dict[str, Dict[str, Any]] = {}
        outcome_summary: Dict[str, Dict[str, Any]] = {}
        for d in dreams:
            if d.action not in action_summary:
                action_summary[d.action] = {
                    "count": 0, "total_confidence": 0.0, "agents": set(),
                }
            action_summary[d.action]["count"] += 1
            action_summary[d.action]["total_confidence"] += d.confidence
            action_summary[d.action]["agents"].add(d.agent_id)

            if d.predicted_outcome not in outcome_summary:
                outcome_summary[d.predicted_outcome] = {
                    "count": 0, "total_confidence": 0.0, "agents": set(),
                }
            outcome_summary[d.predicted_outcome]["count"] += 1
            outcome_summary[d.predicted_outcome]["total_confidence"] += d.confidence
            outcome_summary[d.predicted_outcome]["agents"].add(d.agent_id)

        action_list = [
            {
                "action": a,
                "count": v["count"],
                "avg_confidence": round(
                    v["total_confidence"] / v["count"], 4
                ),
                "agents": list(v["agents"]),
            }
            for a, v in sorted(
                action_summary.items(), key=lambda x: x[1]["count"],
                reverse=True,
            )
        ]
        outcome_list = [
            {
                "outcome": o,
                "count": v["count"],
                "avg_confidence": round(
                    v["total_confidence"] / v["count"], 4
                ),
                "agents": list(v["agents"]),
            }
            for o, v in sorted(
                outcome_summary.items(), key=lambda x: x[1]["count"],
                reverse=True,
            )
        ]

        return {
            "scenario_prefix": scenario_prefix,
            "total_dreams": len(dreams),
            "unique_agents": agents,
            "actions": action_list,
            "outcomes": outcome_list,
            "avg_confidence": round(
                sum(confs) / len(confs), 4
            ) if confs else 0.0,
        }

    def get_fleet_imagination(self) -> Dict[str, Any]:
        """Produce a statistical profile of the fleet's collective thinking.

        Returns:
            A dict with ``total_dreams``, ``unique_agents``,
            ``unique_scenarios``, ``avg_confidence``, ``confidence_stddev``,
            ``most_active_agents``, ``most_discussed_scenarios``,
            ``tag_distribution``, ``dream_ids_by_tag``.
        """
        if not self._entries:
            return {
                "total_dreams": 0,
                "unique_agents": 0,
                "unique_scenarios": 0,
                "avg_confidence": 0.0,
                "confidence_stddev": 0.0,
                "most_active_agents": [],
                "most_discussed_scenarios": [],
                "tag_distribution": {},
                "dream_ids_by_tag": {},
            }

        agents: Counter = Counter(e.agent_id for e in self._entries)
        scenarios: Counter = Counter(e.scenario for e in self._entries)
        confs = [e.confidence for e in self._entries]
        avg_conf = sum(confs) / len(confs)
        variance = (
            sum((c - avg_conf) ** 2 for c in confs) / len(confs)
        )
        stddev = math.sqrt(variance)

        tag_map: Dict[str, List[str]] = defaultdict(list)
        for e in self._entries:
            for t in e.tags:
                tag_map[t].append(e.dream_id)

        return {
            "total_dreams": len(self._entries),
            "unique_agents": len(agents),
            "unique_scenarios": len(scenarios),
            "avg_confidence": round(avg_conf, 4),
            "confidence_stddev": round(stddev, 4),
            "most_active_agents": agents.most_common(10),
            "most_discussed_scenarios": scenarios.most_common(10),
            "tag_distribution": {
                k: len(v) for k, v in tag_map.items()
            },
            "dream_ids_by_tag": dict(tag_map),
        }

    # ── export ─────────────────────────────────────────────────────

    def export_markdown(self) -> str:
        """Render the full journal as human-readable Markdown.

        Returns:
            A multi-line Markdown string suitable for display or
            inclusion in reports.
        """
        lines: List[str] = []
        lines.append(f"# Dream Journal — `{self._owner_id}`")
        lines.append("")
        lines.append(
            f"**Total dreams:** {len(self._entries)}  |  "
            f"**Vector clock:** {self._vector_clock}"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        if not self._entries:
            lines.append("*No dreams recorded yet.*")
            return "\n".join(lines)

        for i, entry in enumerate(self._entries, 1):
            lines.append(f"## Dream {i}: `{entry.dream_id}`")
            lines.append("")
            lines.append(f"- **Agent:** `{entry.agent_id}`")
            lines.append(
                f"- **Time:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(entry.timestamp))}"
            )
            lines.append(f"- **Confidence:** {entry.confidence:.2%}")
            lines.append("")
            lines.append(f"### Scenario")
            lines.append(entry.scenario)
            lines.append("")
            lines.append(f"### Action")
            lines.append(entry.action)
            lines.append("")
            lines.append(f"### Predicted Outcome")
            lines.append(entry.predicted_outcome)
            lines.append("")
            if entry.tags:
                lines.append(
                    f"**Tags:** {', '.join(f'`{t}`' for t in entry.tags)}"
                )
            if entry.parent_dream_ids:
                lines.append(
                    "**Inspired by:** "
                    + ", ".join(f"`{pid}`" for pid in entry.parent_dream_ids)
                )
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    # ── utility ────────────────────────────────────────────────────

    def size(self) -> int:
        """Return the number of dreams in the journal."""
        return len(self._entries)

    def get_all_entries(self) -> List[DreamEntry]:
        """Return a shallow copy of all entries (for iteration)."""
        return list(self._entries)

    def get_entry(self, dream_id: str) -> Optional[DreamEntry]:
        """Look up a single dream by its ID.

        Returns:
            The matching :class:`DreamEntry`, or ``None`` if not found.
        """
        idx = self._entry_index.get(dream_id)
        if idx is not None:
            return self._entries[idx]
        return None

    # ── serialisation ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the journal to a plain dictionary."""
        return {
            "owner_id": self._owner_id,
            "vector_clock": dict(self._vector_clock),
            "entries": [e.to_dict() for e in self._entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DreamJournal:
        """Deserialise a journal from a plain dictionary."""
        journal = cls(data["owner_id"])
        journal._vector_clock = dict(data.get("vector_clock", {}))
        for ed in data.get("entries", []):
            entry = DreamEntry.from_dict(ed)
            journal._entries.append(entry)
            journal._entry_index[entry.dream_id] = (
                len(journal._entries) - 1
            )
        return journal

    # ── private helpers ────────────────────────────────────────────

    def _merge_clock(self, other: Dict[str, int]) -> None:
        """Merge *other* vector clock into this journal's clock (pointwise max)."""
        for agent, counter in other.items():
            self._vector_clock[agent] = max(
                self._vector_clock.get(agent, 0), counter
            )

    @staticmethod
    def _clock_sort_value(vc: Dict[str, int]) -> Tuple[int, ...]:
        """Convert a vector clock to a sortable tuple.

        Clocks are sorted by their *maximum component first*, then by
        the sum of all components (to break ties deterministically).
        """
        if not vc:
            return (0,)
        values = sorted(vc.values(), reverse=True)
        return tuple(values) + (sum(values),)

    def _entry_sort_key(self, entry: DreamEntry) -> Tuple[float, str]:
        """Sort key for interleaving entries by causal order.

        Primary sort: vector clock magnitude.
        Secondary sort: timestamp.
        Tertiary sort: dream_id (for full determinism).
        """
        return (
            -sum(entry.vector_clock.values()),
            entry.timestamp,
            entry.dream_id,
        )

    def _filter(
        self,
        scenario_prefix: Optional[str],
        min_confidence: float,
    ) -> List[DreamEntry]:
        """Return entries matching the scenario prefix and confidence threshold."""
        results: List[DreamEntry] = []
        for e in self._entries:
            if e.confidence < min_confidence:
                continue
            if scenario_prefix and not e.scenario.startswith(scenario_prefix):
                continue
            results.append(e)
        return results

    @staticmethod
    def _tokenise(text: str) -> List[str]:
        """Lowercase and remove stop-words from *text*.

        Returns significant tokens sorted for stability.
        """
        tokens = [
            w
            for w in text.lower().split()
            if w not in STOP_WORDS and len(w) > 1
        ]
        return sorted(set(tokens))


# ────────────────────────────────────────────────────────────────────
# Tidepool Oracle (Main Interface)
# ────────────────────────────────────────────────────────────────────

class TidepoolOracle:
    """The Tidepool Oracle — emergent consensus engine for agent fleets.

    Coordinates per-agent dream journals, merges them into a collective
    journal, extracts patterns, detects anomalies, and produces guidance
    for real decisions.

    The oracle is the single entry-point for all operations.  Behind the
    scenes it maintains:

    * One :class:`DreamJournal` per agent (accessible via
      :meth:`merge_journals`).
    * One collective (fleet-wide) journal that aggregates all agent
      journals.

    Example::

        oracle = create_tidepool_oracle()
        oracle.record_dream("a1", "deploy-v2", "canary release", "smooth rollout", 0.9, ["deployment"])
        oracle.merge_journals("a1")
        print(oracle.consult("deploy-v2"))
    """

    def __init__(
        self,
        agent_id: str = "tidepool",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialise the oracle.

        Args:
            agent_id: Identifier for this oracle instance.
            config: Optional configuration overrides.  Keys not present
                fall back to :data:`_DEFAULT_CONFIG`.
        """
        self._agent_id: str = agent_id
        self._config: Dict[str, Any] = {
            **_DEFAULT_CONFIG,
            **(config or {}),
        }
        # Per-agent journals.
        self._agent_journals: Dict[str, DreamJournal] = {}
        # The collective fleet journal.
        self._fleet_journal: DreamJournal = DreamJournal("fleet")

    # ── configuration ──────────────────────────────────────────────

    def configure(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Update oracle configuration parameters.

        Only keys present in *params* are modified; the rest remain
        unchanged.

        Args:
            params: Dict of configuration key-value pairs to update.

        Returns:
            The full, updated configuration dict.
        """
        self._config.update(params)
        return dict(self._config)

    def get_config(self) -> Dict[str, Any]:
        """Return the current configuration (read-only copy)."""
        return dict(self._config)

    # ── recording dreams ───────────────────────────────────────────

    def record_dream(
        self,
        agent_id: str,
        scenario: str,
        action: str,
        predicted_outcome: str,
        confidence: float,
        tags: Optional[List[str]] = None,
        parent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Record a new dream from an agent.

        Creates a :class:`DreamEntry`, appends it to the agent's personal
        journal, and returns a summary.

        Args:
            agent_id: Identifier of the dreaming agent.
            scenario: Description of the hypothetical situation.
            action: Proposed course of action.
            predicted_outcome: What the agent predicts will happen.
            confidence: Confidence in the prediction (0.0 – 1.0).
            tags: Optional categorisation labels.
            parent_ids: Optional list of dream IDs that inspired this one.

        Returns:
            A dict with ``dream_id``, ``agent_id``, ``timestamp``,
            ``scenario``, ``journal_size``.
        """
        if tags is None:
            tags = []
        if parent_ids is None:
            parent_ids = []

        dream_id = f"drm-{uuid.uuid4()}"
        now = time.time()

        # Ensure the agent has a journal.
        journal = self._agent_journals.setdefault(
            agent_id, DreamJournal(agent_id)
        )

        # Build the vector clock for this entry: inherit from the
        # journal's clock, then increment the agent's counter.
        vc = dict(journal.vector_clock)
        vc[agent_id] = vc.get(agent_id, 0) + 1

        entry = DreamEntry(
            dream_id=dream_id,
            agent_id=agent_id,
            timestamp=now,
            scenario=scenario,
            action=action,
            predicted_outcome=predicted_outcome,
            confidence=confidence,
            vector_clock=vc,
            tags=tags,
            parent_dream_ids=parent_ids,
        )

        journal.append(entry)

        return {
            "dream_id": dream_id,
            "agent_id": agent_id,
            "timestamp": now,
            "scenario": scenario,
            "journal_size": journal.size(),
        }

    # ── merging ────────────────────────────────────────────────────

    def merge_journals(self, agent_id: str) -> Dict[str, Any]:
        """Merge an agent's journal into the collective fleet journal.

        After merging, the fleet journal reflects all dreams contributed
        by *agent_id* (plus any prior merges).

        Args:
            agent_id: The agent whose journal should be merged.

        Returns:
            A merge summary dict with ``agent_id``, ``entries_added``,
            ``entries_existing``, ``fleet_size``, ``merged_clock``.

        Raises:
            KeyError: If *agent_id* has no recorded journal.
        """
        journal = self._agent_journals.get(agent_id)
        if journal is None:
            raise KeyError(
                f"No journal found for agent '{agent_id}'. "
                f"Record at least one dream first."
            )
        result = self._fleet_journal.merge(journal)
        result["agent_id"] = agent_id
        return result

    def merge_all(self) -> Dict[str, Any]:
        """Merge every agent's journal into the fleet journal.

        Returns:
            A summary with ``agents_merged``, ``total_entries_added``,
            ``fleet_size``.
        """
        total_added = 0
        agents_merged: List[str] = []
        for agent_id in list(self._agent_journals):
            mr = self.merge_journals(agent_id)
            total_added += mr["entries_added"]
            agents_merged.append(agent_id)
        return {
            "agents_merged": agents_merged,
            "total_entries_added": total_added,
            "fleet_size": self._fleet_journal.size(),
        }

    # ── consultation ───────────────────────────────────────────────

    def consult(
        self,
        scenario: str,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ask the oracle for guidance on a scenario.

        Queries the fleet journal for consensus actions matching the
        given scenario prefix and returns the top recommendation along
        with supporting data.

        Args:
            scenario: The scenario to consult about (prefix match).
            min_confidence: Override the default confidence threshold.

        Returns:
            A dict with ``scenario``, ``recommendation`` (the top
            consensus action or ``None``), ``alternatives`` (runner-up
            actions), ``total_dreams_considered``, ``agents_consulted``.
        """
        mc = (
            min_confidence
            if min_confidence is not None
            else self._config["min_confidence"]
        )
        consensus = self._fleet_journal.get_consensus(scenario, mc)

        if not consensus:
            return {
                "scenario": scenario,
                "recommendation": None,
                "alternatives": [],
                "total_dreams_considered": 0,
                "agents_consulted": [],
            }

        recommendation = consensus[0]
        alternatives = consensus[1:]
        all_agents = set()
        for c in consensus:
            all_agents.update(c["agents"])

        return {
            "scenario": scenario,
            "recommendation": recommendation,
            "alternatives": alternatives[:5],
            "total_dreams_considered": sum(
                c["support_count"] for c in consensus
            ),
            "agents_consulted": sorted(all_agents),
        }

    # ── fleet metrics ──────────────────────────────────────────────

    def get_fleet_pulse(self) -> Dict[str, Any]:
        """Collective imagination metrics for the entire fleet.

        Returns:
            A dict with ``total_agents``, ``total_fleet_dreams``,
            ``fleet_imagination`` (from :meth:`DreamJournal.get_fleet_imagination`),
            ``dream_density``, ``vivid_dreams_count``.
        """
        imagination = self._fleet_journal.get_fleet_imagination()
        density = self._fleet_journal.get_dream_density(
            self._config["density_window_seconds"]
        )
        vivid = self._fleet_journal.get_vivid_dreams(
            self._config["vivid_dream_limit"]
        )
        return {
            "total_agents": len(self._agent_journals),
            "total_fleet_dreams": self._fleet_journal.size(),
            "fleet_imagination": imagination,
            "dream_density": density,
            "vivid_dreams_count": len(vivid),
        }

    def get_top_scenarios(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return the most discussed scenarios across the fleet.

        Args:
            limit: Maximum scenarios to return (default from config).

        Returns:
            A list of dicts with ``scenario``, ``dream_count``,
            ``unique_agents``, ``avg_confidence``, ``tags``.
        """
        limit = limit or self._config["top_scenarios_limit"]
        scenario_data: Dict[
            str, Dict[str, Any]
        ] = defaultdict(lambda: {
            "count": 0, "agents": set(), "confidences": [], "tags": set(),
        })
        for entry in self._fleet_journal.get_all_entries():
            sd = scenario_data[entry.scenario]
            sd["count"] += 1
            sd["agents"].add(entry.agent_id)
            sd["confidences"].append(entry.confidence)
            sd["tags"].update(entry.tags)

        results: List[Dict[str, Any]] = []
        for scenario, data in scenario_data.items():
            avg_c = (
                sum(data["confidences"]) / len(data["confidences"])
                if data["confidences"]
                else 0.0
            )
            results.append({
                "scenario": scenario,
                "dream_count": data["count"],
                "unique_agents": list(data["agents"]),
                "avg_confidence": round(avg_c, 4),
                "tags": list(data["tags"]),
            })

        results.sort(key=lambda r: r["dream_count"], reverse=True)
        return results[:limit]

    def get_dream_network(self) -> Dict[str, Any]:
        """Build the causality graph of dream inspiration.

        Each node is a dream, and directed edges point from parent dreams
        to child dreams (i.e. which dreams inspired which).

        Returns:
            A dict with ``nodes`` (list of ``{dream_id, agent_id,
            scenario, confidence}``), ``edges`` (list of
            ``{from_dream, to_dream}``), ``orphan_count``,
            ``max_depth``, ``cycles_detected``.
        """
        fleet_entries = self._fleet_journal.get_all_entries()
        entry_map: Dict[str, DreamEntry] = {
            e.dream_id: e for e in fleet_entries
        }

        nodes: List[Dict[str, Any]] = [
            {
                "dream_id": e.dream_id,
                "agent_id": e.agent_id,
                "scenario": e.scenario,
                "confidence": e.confidence,
            }
            for e in fleet_entries
        ]
        edges: List[Dict[str, str]] = []
        for e in fleet_entries:
            for pid in e.parent_dream_ids:
                if pid in entry_map:
                    edges.append({"from_dream": pid, "to_dream": e.dream_id})

        # Compute orphans (no parents).
        children: Set[str] = {edge["to_dream"] for edge in edges}
        all_ids: Set[str] = set(entry_map)
        orphan_count = len(all_ids - children)

        # Compute max depth via DFS.
        adjacency: Dict[str, List[str]] = defaultdict(list)
        for edge in edges:
            adjacency[edge["from_dream"]].append(edge["to_dream"])

        max_depth = 0
        visited_global: Set[str] = set()

        def _dfs(node: str, depth: int) -> None:
            nonlocal max_depth
            if depth > max_depth:
                max_depth = depth
            visited_global.add(node)
            for child in adjacency.get(node, []):
                _dfs(child, depth + 1)

        for nid in all_ids:
            if nid not in children:
                _dfs(nid, 0)

        # Simple cycle detection: if any child appears as an ancestor.
        has_cycle = False

        def _cycle_detect(node: str, ancestors: Set[str]) -> bool:
            if node in ancestors:
                return True
            new_ancestors = ancestors | {node}
            for child in adjacency.get(node, []):
                if _cycle_detect(child, new_ancestors):
                    return True
            return False

        for nid in all_ids:
            if _cycle_detect(nid, set()):
                has_cycle = True
                break

        return {
            "nodes": nodes,
            "edges": edges,
            "orphan_count": orphan_count,
            "max_depth": max_depth,
            "cycles_detected": has_cycle,
        }

    # ── simulation ─────────────────────────────────────────────────

    def run_simulation_round(
        self,
        scenarios: List[str],
        agents: List[str],
    ) -> Dict[str, Any]:
        """Simulate a round of dreaming across agents.

        For each (scenario, agent) pair, a deterministic synthetic dream
        is generated using SHA-256 hashing of the combined input.  This
        enables reproducible testing of the oracle without real agent
        input.

        The hash deterministically selects an action template, outcome
        template, confidence value, and tags.

        Args:
            scenarios: List of scenario description strings.
            agents: List of agent identifier strings.

        Returns:
            A dict with ``round_id``, ``scenarios_count``,
            ``agents_count``, ``dreams_generated``, ``dreams`` (list of
            serialised entries), ``fleet_size_after``.
        """
        round_id = f"sim-{uuid.uuid4().hex[:8]}"
        modifier = self._config.get("simulation_seed_modifier", 0)
        dreams_generated: List[Dict[str, Any]] = []

        for scenario in scenarios:
            for agent_id in agents:
                seed_input = f"{scenario}:{agent_id}:{modifier}".encode("utf-8")
                digest = hashlib.sha256(seed_input).hexdigest()
                seed_int = int(digest, 16)

                # Deterministic action selection.
                action_idx = seed_int % len(_ACTION_TEMPLATES)
                action = _ACTION_TEMPLATES[action_idx]

                # Deterministic outcome selection (use different part of hash).
                outcome_idx = (seed_int >> 8) % len(_OUTCOME_TEMPLATES)
                outcome = _OUTCOME_TEMPLATES[outcome_idx]

                # Deterministic confidence: 0.5 – 1.0.
                conf_raw = (seed_int >> 16) % 10000
                confidence = 0.5 + 0.5 * (conf_raw / 10000.0)
                confidence = round(confidence, 4)

                # Deterministic tags.
                tag_pool = ["simulation", "testing", "merge-strategy",
                            "deployment", "monitoring", "performance",
                            "reliability", "coordination"]
                num_tags = 1 + (seed_int >> 24) % 3
                tag_start = (seed_int >> 28) % len(tag_pool)
                tags = [
                    tag_pool[(tag_start + i) % len(tag_pool)]
                    for i in range(num_tags)
                ]

                result = self.record_dream(
                    agent_id=agent_id,
                    scenario=scenario,
                    action=action,
                    predicted_outcome=outcome,
                    confidence=confidence,
                    tags=tags,
                    parent_ids=[],
                )
                dreams_generated.append({
                    "dream_id": result["dream_id"],
                    "agent_id": agent_id,
                    "scenario": scenario,
                    "action": action,
                    "predicted_outcome": outcome,
                    "confidence": confidence,
                    "tags": tags,
                })

        # Auto-merge all agent journals into the fleet journal.
        merge_summary = self.merge_all()

        return {
            "round_id": round_id,
            "scenarios_count": len(scenarios),
            "agents_count": len(agents),
            "dreams_generated": dreams_generated,
            "total_generated": len(dreams_generated),
            "merge_summary": merge_summary,
            "fleet_size_after": self._fleet_journal.size(),
        }

    # ── anomaly detection ──────────────────────────────────────────

    def get_anomalies(
        self,
        min_confidence: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Find dreams that contradict the fleet consensus.

        An anomaly is a dream that (a) has high confidence, but (b)
        proposes a different action than the top consensus action for
        its scenario.  The confidence gap between the anomaly and the
        consensus action's average confidence determines severity.

        Args:
            min_confidence: Minimum confidence for anomalies.  Defaults
                to the configured ``min_confidence``.

        Returns:
            A list of anomaly descriptors sorted by descending severity.
            Each has ``dream_id``, ``agent_id``, ``scenario``,
            ``anomaly_action``, ``consensus_action``,
            ``anomaly_confidence``, ``consensus_avg_confidence``,
            ``confidence_gap``, ``severity``.
        """
        mc = (
            min_confidence
            if min_confidence is not None
            else self._config["min_confidence"]
        )
        gap_threshold = self._config["anomaly_confidence_gap"]

        # Build per-scenario consensus maps.
        scenario_consensus: Dict[str, Dict[str, Any]] = {}
        entries = self._fleet_journal.get_all_entries()
        scenario_groups: Dict[str, List[DreamEntry]] = defaultdict(list)
        for e in entries:
            if e.confidence >= mc:
                scenario_groups[e.scenario].append(e)

        for scenario, group in scenario_groups.items():
            action_groups: Dict[str, List[DreamEntry]] = defaultdict(list)
            for e in group:
                action_groups[e.action].append(e)

            if not action_groups:
                continue

            # Find top action by consensus score.
            best_action = max(
                action_groups,
                key=lambda a: len(action_groups[a])
                * sum(e.confidence for e in action_groups[a])
                / len(action_groups[a]),
            )
            best_avg = (
                sum(e.confidence for e in action_groups[best_action])
                / len(action_groups[best_action])
            )
            scenario_consensus[scenario] = {
                "action": best_action,
                "avg_confidence": best_avg,
            }

        # Identify anomalies.
        anomalies: List[Dict[str, Any]] = []
        for e in entries:
            if e.confidence < mc:
                continue
            consensus = scenario_consensus.get(e.scenario)
            if consensus is None:
                continue
            if e.action == consensus["action"]:
                continue
            gap = abs(e.confidence - consensus["avg_confidence"])
            if gap >= gap_threshold:
                severity = (
                    "high" if gap >= 0.5
                    else "medium" if gap >= gap_threshold
                    else "low"
                )
                anomalies.append({
                    "dream_id": e.dream_id,
                    "agent_id": e.agent_id,
                    "scenario": e.scenario,
                    "anomaly_action": e.action,
                    "consensus_action": consensus["action"],
                    "anomaly_confidence": e.confidence,
                    "consensus_avg_confidence": round(
                        consensus["avg_confidence"], 4
                    ),
                    "confidence_gap": round(gap, 4),
                    "severity": severity,
                })

        anomalies.sort(key=lambda a: a["confidence_gap"], reverse=True)
        return anomalies

    # ── reporting ──────────────────────────────────────────────────

    def get_wisdom_report(self) -> str:
        """Generate a comprehensive Markdown wisdom report.

        The report includes fleet pulse, top scenarios, pattern
        analysis, anomalies, vivid dreams, and the dream network summary.

        Returns:
            A multi-line Markdown string.
        """
        lines: List[str] = []
        lines.append("# Tidepool Oracle — Fleet Wisdom Report")
        lines.append("")
        lines.append(
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        lines.append("")

        # Fleet pulse.
        pulse = self.get_fleet_pulse()
        lines.append("## Fleet Pulse")
        lines.append("")
        lines.append(f"- **Total agents:** {pulse['total_agents']}")
        lines.append(f"- **Total fleet dreams:** {pulse['total_fleet_dreams']}")
        imag = pulse.get("fleet_imagination", {})
        lines.append(
            f"- **Avg confidence:** {imag.get('avg_confidence', 'N/A')}"
        )
        lines.append(
            f"- **Confidence σ:** {imag.get('confidence_stddev', 'N/A')}"
        )
        density = pulse.get("dream_density", {})
        lines.append(
            f"- **Dreams/min (last {density.get('window_seconds', '?')}s):** "
            f"{density.get('dreams_per_minute', 'N/A')}"
        )
        lines.append("")

        # Top scenarios.
        top_scenarios = self.get_top_scenarios()
        lines.append("## Top Scenarios")
        lines.append("")
        if top_scenarios:
            lines.append("| Scenario | Dreams | Agents | Avg Conf |")
            lines.append("|----------|--------|--------|----------|")
            for s in top_scenarios:
                agent_str = ", ".join(
                    f"`{a}`" for a in s["unique_agents"][:4]
                )
                if len(s["unique_agents"]) > 4:
                    agent_str += f" +{len(s['unique_agents']) - 4}"
                lines.append(
                    f"| {s['scenario'][:50]} | {s['dream_count']} "
                    f"| {agent_str} | {s['avg_confidence']:.2%} |"
                )
        else:
            lines.append("*No scenarios recorded yet.*")
        lines.append("")

        # Pattern analysis.
        patterns = self._fleet_journal.extract_patterns(
            self._config["pattern_min_occurrences"]
        )
        lines.append("## Recurring Patterns")
        lines.append("")
        if patterns:
            for p in patterns[:10]:
                kw_str = ", ".join(f"`{k}`" for k in p["keywords"][:6])
                agent_str = ", ".join(
                    f"`{a}`" for a in p["agents_contributing"][:4]
                )
                lines.append(
                    f"- **Freq {p['frequency']}:** {kw_str}  "
                    f"(agents: {agent_str}, avg conf: {p['avg_confidence']:.2%})"
                )
        else:
            lines.append("*No recurring patterns detected yet.*")
        lines.append("")

        # Anomalies.
        anomalies = self.get_anomalies()
        lines.append("## Anomalies (Contradictions)")
        lines.append("")
        if anomalies:
            for a in anomalies[:10]:
                lines.append(
                    f"- **[{a['severity'].upper()}]** `{a['agent_id']}` "
                    f"proposes **{a['anomaly_action'][:60]}** for "
                    f"`{a['scenario'][:40]}` "
                    f"(consensus: {a['consensus_action'][:40]}, "
                    f"gap: {a['confidence_gap']:.2%})"
                )
        else:
            lines.append("*No anomalies detected.*")
        lines.append("")

        # Vivid dreams.
        vivid = self._fleet_journal.get_vivid_dreams(
            self._config["vivid_dream_limit"]
        )
        lines.append("## Most Vivid Dreams")
        lines.append("")
        if vivid:
            for v in vivid[:5]:
                lines.append(
                    f"- `{v['dream_id']}` by `{v['agent_id']}` — "
                    f"conf **{v['confidence']:.2%}**: "
                    f"*{v['scenario'][:60]}* → *{v['action'][:60]}*"
                )
        else:
            lines.append("*No vivid dreams recorded.*")
        lines.append("")

        # Dream network summary.
        network = self.get_dream_network()
        lines.append("## Dream Network")
        lines.append("")
        lines.append(f"- **Nodes:** {len(network['nodes'])}")
        lines.append(f"- **Edges:** {len(network['edges'])}")
        lines.append(f"- **Orphan roots:** {network['orphan_count']}")
        lines.append(f"- **Max depth:** {network['max_depth']}")
        lines.append(
            f"- **Cycles detected:** {'yes' if network['cycles_detected'] else 'no'}"
        )
        lines.append("")

        # Fleet journal markdown excerpt.
        lines.append("---")
        lines.append("")
        lines.append("## Fleet Dream Journal (excerpt)")
        lines.append("")
        fleet_md = self._fleet_journal.export_markdown()
        # Include only the header and first few entries.
        fleet_lines = fleet_md.split("\n")
        excerpt_end = 0
        entry_count = 0
        for i, fl in enumerate(fleet_lines):
            if fl.startswith("## Dream "):
                entry_count += 1
                if entry_count > 5:
                    excerpt_end = i
                    break
        if excerpt_end > 0:
            lines.extend(fleet_lines[:excerpt_end])
            remaining = len(fleet_lines) - excerpt_end
            lines.append(f"\n*... and {remaining} more lines (truncated).*\n")
        else:
            lines.append(fleet_md)

        return "\n".join(lines)

    # ── direct fleet journal access ────────────────────────────────

    def get_fleet_journal(self) -> DreamJournal:
        """Return a reference to the collective fleet journal.

        This allows advanced callers to use journal-level APIs
        (``get_consensus``, ``extract_patterns``, etc.) directly.
        """
        return self._fleet_journal

    def get_agent_journal(self, agent_id: str) -> Optional[DreamJournal]:
        """Return the journal for a specific agent, or ``None``."""
        return self._agent_journals.get(agent_id)

    # ── serialisation ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the entire oracle state to a plain dictionary.

        Useful for persistence or network transfer.
        """
        return {
            "agent_id": self._agent_id,
            "config": dict(self._config),
            "agent_journals": {
                aid: j.to_dict() for aid, j in self._agent_journals.items()
            },
            "fleet_journal": self._fleet_journal.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TidepoolOracle:
        """Deserialise an oracle from a plain dictionary.

        Args:
            data: A dict previously produced by :meth:`to_dict`.

        Returns:
            A fully reconstructed :class:`TidepoolOracle`.
        """
        oracle = cls(
            agent_id=data["agent_id"],
            config=data.get("config"),
        )
        for aid, jd in data.get("agent_journals", {}).items():
            oracle._agent_journals[aid] = DreamJournal.from_dict(jd)
        oracle._fleet_journal = DreamJournal.from_dict(
            data["fleet_journal"]
        )
        return oracle


# ────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────

def create_tidepool_oracle(
    agent_id: str = "tidepool",
    config: Optional[Dict[str, Any]] = None,
) -> TidepoolOracle:
    """Create and return a new :class:`TidepoolOracle` instance.

    This is the recommended entry-point for constructing an oracle.

    Args:
        agent_id: Identifier for the oracle instance (default
            ``"tidepool"``).
        config: Optional configuration overrides merged on top of the
            defaults.

    Returns:
        A ready-to-use :class:`TidepoolOracle`.

    Example::

        oracle = create_tidepool_oracle(agent_id="oracle-prod-1")
        oracle.record_dream(
            agent_id="agent-3",
            scenario="service-discovery timeout",
            action="fall back to cached DNS records",
            predicted_outcome="request served within SLA",
            confidence=0.88,
            tags=["resilience", "networking"],
        )
    """
    return TidepoolOracle(agent_id=agent_id, config=config)
