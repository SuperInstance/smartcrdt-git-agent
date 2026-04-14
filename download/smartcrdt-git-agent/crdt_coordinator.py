"""CRDT Merge Analysis Engine for the SmartCRDT Monorepo.

Analyzes, classifies, and reasons about CRDT merge semantics across seven
families: counters, sets, registers, clocks, gossip, maps, and sequences.

Usage::

    c = CRDTCoordinator()
    c.analyze_merge("g-counter", "increment", {"replica_id": "r1", ...})
    c.detect_conflicts("lww-register", ops_list)
"""

from __future__ import annotations

import hashlib, json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


class Convergence(str, Enum):
    """Convergence guarantee levels."""
    STRONG = "strong"
    EVENTUAL = "eventual"
    WEAK = "weak"
    NONE = "none"


class OpKind(str, Enum):
    """CRDT operation semantic roles."""
    READ = "read"; WRITE = "write"; MERGE = "merge"; QUERY = "query"


@dataclass
class _Spec:
    """Internal CRDT type specification."""
    name: str; family: str; desc: str; merge_fn: str
    conv: Convergence; cplx: str; comm: bool; idem: bool
    uses: List[str]; warns: List[str]; antis: List[str]
    ops: Dict[str, OpKind]; state: str


class CRDTCoordinator:
    """Merge analysis engine for the SmartCRDT monorepo.

    Provides merge implication analysis, conflict detection, operation
    classification, convergence assessment, and resolution recommendations
    across all seven CRDT families (19 types total).
    """

    def __init__(self) -> None:
        self._t: Dict[str, _Spec] = {}
        self._reg()

    # ---------------------------------------------------------------- types

    def get_supported_types(self) -> List[str]:
        """Return sorted list of all supported CRDT type identifiers."""
        return sorted(self._t)

    def get_semantics(self, crdt_type: str) -> Dict[str, Any]:
        """Return comprehensive merge semantics for a CRDT type.

        Includes merge function description, convergence guarantees,
        complexity, use cases, warnings, and anti-patterns.

        Args:
            crdt_type: Identifier such as ``"g-counter"`` or ``"lww-register"``.

        Returns:
            Dict with name, family, description, merge_function, convergence,
            complexity, commutative, idempotent, use_cases, warnings,
            anti_patterns, operations, state_structure.

        Raises:
            ValueError: If *crdt_type* is not recognised.
        """
        s = self._r(crdt_type)
        return {
            "name": s.name, "family": s.family, "description": s.desc,
            "merge_function": s.merge_fn, "convergence": s.conv.value,
            "complexity": s.cplx, "commutative": s.comm, "idempotent": s.idem,
            "use_cases": s.uses, "warnings": s.warns, "anti_patterns": s.antis,
            "operations": {k: v.value for k, v in s.ops.items()},
            "state_structure": s.state,
        }

    # -------------------------------------------------------------- analysis

    def analyze_merge(self, crdt_type: str, operation: str,
                      ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze merge implications of an operation on a CRDT type.

        Determines causal ordering, concurrency, convergence, and flags
        hazards (split-brain, lost updates, ordering violations).

        Args:
            crdt_type: CRDT type identifier.
            operation: Operation name (e.g. ``"add"``).
            ctx: Dict with ``replica_id``, ``state``, optional ``peer_states``,
                ``clock_skew_ms``, ``vector_clocks``.

        Returns:
            Dict with concurrent, causal_order, convergence, hazards,
            resolution_hint, merge_complexity, type_commutative,
            type_idempotent.
        """
        s = self._r(crdt_type)
        peers = ctx.get("peer_states", {})
        loc = ctx.get("state", {})
        conc = any(p != loc for p in peers.values()) if peers else False
        causal = self._co(ctx)
        hz: List[str] = []
        if conc and not s.comm:
            hz.append("non-commutative-concurrent-write")
        if not peers and operation not in ("query", "read", "merge"):
            hz.append("possible-lost-update-no-peers")
        if s.conv in (Convergence.WEAK, Convergence.NONE):
            hz.append(f"weak-convergence:{s.conv.value}")
        if operation == "merge" and not loc:
            hz.append("merge-into-empty-state")
        if crdt_type in ("lww-register", "hlc") and ctx.get("clock_skew_ms", 0) > 0:
            hz.append(f"clock-skick:{ctx['clock_skew_ms']}ms")
        n = len(peers) + 1
        return {
            "crdt_type": crdt_type, "operation": operation,
            "replica_id": ctx.get("replica_id", "unknown"),
            "concurrent": conc, "causal_order": causal,
            "convergence": s.conv.value, "hazards": hz,
            "resolution_hint": self._ar(s, hz),
            "merge_complexity": f"{s.cplx} (n={n})" if "O(1)" not in s.cplx else s.cplx,
            "type_commutative": s.comm, "type_idempotent": s.idem,
        }

    # -------------------------------------------------------- classification

    def classify_operation(self, crdt_type: str,
                           operation: str) -> Dict[str, Any]:
        """Classify an operation by its CRDT semantic role.

        Determines read/write/merge/query and assesses commutativity
        and idempotency.

        Args:
            crdt_type: CRDT type identifier.
            operation: Operation name.

        Returns:
            Dict with crdt_type, operation, kind, commutative, idempotent,
            description.
        """
        s = self._r(crdt_type)
        k = s.ops.get(operation, OpKind.WRITE)
        descs = {OpKind.READ: "Reads current value without mutation.",
                 OpKind.WRITE: "Mutates the CRDT state.",
                 OpKind.MERGE: "Incorporates a remote replica's state.",
                 OpKind.QUERY: "Inspects metadata or derived state."}
        return {
            "crdt_type": crdt_type, "operation": operation, "kind": k.value,
            "commutative": s.comm or k in (OpKind.READ, OpKind.QUERY),
            "idempotent": s.idem or k in (OpKind.READ, OpKind.QUERY, OpKind.MERGE),
            "description": descs.get(k, ""),
        }

    # -------------------------------------------------------- conflict detect

    def detect_conflicts(self, crdt_type: str,
                         ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect potential conflicts among concurrent operations.

        Each dict needs ``operation`` and ``replica_id``; optional
        ``clock_value``, ``timestamp``, ``key``.

        Returns:
            List of conflict descriptors with conflict_type,
            operations_involved, severity, description, suggested_resolution.
        """
        s = self._r(crdt_type)
        cf: List[Dict[str, Any]] = []
        ws = [o for o in ops
              if s.ops.get(o.get("operation", ""), OpKind.WRITE) == OpKind.WRITE]
        # Concurrent writes from distinct replicas
        if len(ws) > 1 and len({o.get("replica_id") for o in ws}) > 1:
            sev = "high" if not s.comm else "low"
            cf.append({"conflict_type": "concurrent-write",
                       "operations_involved": [{"replica_id": o.get("replica_id"),
                                                "op": o.get("operation")} for o in ws],
                       "severity": sev,
                       "description": "Concurrent writes from distinct replicas.",
                       "suggested_resolution": self._ar(s, ["concurrent-write"])})
        # Lost-update risk
        for o in ws:
            if "clock_value" not in o and "timestamp" not in o:
                cf.append({"conflict_type": "lost-update-risk",
                           "operations_involved": [{"replica_id": o.get("replica_id"),
                                                    "op": o.get("operation")}],
                           "severity": "medium",
                           "description": "Op lacks causal metadata.",
                           "suggested_resolution": "Attach vector clock or HLC."})
        # Ordering ambiguity for LWW
        if crdt_type in ("lww-register", "hlc"):
            ts = [(r, t) for o in ops if (t := o.get("timestamp", o.get("clock_value")))
                  is not None for r in [o.get("replica_id")]]
            ts.sort(key=lambda x: x[1])
            if len(ts) >= 2 and any(
                    abs(ts[i][1] - ts[i-1][1]) < 1e-9 for i in range(1, len(ts))):
                cf.append({"conflict_type": "ordering-ambiguity",
                           "operations_involved": [{"replica_id": r, "ts": t} for r, t in ts],
                           "severity": "medium",
                           "description": "Identical timestamps — arbitrary tiebreak.",
                           "suggested_resolution": "Use HLC for better tiebreaking."})
        return cf

    # --------------------------------------------------------- test vectors

    def generate_test_vectors(self, crdt_type: str,
                              count: int = 5) -> List[Dict[str, Any]]:
        """Generate executable test scenario descriptors for a CRDT type.

        Vary concurrent writes, partitions, and clock skew scenarios.

        Args:
            crdt_type: CRDT type identifier.
            count: Number of vectors (default 5).

        Returns:
            List of dicts with id, crdt_type, description, initial_state,
            operations, expected_outcome, tags.
        """
        s = self._r(crdt_type)
        vs: List[Dict[str, Any]] = []
        for i in range(count):
            mode = i % 3
            tags = (["concurrent"] if mode == 0 else
                    ["partition"] if mode == 1 else [])
            if i % 5 == 4: tags.append("clock_skew")
            if i % 7 == 6: tags.append("message_loss")
            nr = min(2 + i % 3, 5)
            ol = [{"replica_id": f"r{j % nr}", "operation": on, "timestamp": 1000*j+i}
                  for j, on in enumerate(s.ops) if j < 2 + i]
            vid = hashlib.sha256(
                f"{crdt_type}:{i}:{json.dumps(ol, sort_keys=True)}".encode()
            ).hexdigest()[:12]
            vs.append({"id": vid, "crdt_type": crdt_type,
                        "description": f"Scenario {i+1}/{count} for {s.name}",
                        "initial_state": {"type": s.name}, "operations": ol,
                        "expected_outcome": f"converge:{mode != 1 or s.comm}",
                        "tags": tags})
        return vs

    # ------------------------------------------------------- convergence

    def assess_convergence(self, crdt_type: str,
                           scenario: Dict[str, Any]) -> Dict[str, Any]:
        """Assess whether a scenario guarantees CRDT convergence.

        Args:
            crdt_type: CRDT type identifier.
            scenario: Dict with operations, network_partition (bool),
                message_loss (bool), clock_skew_ms (int).

        Returns:
            Dict with guaranteed (bool), confidence (0–1), warnings,
            requirements, convergence_level.
        """
        s = self._r(crdt_type)
        part = scenario.get("network_partition", False)
        mloss = scenario.get("message_loss", False)
        skew = scenario.get("clock_skew_ms", 0)
        w: List[str] = []; rq: List[str] = []; ok = True; conf = 1.0
        if s.conv == Convergence.STRONG:
            rq.append("All replicas must eventually deliver all messages")
        elif s.conv == Convergence.EVENTUAL:
            rq.append("No permanent partition")
        else:
            ok = False; conf = 0.3
            w.append(f"{s.conv.value} convergence — may need coordination")
        if part and s.conv == Convergence.EVENTUAL:
            ok = False; conf *= 0.5; w.append("Partition blocks delivery")
        if mloss:
            conf *= 0.7; w.append("Message loss may drop deltas")
        if crdt_type == "lww-register" and skew > 0:
            conf = min(conf, 0.85); w.append(f"Clock skew {skew}ms")
        for o in scenario.get("operations", []):
            if o.get("operation", "") not in s.ops:
                w.append(f"Unknown op '{o.get('operation')}'"); conf *= 0.8
        return {"crdt_type": crdt_type, "guaranteed": ok,
                "confidence": round(conf, 4), "warnings": w,
                "requirements": rq, "convergence_level": s.conv.value}

    # -------------------------------------------------------- resolution

    def recommend_resolution(self, crdt_type: str,
                             conflict: Dict[str, Any]) -> Dict[str, Any]:
        """Recommend a resolution strategy for a detected conflict.

        Returns prioritised strategies tailored to CRDT type and conflict.

        Args:
            crdt_type: CRDT type identifier.
            conflict: Conflict descriptor from detect_conflicts.

        Returns:
            Dict with primary_strategy, fallback_strategies, rationale,
            complexity, severity, caveats.
        """
        s = self._r(crdt_type)
        ct = conflict.get("conflict_type", "unknown")
        sv = conflict.get("severity", "medium")
        strat: Dict[str, Dict[str, Any]] = {
            ("concurrent-write", "counter"): dict(
                primary="merge-counters", fallbacks=["lww", "app-resolve"],
                rationale="Counters are commutative; merge by summing.",
                complexity="O(n) replicas"),
            ("concurrent-write", "set"): dict(
                primary="crdt-set-merge", fallbacks=["union-bias", "app-resolve"],
                rationale="OR-Set semantics resolve via add/remove tags.",
                complexity="O(k) elements"),
            ("concurrent-write", "register"): dict(
                primary="lww-tiebreak", fallbacks=["mv-register", "app-resolve"],
                rationale="LWW with timestamp + replica-id tiebreak.",
                complexity="O(1)"),
            ("concurrent-write", "map"): dict(
                primary="recursive-merge", fallbacks=["shallow-merge", "app-resolve"],
                rationale="Nested CRDT maps merge recursively.",
                complexity="O(depth * keys)"),
            ("concurrent-write", "sequence"): dict(
                primary="positional-merge", fallbacks=["app-resolve", "manual"],
                rationale="RGA/Treedoc merge by unique identifiers.",
                complexity="O(n log n)"),
            ("lost-update-risk", "*"): dict(
                primary="attach-vector-clock", fallbacks=["hlc", "causal-bcast"],
                rationale="Causal metadata prevents lost updates.",
                complexity="O(replicas)"),
            ("ordering-ambiguity", "register"): dict(
                primary="hlc-clock", fallbacks=["lamport", "replica-id"],
                rationale="HLC provides physical + logical ordering.",
                complexity="O(1)"),
        }
        e = strat.get((ct, s.family)) or strat.get((ct, "*"), dict(
            primary="app-resolve", fallbacks=["manual"],
            rationale=f"No auto resolution for {ct} on {s.family}.",
            complexity="varies"))
        cv: List[str] = []
        if sv == "high" and s.conv != Convergence.STRONG:
            cv.append("High severity on non-strong type — divergence possible.")
        if not s.comm:
            cv.append("Non-commutative: resolution order matters.")
        return {"crdt_type": crdt_type, "conflict_type": ct,
                "primary_strategy": e["primary"],
                "fallback_strategies": e.get("fallbacks", []),
                "rationale": e["rationale"],
                "complexity": e.get("complexity", "unknown"),
                "severity": sv, "caveats": cv}

    # ================================================================ priv

    def _r(self, ct: str) -> _Spec:
        if ct not in self._t:
            raise ValueError(f"Unknown CRDT type '{ct}'. "
                             f"Supported: {', '.join(sorted(self._t))}")
        return self._t[ct]

    @staticmethod
    def _co(ctx: Dict[str, Any]) -> str:
        """Determine causal ordering from vector_clocks."""
        cl = ctx.get("vector_clocks", {}); vs = list(cl.values())
        if len(vs) < 2: return "unknown"
        a, b = vs[0], vs[1]; ks = set(a) | set(b)
        ad = all(a.get(k, 0) >= b.get(k, 0) for k in ks)
        bd = all(b.get(k, 0) >= a.get(k, 0) for k in ks)
        return "happens-before" if ad else ("happens-after" if bd else "concurrent")

    @staticmethod
    def _ar(s: _Spec, h: List[str]) -> str:
        """Auto-resolve hint from hazard list."""
        hs = str(h)
        if "clock-skick" in hs: return "Migrate to Hybrid Logical Clock."
        if "non-commutative" in hs: return "Ensure causal ordering via vector clocks."
        if "weak-convergence" in hs: return "Add application-level reconciliation."
        if "merge-into-empty" in hs: return "Initialise state before merging."
        return "No specific resolution required."

    # ============================================================ registry

    def _reg(self) -> None:
        """Populate the type registry with all 7 CRDT families (19 types)."""
        W, R, M, Q = OpKind.WRITE, OpKind.READ, OpKind.MERGE, OpKind.QUERY
        S, E = Convergence.STRONG, Convergence.EVENTUAL
        t = self._t

        # ── 1. Counters ──────────────────────────────────────────────────
        t["g-counter"] = _Spec(
            "G-Counter", "counter",
            "Grow-only counter; per-replica counts merged by element-wise max.",
            "merge(a,b)={k:max(a[k],b[k]) for k in a|b}", S, "O(n)",
            True, True, ["likes", "page views", "event counts"],
            ["Cannot decrement — use PN-Counter."],
            ["G-Counter when decrements needed"],
            {"increment": W, "merge": M, "value": Q, "reset": W},
            "Dict[str, int]  # replica_id → count")

        t["pn-counter"] = _Spec(
            "PN-Counter", "counter",
            "Two G-Counters (P, N); value = P − N. Merge each component.",
            "merge(a,b)=(merge_g(a.P,b.P),merge_g(a.N,b.N))", S, "O(n)",
            True, True, ["balance", "inventory", "vote tallies"],
            ["May go negative during partition."],
            ["Relying on non-negative invariant without checks"],
            {"increment": W, "decrement": W, "merge": M, "value": Q},
            "Dict[str, Tuple[int,int]]  # (P,N) per replica")

        t["bounded-counter"] = _Spec(
            "Bounded Counter", "counter",
            "PN-Counter clamped to [lower, upper] with compensation logic.",
            "merge then clamp to [lower, upper] with compensation", E, "O(n)",
            True, False, ["rate limiters", "seat reservations", "quotas"],
            ["Bounds may be temporarily violated during partitions."],
            ["Bounded counter without compensation mechanism"],
            {"increment": W, "decrement": W, "merge": M, "value": Q, "clamp": W},
            "PN-Counter + [lower, upper] bounds")

        # ── 2. Sets ──────────────────────────────────────────────────────
        t["add-wins-set"] = _Spec(
            "Add-Wins Set", "set",
            "OR-Set variant: concurrent add+remove → add wins (kept).",
            "union of tags; concurrent add beats remove", S, "O(k)",
            True, True, ["user groups", "feature flags", "tags"],
            ["Removed elements may reappear after merge."],
            ["Assuming remove is permanent during partitions"],
            {"add": W, "remove": W, "merge": M, "contains": Q, "members": R},
            "Dict[element, Set[unique_tag]]")

        t["remove-wins-set"] = _Spec(
            "Remove-Wins Set", "set",
            "Concurrent add+remove → remove wins (element dropped).",
            "union of tags; concurrent remove beats add", S, "O(k)",
            True, True, ["blocklists", "revocation lists"],
            ["Added elements may disappear after merge."],
            ["Remove-wins for collaborative editing"],
            {"add": W, "remove": W, "merge": M, "contains": Q, "members": R},
            "Dict[element, Set[unique_tag]]")

        t["observed-remove-set"] = _Spec(
            "Observed-Remove Set (OR-Set)", "set",
            "Standard OR-Set: tagged on add; remove deletes observed tags only.",
            "merge = union of all add/remove tags per element", S, "O(k)",
            True, True, ["shopping carts", "shared state", "collab docs"],
            ["Memory grows with history; GC required."],
            ["Never GC-ing tombstones on long-lived sets"],
            {"add": W, "remove": W, "merge": M, "contains": Q, "members": R, "gc": W},
            "Dict[element, Set[unique_tag]]")

        # ── 3. Registers ─────────────────────────────────────────────────
        t["lww-register"] = _Spec(
            "Last-Writer-Wins Register", "register",
            "Latest-timestamped value wins; ties broken by replica ID.",
            "merge(a,b)=a if (ts_a,id_a)>(ts_b,id_b) else b", S, "O(1)",
            True, True, ["user profiles", "config", "metadata"],
            ["Concurrent writes silently discard losers."],
            ["LWW for collaborative text editing"],
            {"set": W, "get": R, "merge": M},
            "Tuple[timestamp, replica_id, value]")

        t["multi-value-register"] = _Spec(
            "Multi-Value Register (MV-Register)", "register",
            "Retains all concurrent values for application-level resolution.",
            "merge(a,b)=concurrent_union(values_a,values_b)", S, "O(c)",
            True, True, ["conflict-aware data", "audit trails"],
            ["Application must handle multiple concurrent values."],
            ["Assuming single value without checking concurrent set"],
            {"set": W, "get": R, "merge": M, "values": Q},
            "Set[Tuple[vector_clock, value]]")

        # ── 4. Clocks ────────────────────────────────────────────────────
        t["lamport-clock"] = _Spec(
            "Lamport Clock", "clock",
            "Single integer logical clock for partial event ordering.",
            "merge(a,b)=max(a,b)", S, "O(1)",
            True, True, ["event ordering", "causal timestamps"],
            ["Cannot distinguish all concurrency."],
            ["Lamport alone for distributed locking"],
            {"tick": W, "merge": M, "now": R}, "int")

        t["vector-clock"] = _Spec(
            "Vector Clock", "clock",
            "Per-replica counter array capturing causal happens-before.",
            "merge(a,b)={k:max(a[k],b[k])}", S, "O(n)",
            True, True, ["causal tracking", "conflict detection", "versioning"],
            ["Grows linearly with replica count."],
            ["Vector clocks with ephemeral replicas"],
            {"increment": W, "merge": M, "happens_before": Q, "now": R},
            "Dict[str, int]")

        t["hlc"] = _Spec(
            "Hybrid Logical Clock (HLC)", "clock",
            "Physical + logical time for causal ordering near wall-clock.",
            "l=max(l_a,l_b,pt); c=l==l_a?c_a+1:(l==l_b?c_b+1:0)", S, "O(1)",
            True, False, ["distributed DBs", "event sourcing", "LWW registers"],
            ["Requires bounded physical clock drift."],
            ["Deploying across nodes with > ε drift"],
            {"now": R, "send": W, "receive": W, "merge": M},
            "Tuple[physical_time, logical_counter, node_id]")

        # ── 5. Gossip ────────────────────────────────────────────────────
        t["anti-entropy"] = _Spec(
            "Anti-Entropy Gossip", "gossip",
            "Full-state periodic gossip: exchange complete state and merge.",
            "each peer applies CRDT merge on received full state", E, "O(state*fanout)",
            True, True, ["membership", "config propagation", "bootstrap"],
            ["Bandwidth intensive for large states."],
            ["Anti-entropy only for large datasets"],
            {"gossip": M, "merge": M, "initiate": W},
            "Full CRDT state of managed type")

        t["plumtree"] = _Spec(
            "Plumtree (Epidemic Broadcast Trees)", "gossip",
            "Broadcast tree for O(log n) delivery + lazy anti-entropy recovery.",
            "disseminate via tree; recover missing via anti-entropy", E, "O(log n)",
            True, True, ["message broadcast", "event distribution", "pub-sub"],
            ["Tree may stale on churn."],
            ["Plumtree without periodic tree maintenance"],
            {"broadcast": W, "receive": M, "graft": W, "prune": W, "ihave": Q},
            "Tree topology + message cache")

        t["hyparview"] = _Spec(
            "HyParView", "gossip",
            "Membership protocol with random partial view for gossip overlay.",
            "merge views via join/forward/neighbour/shuffle", E, "O(log n)",
            True, False, ["cluster membership", "peer discovery"],
            ["View convergence is probabilistic."],
            ["HyParView alone for strong consistency"],
            {"join": W, "forward_join": W, "neighbour": W, "shuffle": W, "merge": M},
            "active_view + passive_view + node_id")

        # ── 6. Maps ──────────────────────────────────────────────────────
        t["crdt-map"] = _Spec(
            "CRDT Map", "map",
            "Composite CRDT: each value is nested CRDT. Recursive merge.",
            "for each key merge value CRDTs + metadata registers", S, "O(keys*v)",
            True, True, ["document models", "game state", "config trees"],
            ["Key removal needs tombstone; memory leak risk."],
            ["Deep nested maps without GC"],
            {"set": W, "remove": W, "get": R, "merge": M, "keys": Q},
            "Dict[key, Tuple[metadata_register, value_crdt]]")

        # ── 7. Sequences ─────────────────────────────────────────────────
        t["rga"] = _Spec(
            "Replicated Growable Array (RGA)", "sequence",
            "Linked-list sequence; inserts reference left neighbour; "
            "ordered by (timestamp, replica_id).",
            "insert at position via (origin_ts, replica_id)", S, "O(n)",
            True, True, ["collaborative text editors", "shared documents"],
            ["Tombstones accumulate; compaction required."],
            ["RGA for non-collaborative append-only logs"],
            {"insert": W, "delete": W, "merge": M, "read": R},
            "Linked list of (id, origin, value, tombstone)")

        t["treedoc"] = _Spec(
            "Treedoc", "sequence",
            "Binary-tree sequence; insertions at tree-path positions.",
            "tree union; concurrent siblings by timestamp", S, "O(log n)",
            True, True, ["collaborative editing", "ordered lists"],
            ["Tree can become unbalanced."],
            ["Deep concurrent inserts at same position"],
            {"insert": W, "delete": W, "merge": M, "read": R},
            "Binary tree of (path, timestamp, value, tombstone)")

        t["logoot"] = _Spec(
            "Logoot", "sequence",
            "Fractional-index sequence; integer-list positions between neighbours.",
            "sorted by fractional index; new indices between neighbours", S, "O(n log n)",
            True, True, ["ordered data", "shared lists", "doc editing"],
            ["Index space grows; compaction needed."],
            ["Unbounded concurrent inserts without index caps"],
            {"insert": W, "delete": W, "merge": M, "read": R},
            "List of (fractional_index, value, tombstone)")

        t["yjs-sequence"] = _Spec(
            "Yjs-style Sequence", "sequence",
            "Yjs-inspired: (client_id, clock) IDs, left/right refs, "
            "integrated GC, block compression.",
            "integrate remote items via (client_id, clock) IDs", S, "O(n)",
            True, True, ["Yjs editors", "whiteboards", "rich text"],
            ["High impl complexity; relies on GC."],
            ["Port without block compression and GC"],
            {"insert": W, "delete": W, "merge": M, "read": R, "gc": W},
            "Linked list of Item(id, left, right, content, deleted)")


def create_coordinator() -> CRDTCoordinator:
    """Factory for a default :class:`CRDTCoordinator` instance."""
    return CRDTCoordinator()
