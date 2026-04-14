#!/usr/bin/env python3
"""Comprehensive tests for the three new subsystems of smartcrdt-git-agent.

Covers DriftLogIndexer, RepoCartographer, and NecrosisDetector with
focus on correctness, edge cases, and CRDT semantics.

Python 3.9+ stdlib only.
"""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone, timedelta

# Ensure parent directory is on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift_log_indexer import DriftLogIndexer, VectorClock
from repo_cartographer import RepoCartographer
from necrosis_detector import (
    NecrosisDetector,
    AgentState,
    AgentHeartbeat,
    _LWWRegister,
    _PNCounter,
)


# ======================================================================
# TestDriftLogIndexer
# ======================================================================


class TestDriftLogIndexer(unittest.TestCase):
    """Tests for the DriftLogIndexer and VectorClock subsystem."""

    def setUp(self):
        self.log = DriftLogIndexer(agent_id="agent-1", log_id="test-log")

    # ---- record_event ------------------------------------------------

    def test_record_event_basic(self):
        entry = self.log.record_event("heartbeat", "agent-1")
        self.assertIn("event_id", entry)
        self.assertIn("timestamp", entry)
        self.assertEqual(entry["agent_id"], "agent-1")
        self.assertEqual(entry["event_type"], "heartbeat")
        self.assertIn("vector_clock", entry)
        self.assertEqual(entry["log_id"], "test-log")
        self.assertEqual(self.log.size, 1)

    def test_record_event_unique_ids(self):
        e1 = self.log.record_event("heartbeat", "agent-1")
        e2 = self.log.record_event("task_claimed", "agent-1")
        self.assertNotEqual(e1["event_id"], e2["event_id"])

    def test_record_event_invalid_type(self):
        with self.assertRaises(ValueError):
            self.log.record_event("nonexistent_type", "agent-1")

    def test_record_event_no_agent_id(self):
        bare_log = DriftLogIndexer()
        with self.assertRaises(ValueError):
            bare_log.record_event("heartbeat")

    def test_record_event_with_payload(self):
        entry = self.log.record_event(
            "bottle_sent", "agent-1", {"to": "agent-2", "msg": "hello"}
        )
        self.assertEqual(entry["payload"]["to"], "agent-2")

    def test_record_event_with_parent_ids(self):
        e1 = self.log.record_event("heartbeat", "agent-1")
        e2 = self.log.record_event(
            "task_claimed", "agent-1", parent_ids=[e1["event_id"]]
        )
        self.assertEqual(e2["causality_chain"], [e1["event_id"]])

    def test_record_event_parent_not_found(self):
        with self.assertRaises(ValueError):
            self.log.record_event(
                "heartbeat", "agent-1", parent_ids=["nonexistent-id"]
            )

    def test_record_event_vector_clock_increments(self):
        self.log.record_event("heartbeat", "agent-1")
        self.log.record_event("heartbeat", "agent-1")
        vc = self.log._vector_clock
        self.assertEqual(vc.get("agent-1"), 2)

    # ---- merge --------------------------------------------------------

    def test_merge_basic(self):
        remote = DriftLogIndexer(agent_id="agent-2", log_id="remote")
        remote.record_event("heartbeat", "agent-2")
        new_entries = self.log.merge(remote)
        self.assertEqual(len(new_entries), 1)
        self.assertEqual(self.log.size, 1)

    def test_merge_idempotent(self):
        remote = DriftLogIndexer(agent_id="agent-2", log_id="remote")
        remote.record_event("heartbeat", "agent-2")
        self.log.merge(remote)
        new_entries = self.log.merge(remote)
        self.assertEqual(len(new_entries), 0)
        self.assertEqual(self.log.size, 1)

    def test_merge_preserves_local_entries(self):
        self.log.record_event("heartbeat", "agent-1")
        remote = DriftLogIndexer(agent_id="agent-2", log_id="remote")
        remote.record_event("heartbeat", "agent-2")
        self.log.merge(remote)
        self.assertEqual(self.log.size, 2)

    def test_merge_vector_clock_merged(self):
        self.log.record_event("heartbeat", "agent-1")
        remote = DriftLogIndexer(agent_id="agent-2", log_id="remote")
        remote.record_event("heartbeat", "agent-2")
        self.log.merge(remote)
        vc = self.log._vector_clock
        self.assertEqual(vc.get("agent-1"), 1)
        self.assertEqual(vc.get("agent-2"), 1)

    # ---- query --------------------------------------------------------

    def test_query_by_agent_id(self):
        self.log.record_event("heartbeat", "agent-1")
        self.log.record_event("heartbeat", "agent-2")
        results = self.log.query(agent_id="agent-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["agent_id"], "agent-1")

    def test_query_by_event_type(self):
        self.log.record_event("heartbeat", "agent-1")
        self.log.record_event("task_claimed", "agent-1")
        results = self.log.query(event_type="heartbeat")
        self.assertEqual(len(results), 1)

    def test_query_time_range(self):
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=2)).isoformat()
        new_ts = (now + timedelta(hours=1)).isoformat()
        entry = self.log.record_event("heartbeat", "agent-1")
        results = self.log.query(since=old_ts, until=new_ts)
        self.assertTrue(len(results) >= 1)

    def test_query_limit(self):
        for _ in range(5):
            self.log.record_event("heartbeat", "agent-1")
        results = self.log.query(limit=2)
        self.assertEqual(len(results), 2)

    def test_query_empty_log(self):
        results = self.log.query()
        self.assertEqual(results, [])

    def test_query_invalid_event_type(self):
        with self.assertRaises(ValueError):
            self.log.query(event_type="invalid")

    def test_query_negative_limit(self):
        with self.assertRaises(ValueError):
            self.log.query(limit=-1)

    # ---- get_drift_metrics --------------------------------------------

    def test_get_drift_metrics_empty(self):
        m = self.log.get_drift_metrics()
        self.assertEqual(m["total_events"], 0)
        self.assertEqual(m["unique_agents"], 0)
        self.assertEqual(m["convergence_rate"], 0.0)
        self.assertIsNone(m["most_active_agent"])

    def test_get_drift_metrics_with_events(self):
        self.log.record_event("heartbeat", "agent-1")
        self.log.record_event("task_claimed", "agent-2")
        m = self.log.get_drift_metrics()
        self.assertEqual(m["total_events"], 2)
        self.assertEqual(m["unique_agents"], 2)
        self.assertIn("agent-1", m["agent_event_counts"])
        self.assertIn("agent-2", m["agent_event_counts"])
        self.assertIsNotNone(m["most_active_agent"])

    # ---- export -------------------------------------------------------

    def test_export_json_valid(self):
        self.log.record_event("heartbeat", "agent-1")
        raw = self.log.export_json()
        data = json.loads(raw)
        self.assertEqual(data["log_id"], "test-log")
        self.assertEqual(data["total_entries"], 1)
        self.assertEqual(len(data["entries"]), 1)

    def test_export_markdown_contains_header(self):
        self.log.record_event("heartbeat", "agent-1")
        md = self.log.export_markdown(summary_only=True)
        self.assertIn("# Drift Log:", md)
        self.assertIn("Fleet Overview", md)

    def test_export_markdown_summary_only(self):
        self.log.record_event("heartbeat", "agent-1")
        md_summary = self.log.export_markdown(summary_only=True)
        # Summary mode should NOT contain the full event log section.
        self.assertNotIn("## Event Log", md_summary)

    def test_export_markdown_full(self):
        self.log.record_event("heartbeat", "agent-1")
        md_full = self.log.export_markdown(summary_only=False)
        self.assertIn("## Event Log", md_full)

    # ---- get_causality_chain ------------------------------------------

    def test_get_causality_chain_traces_parents(self):
        e1 = self.log.record_event("heartbeat", "agent-1")
        e2 = self.log.record_event(
            "task_claimed", "agent-1", parent_ids=[e1["event_id"]]
        )
        chain = self.log.get_causality_chain(e2["event_id"])
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["event_id"], e1["event_id"])
        self.assertEqual(chain[1]["event_id"], e2["event_id"])

    def test_get_causality_chain_unknown_event(self):
        chain = self.log.get_causality_chain("nonexistent")
        self.assertEqual(chain, [])

    def test_get_causality_chain_no_parents(self):
        e1 = self.log.record_event("heartbeat", "agent-1")
        chain = self.log.get_causality_chain(e1["event_id"])
        self.assertEqual(len(chain), 1)

    # ---- detect_anomalies ---------------------------------------------

    def test_detect_anomalies_empty(self):
        anomalies = self.log.detect_anomalies()
        self.assertEqual(anomalies, [])

    def test_detect_anomalies_stale_heartbeat(self):
        # Record a heartbeat, then a much-later event so the heartbeat
        # appears stale.
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        entry = self.log.record_event("heartbeat", "agent-1")
        # Force the heartbeat timestamp into the past.
        entry["timestamp"] = old_ts

        # Add a recent event so the window boundary is "now".
        self.log.record_event("task_claimed", "agent-1")

        anomalies = self.log.detect_anomalies()
        types = [a["anomaly_type"] for a in anomalies]
        self.assertIn("stale_heartbeat", types)

    def test_detect_anomalies_necrosis_without_recovery(self):
        self.log.record_event("necrosis_alert", "agent-1")
        anomalies = self.log.detect_anomalies()
        types = [a["anomaly_type"] for a in anomalies]
        self.assertIn("necrosis_without_recovery", types)

    def test_detect_anomalies_silent_agent(self):
        # Agent-2 has an old event, agent-1 has a recent event.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        old_entry = self.log.record_event("heartbeat", "agent-2")
        old_entry["timestamp"] = old_ts

        # Sort entries so newest is last (detect_anomalies reads _entries[-1]).
        self.log._entries.sort(key=lambda e: e["timestamp"])

        self.log.record_event("heartbeat", "agent-1")
        anomalies = self.log.detect_anomalies(window_seconds=3600)
        types = [a["anomaly_type"] for a in anomalies]
        self.assertIn("silent_agent", types)

    # ---- VectorClock --------------------------------------------------

    def test_vector_clock_increment(self):
        vc = VectorClock()
        vc.increment("a")
        vc.increment("a")
        self.assertEqual(vc.get("a"), 2)

    def test_vector_clock_compare_equal(self):
        vc1 = VectorClock({"a": 1, "b": 2})
        vc2 = VectorClock({"a": 1, "b": 2})
        self.assertEqual(vc1.compare(vc2), "equal")

    def test_vector_clock_compare_before(self):
        vc1 = VectorClock({"a": 1})
        vc2 = VectorClock({"a": 2})
        self.assertEqual(vc1.compare(vc2), "before")

    def test_vector_clock_compare_after(self):
        vc1 = VectorClock({"a": 3})
        vc2 = VectorClock({"a": 1})
        self.assertEqual(vc1.compare(vc2), "after")

    def test_vector_clock_compare_concurrent(self):
        vc1 = VectorClock({"a": 1, "b": 0})
        vc2 = VectorClock({"a": 0, "b": 1})
        self.assertEqual(vc1.compare(vc2), "concurrent")

    def test_vector_clock_happens_before(self):
        vc1 = VectorClock({"a": 1})
        vc2 = VectorClock({"a": 2})
        self.assertTrue(vc1.happens_before(vc2))
        self.assertFalse(vc2.happens_before(vc1))

    def test_vector_clock_merge(self):
        vc1 = VectorClock({"a": 3, "b": 1})
        vc2 = VectorClock({"a": 1, "b": 4, "c": 2})
        vc1.merge(vc2)
        self.assertEqual(vc1.get("a"), 3)
        self.assertEqual(vc1.get("b"), 4)
        self.assertEqual(vc1.get("c"), 2)

    def test_vector_clock_copy_independence(self):
        vc1 = VectorClock({"a": 1})
        vc2 = vc1.copy()
        vc2.increment("a")
        self.assertEqual(vc1.get("a"), 1)
        self.assertEqual(vc2.get("a"), 2)

    # ---- Edge cases ---------------------------------------------------

    def test_single_event_log(self):
        e = self.log.record_event("heartbeat", "agent-1")
        self.assertEqual(self.log.size, 1)
        metrics = self.log.get_drift_metrics()
        self.assertEqual(metrics["total_events"], 1)
        chain = self.log.get_causality_chain(e["event_id"])
        self.assertEqual(len(chain), 1)

    def test_concurrent_events_merge_ordering(self):
        """Two agent logs with concurrent events merge without loss."""
        log_a = DriftLogIndexer(agent_id="agent-a", log_id="log-a")
        log_b = DriftLogIndexer(agent_id="agent-b", log_id="log-b")
        log_a.record_event("heartbeat", "agent-a")
        log_b.record_event("heartbeat", "agent-b")
        new = log_a.merge(log_b)
        self.assertEqual(len(new), 1)
        self.assertEqual(log_a.size, 2)


# ======================================================================
# TestRepoCartographer
# ======================================================================


class TestRepoCartographer(unittest.TestCase):
    """Tests for the RepoCartographer subsystem."""

    def setUp(self):
        self.cart = RepoCartographer()

    # ---- index_repo ---------------------------------------------------

    def test_index_repo_basic(self):
        result = self.cart.index_repo("crdt-core", {"language": "python"})
        self.assertTrue(result["indexed"])
        self.assertEqual(result["repo"], "crdt-core")
        self.assertEqual(self.cart.get_repo_count(), 1)

    def test_index_repo_default_metadata(self):
        self.cart.index_repo("solo-repo")
        repos = self.cart.get_all_repos()
        self.assertIn("solo-repo", repos)

    def test_index_repo_merge_metadata(self):
        self.cart.index_repo("repo-x", {"language": "python", "test_count": 10})
        self.cart.index_repo("repo-x", {"test_count": 20})
        health = self.cart.get_repo_health("repo-x")
        self.assertEqual(health["test_count"], 20)
        self.assertEqual(health["language"], "python")

    # ---- add_dependency / remove_dependency ---------------------------

    def test_add_dependency_basic(self):
        result = self.cart.add_dependency("app", "lib")
        self.assertTrue(result["added"])
        self.assertEqual(result["from"], "app")
        self.assertEqual(result["to"], "lib")
        self.assertEqual(self.cart.get_edge_count(), 1)

    def test_add_dependency_auto_indexes(self):
        self.cart.add_dependency("new-app", "new-lib")
        self.assertIn("new-app", self.cart.get_all_repos())
        self.assertIn("new-lib", self.cart.get_all_repos())

    def test_add_dependency_self_rejected(self):
        with self.assertRaises(ValueError):
            self.cart.add_dependency("repo", "repo")

    def test_add_dependency_invalid_strength(self):
        with self.assertRaises(ValueError):
            self.cart.add_dependency("a", "b", strength="invalid")

    def test_add_dependency_weak_strength(self):
        self.cart.add_dependency("a", "b", strength="weak")
        deps = self.cart.get_dependencies("a")
        self.assertEqual(deps["b"], "weak")

    def test_remove_dependency_existing(self):
        self.cart.add_dependency("app", "lib")
        result = self.cart.remove_dependency("app", "lib")
        self.assertTrue(result["removed"])
        self.assertEqual(self.cart.get_edge_count(), 0)

    def test_remove_dependency_nonexistent(self):
        result = self.cart.remove_dependency("app", "lib")
        self.assertFalse(result["removed"])

    # ---- get_impact_analysis ------------------------------------------

    def test_get_impact_analysis_basic(self):
        self.cart.add_dependency("app", "lib")
        impact = self.cart.get_impact_analysis("lib")
        self.assertIn("app", impact["affected_repos"])
        self.assertEqual(impact["total_affected"], 1)

    def test_get_impact_analysis_depth(self):
        self.cart.add_dependency("app", "lib")
        self.cart.add_dependency("lib", "core")
        # Impact of core should reach lib (depth 1) and app (depth 2).
        impact = self.cart.get_impact_analysis("core")
        self.assertIn("lib", impact["affected_repos"])
        self.assertIn("app", impact["affected_repos"])
        self.assertEqual(impact["total_affected"], 2)

    def test_get_impact_analysis_unknown_repo(self):
        impact = self.cart.get_impact_analysis("ghost")
        self.assertEqual(impact["total_affected"], 0)
        self.assertIn("error", impact)

    # ---- get_dependency_chain -----------------------------------------

    def test_get_dependency_chain_basic(self):
        self.cart.add_dependency("app", "lib")
        self.cart.add_dependency("lib", "core")
        chain = self.cart.get_dependency_chain("app")
        self.assertEqual(chain["total_dependencies"], 2)
        self.assertIn("lib", chain["direct_dependencies"])
        self.assertIn("core", chain["transitive_dependencies"])

    def test_get_dependency_chain_unknown(self):
        chain = self.cart.get_dependency_chain("ghost")
        self.assertEqual(chain["total_dependencies"], 0)

    # ---- detect_cycles -----------------------------------------------

    def test_detect_cycles_none(self):
        self.cart.add_dependency("app", "lib")
        self.cart.add_dependency("lib", "core")
        self.assertEqual(self.cart.detect_cycles(), [])

    def test_detect_cycles_exists(self):
        self.cart.add_dependency("a", "b")
        self.cart.add_dependency("b", "a")
        cycles = self.cart.detect_cycles()
        self.assertTrue(len(cycles) > 0)
        # The cycle should contain both a and b.
        members = set(cycles[0])
        self.assertEqual(members, {"a", "b"})

    # ---- find_orphans -------------------------------------------------

    def test_find_orphans_none(self):
        self.cart.add_dependency("app", "lib")
        orphans = self.cart.find_orphans()
        self.assertEqual(orphans, [])

    def test_find_orphans_disconnected(self):
        self.cart.index_repo("standalone")
        self.cart.add_dependency("a", "b")
        orphans = self.cart.find_orphans()
        self.assertIn("standalone", orphans)

    # ---- topological_sort ---------------------------------------------

    def test_topological_sort_basic(self):
        self.cart.add_dependency("app", "lib")
        self.cart.add_dependency("lib", "core")
        order = self.cart.topological_sort()
        self.assertTrue(order.index("core") < order.index("lib"))
        self.assertTrue(order.index("lib") < order.index("app"))

    def test_topological_sort_with_cycles(self):
        self.cart.add_dependency("a", "b")
        self.cart.add_dependency("b", "a")
        self.cart.index_repo("c")
        order = self.cart.topological_sort()
        # c should be ordered, a and b at end.
        self.assertIn("c", order)
        # All repos should be present.
        self.assertEqual(len(order), 3)

    # ---- compute_fleet_health -----------------------------------------

    def test_compute_fleet_health_empty(self):
        h = self.cart.compute_fleet_health()
        self.assertEqual(h["total_repos"], 0)
        self.assertEqual(h["fleet_health_score"], 0.0)

    def test_compute_fleet_health_returns_zero_to_one(self):
        self.cart.index_repo("repo-a", {"test_count": 50, "has_tests": True})
        h = self.cart.compute_fleet_health()
        score = h["fleet_health_score"]
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_compute_fleet_health_distribution_keys(self):
        self.cart.index_repo("r1", {"test_count": 50})
        h = self.cart.compute_fleet_health()
        dist = h["distribution"]
        self.assertIn("healthy", dist)
        self.assertIn("warning", dist)
        self.assertIn("critical", dist)

    # ---- get_cluster_map ----------------------------------------------

    def test_get_cluster_map_basic(self):
        self.cart.add_dependency("app", "lib")
        cm = self.cart.get_cluster_map()
        self.assertGreaterEqual(cm["total_clusters"], 1)
        self.assertIsNotNone(cm["largest_cluster"])

    def test_get_cluster_map_disconnected(self):
        self.cart.index_repo("iso-a")
        self.cart.index_repo("iso-b")
        cm = self.cart.get_cluster_map()
        self.assertEqual(cm["singleton_count"], 2)

    # ---- get_repo_health ----------------------------------------------

    def test_get_repo_health_basic(self):
        self.cart.index_repo("repo-a", {"test_count": 100, "has_tests": True})
        rh = self.cart.get_repo_health("repo-a")
        self.assertIn("health_score", rh)
        self.assertIn("rating", rh)
        self.assertGreaterEqual(rh["health_score"], 0.0)
        self.assertLessEqual(rh["health_score"], 1.0)

    def test_get_repo_health_unknown(self):
        rh = self.cart.get_repo_health("ghost")
        self.assertEqual(rh["health_score"], 0.0)
        self.assertIn("error", rh)

    def test_get_repo_health_rating_ranges(self):
        self.cart.index_repo("r1")
        rh = self.cart.get_repo_health("r1")
        self.assertIn(rh["rating"], ("healthy", "warning", "critical"))

    # ---- suggest_merge_order ------------------------------------------

    def test_suggest_merge_order_basic(self):
        self.cart.add_dependency("app", "lib")
        self.cart.add_dependency("lib", "core")
        order = self.cart.suggest_merge_order(["app", "core"])
        # core should come before app.
        core_idx = next(
            i for i, r in enumerate(order) if r in ("core", "~core")
        )
        app_idx = next(
            i for i, r in enumerate(order) if r == "app"
        )
        self.assertLess(core_idx, app_idx)

    def test_suggest_merge_order_empty(self):
        self.assertEqual(self.cart.suggest_merge_order([]), [])

    def test_suggest_merge_order_unknown_repos(self):
        result = self.cart.suggest_merge_order(["ghost"])
        self.assertIn("ghost", result)

    # ---- Edge cases ---------------------------------------------------

    def test_edge_case_empty_graph(self):
        self.assertEqual(self.cart.get_repo_count(), 0)
        self.assertEqual(self.cart.get_edge_count(), 0)
        self.assertEqual(self.cart.find_orphans(), [])
        self.assertEqual(self.cart.detect_cycles(), [])
        self.assertEqual(self.cart.topological_sort(), [])

    def test_edge_case_single_repo(self):
        self.cart.index_repo("solo")
        self.assertEqual(self.cart.get_repo_count(), 1)
        self.assertIn("solo", self.cart.find_orphans())
        self.assertEqual(self.cart.topological_sort(), ["solo"])


# ======================================================================
# TestNecrosisDetector
# ======================================================================


class TestNecrosisDetector(unittest.TestCase):
    """Tests for the NecrosisDetector subsystem."""

    def setUp(self):
        self.nd = NecrosisDetector()
        self.now = time.time()

    def _heartbeat(self, agent_id, timestamp=None, **kwargs):
        """Helper: build and record a heartbeat."""
        hb = {
            "agent_id": agent_id,
            "timestamp": timestamp or self.now,
            "test_count": kwargs.get("test_count", 100),
            "tasks_completed": kwargs.get("tasks_completed", 10),
            "repo_count": kwargs.get("repo_count", 3),
            "status": kwargs.get("status", "active"),
        }
        return self.nd.record_heartbeat(hb)

    # ---- record_heartbeat ---------------------------------------------

    def test_record_heartbeat_basic(self):
        result = self._heartbeat("agent-1")
        self.assertEqual(result["agent_id"], "agent-1")
        self.assertEqual(result["current_state"], "healthy")
        # New agents start in HEALTHY state, so first heartbeat has no transition.
        self.assertFalse(result["transitioned"])

    def test_record_heartbeat_second_heartbeat_no_transition(self):
        self._heartbeat("agent-1")
        result = self._heartbeat("agent-1")
        self.assertFalse(result["transitioned"])
        self.assertEqual(result["current_state"], "healthy")

    def test_record_heartbeat_new_agent_created(self):
        result = self._heartbeat("brand-new")
        state = self.nd.get_agent_state("brand-new")
        self.assertEqual(state["state"], "healthy")
        self.assertEqual(state["test_count"], 100)

    # ---- get_agent_state ----------------------------------------------

    def test_get_agent_state_unknown(self):
        state = self.nd.get_agent_state("ghost")
        self.assertEqual(state["state"], "unknown")

    def test_get_agent_state_known(self):
        self._heartbeat("agent-1")
        state = self.nd.get_agent_state("agent-1")
        self.assertEqual(state["agent_id"], "agent-1")
        self.assertEqual(state["state"], "healthy")
        self.assertIn("silence_seconds", state)
        self.assertIn("test_count", state)
        self.assertIn("test_counter_value", state)

    # ---- get_fleet_pulse ----------------------------------------------

    def test_get_fleet_pulse_empty(self):
        pulse = self.nd.get_fleet_pulse()
        self.assertEqual(pulse["total_agents"], 0)
        self.assertEqual(pulse["fleet_health_score"], 0.0)
        self.assertEqual(pulse["healthy_count"], 0)

    def test_get_fleet_pulse_with_agents(self):
        self._heartbeat("a1")
        self._heartbeat("a2")
        pulse = self.nd.get_fleet_pulse()
        self.assertEqual(pulse["total_agents"], 2)
        self.assertEqual(pulse["healthy_count"], 2)
        self.assertGreaterEqual(pulse["fleet_health_score"], 0.0)
        self.assertLessEqual(pulse["fleet_health_score"], 1.0)

    # ---- beachcomb_scan -----------------------------------------------

    def test_beachcomb_scan_empty(self):
        anomalies = self.nd.beachcomb_scan()
        self.assertEqual(anomalies, [])

    def test_beachcomb_scan_all_healthy(self):
        self._heartbeat("a1")
        anomalies = self.nd.beachcomb_scan()
        self.assertEqual(anomalies, [])

    def test_beachcomb_scan_degraded_agent(self):
        # Record a heartbeat far in the past to trigger degraded state.
        old_ts = self.now - 1800  # exactly at degraded threshold
        self.nd._thresholds["threshold_heartbeat_degraded"] = 60  # 1 min
        old_hb = {
            "agent_id": "slow-agent",
            "timestamp": old_ts - 120,  # 2 min ago
            "test_count": 50,
            "tasks_completed": 5,
            "repo_count": 1,
            "status": "active",
        }
        self.nd.record_heartbeat(old_hb)
        # Force last_seen_ts to the old time so effective_state evaluates.
        rec = self.nd._agents["slow-agent"]
        rec.last_seen_ts = old_ts - 120

        anomalies = self.nd.beachcomb_scan()
        types = [a["anomaly_type"] for a in anomalies]
        self.assertTrue(
            any("degraded" in t or "critical" in t for t in types),
            f"Expected degraded/critical anomaly, got: {types}",
        )

    # ---- get_alerts ---------------------------------------------------

    def test_get_alerts_empty(self):
        alerts = self.nd.get_alerts()
        self.assertEqual(alerts, [])

    def test_get_alerts_filter_severity(self):
        # Trigger a test attrition alert.
        self._heartbeat("a1", test_count=200)
        self._heartbeat("a1", test_count=100)  # 50% drop -> above default 10%
        all_alerts = self.nd.get_alerts()
        self.assertTrue(len(all_alerts) > 0)
        warning_alerts = self.nd.get_alerts(severity="warning")
        for a in warning_alerts:
            self.assertEqual(a["severity"], "warning")

    # ---- suggest_tow --------------------------------------------------

    def test_suggest_tow_unknown_agent(self):
        result = self.nd.suggest_tow("ghost")
        self.assertIsNone(result["tow_candidate"])
        self.assertEqual(result["failing_state"], "unknown")

    def test_suggest_tow_healthy_agent_no_tow(self):
        self._heartbeat("healthy-agent")
        result = self.nd.suggest_tow("healthy-agent")
        self.assertIsNone(result["tow_candidate"])

    def test_suggest_tow_finds_healthy_candidate(self):
        # Make a healthy candidate available.
        self._heartbeat("rescuer")
        # Make a failing agent (old heartbeat).
        old_ts = self.now - 7200  # beyond critical threshold
        self.nd._thresholds["threshold_heartbeat_critical"] = 60
        old_hb = {
            "agent_id": "failing",
            "timestamp": old_ts - 120,
            "test_count": 10,
            "tasks_completed": 0,
            "repo_count": 1,
            "status": "error",
        }
        self.nd.record_heartbeat(old_hb)
        rec = self.nd._agents["failing"]
        rec.last_seen_ts = old_ts - 120

        result = self.nd.suggest_tow("failing")
        self.assertEqual(result["tow_candidate"], "rescuer")

    # ---- get_forensics ------------------------------------------------

    def test_get_forensics_unknown(self):
        f = self.nd.get_forensics("ghost")
        self.assertEqual(f["current_state"], "unknown")
        self.assertEqual(f["transition_count"], 0)
        self.assertEqual(f["transitions"], [])

    def test_get_forensics_with_transitions(self):
        # First heartbeat on a new agent: no transition (already HEALTHY).
        self._heartbeat("a1")
        f = self.nd.get_forensics("a1")
        self.assertEqual(f["transition_count"], 0)

        # Now force agent into CRITICAL, then send fresh heartbeat
        # to trigger a recovery transition (CRITICAL -> HEALTHY).
        rec = self.nd._agents["a1"]
        rec.state = AgentState.CRITICAL
        self._heartbeat("a1")
        f = self.nd.get_forensics("a1")
        self.assertGreaterEqual(f["transition_count"], 1)
        self.assertTrue(len(f["transitions"]) > 0)
        self.assertIn("from_state", f["transitions"][0])
        self.assertIn("to_state", f["transitions"][0])

    # ---- get_all_agent_states -----------------------------------------

    def test_get_all_agent_states(self):
        self._heartbeat("a1")
        self._heartbeat("a2")
        states = self.nd.get_all_agent_states()
        self.assertEqual(len(states), 2)
        self.assertIn("a1", states)
        self.assertIn("a2", states)

    # ---- configure ----------------------------------------------------

    def test_configure_updates_thresholds(self):
        result = self.nd.configure({"threshold_heartbeat_degraded": 600})
        self.assertIn("threshold_heartbeat_degraded", result["updated_keys"])
        self.assertAlmostEqual(
            result["current_thresholds"]["threshold_heartbeat_degraded"], 600.0
        )

    def test_configure_ignores_unknown_keys(self):
        result = self.nd.configure({"bogus_key": 42})
        self.assertEqual(result["updated_keys"], [])

    def test_configure_no_change(self):
        current = self.nd.thresholds
        result = self.nd.configure(
            {"threshold_heartbeat_degraded": current["threshold_heartbeat_degraded"]}
        )
        self.assertEqual(result["updated_keys"], [])

    # ---- export_report ------------------------------------------------

    def test_export_report_contains_header(self):
        self._heartbeat("a1")
        report = self.nd.export_report()
        self.assertIn("# Fleet Health Report", report)
        self.assertIn("Fleet Health Score", report)

    # ---- State transitions --------------------------------------------

    def test_state_transition_healthy_on_heartbeat(self):
        result = self._heartbeat("a1")
        self.assertEqual(result["previous_state"], "healthy")
        self.assertEqual(result["current_state"], "healthy")

    # ---- Recovery -----------------------------------------------------

    def test_recovery_heartbeat_resets_to_healthy(self):
        # Create agent with old heartbeat to move to degraded/critical.
        old_ts = self.now - 7200
        self.nd._thresholds["threshold_heartbeat_critical"] = 60
        old_hb = {
            "agent_id": "recovering",
            "timestamp": old_ts - 120,
            "test_count": 50,
            "tasks_completed": 5,
            "repo_count": 2,
            "status": "active",
        }
        self.nd.record_heartbeat(old_hb)
        rec = self.nd._agents["recovering"]
        rec.last_seen_ts = old_ts - 120
        rec.state = AgentState.CRITICAL

        # Now send a fresh heartbeat.
        result = self._heartbeat("recovering")
        self.assertEqual(result["current_state"], "healthy")

    # ---- LWW Register -------------------------------------------------

    def test_lww_register_set_and_get(self):
        reg = _LWWRegister("test")
        reg.set("value-a", 1.0, "node-1")
        self.assertEqual(reg.get(), "value-a")

    def test_lww_register_later_timestamp_wins(self):
        reg = _LWWRegister("test")
        reg.set("old", 1.0, "node-1")
        reg.set("new", 2.0, "node-2")
        self.assertEqual(reg.get(), "new")

    def test_lww_register_same_timestamp_higher_node_wins(self):
        reg = _LWWRegister("test")
        reg.set("low", 5.0, "aaa")
        reg.set("high", 5.0, "zzz")
        self.assertEqual(reg.get(), "high")

    def test_lww_register_merge(self):
        reg_a = _LWWRegister("test")
        reg_a.set("value-a", 3.0, "node-a")
        reg_b = _LWWRegister("test")
        reg_b.set("value-b", 5.0, "node-b")
        reg_a.merge(reg_b)
        self.assertEqual(reg_a.get(), "value-b")

    # ---- PN Counter ---------------------------------------------------

    def test_pn_counter_basic(self):
        ctr = _PNCounter("test")
        ctr.increment(10)
        ctr.decrement(3)
        self.assertEqual(ctr.value(), 7)

    def test_pn_counter_merge(self):
        ctr_a = _PNCounter("test")
        ctr_a.increment(5)
        ctr_a.decrement(2)
        ctr_b = _PNCounter("test")
        ctr_b.increment(10)
        ctr_a.merge(ctr_b)
        # a: +5-2, b: +10 => merged: +15-2 = 13
        self.assertEqual(ctr_a.value(), 13)

    def test_pn_counter_no_negative_delta(self):
        ctr = _PNCounter("test")
        ctr.decrement(-5)  # negative delta should be ignored
        self.assertEqual(ctr.value(), 0)

    # ---- Simultaneous alerts ------------------------------------------

    def test_simultaneous_alerts_multiple_agents(self):
        # Trigger test attrition on two agents simultaneously.
        self._heartbeat("a1", test_count=200)
        self._heartbeat("a2", test_count=200)
        self._heartbeat("a1", test_count=100)
        self._heartbeat("a2", test_count=100)
        alerts = self.nd.get_alerts()
        # Each agent should have at least one alert.
        agents_with_alerts = {a["agent_id"] for a in alerts}
        self.assertIn("a1", agents_with_alerts)
        self.assertIn("a2", agents_with_alerts)

    # ---- Edge cases ---------------------------------------------------

    def test_no_heartbeats_fleet_pulse(self):
        pulse = self.nd.get_fleet_pulse()
        self.assertEqual(pulse["fleet_health_score"], 0.0)
        self.assertEqual(pulse["total_agents"], 0)

    def test_single_heartbeat_all_states(self):
        result = self._heartbeat("lone-wolf")
        self.assertEqual(result["current_state"], "healthy")
        state = self.nd.get_agent_state("lone-wolf")
        self.assertEqual(state["state"], "healthy")
        forensics = self.nd.get_forensics("lone-wolf")
        # New agent starts HEALTHY, so no transition on first heartbeat.
        self.assertEqual(forensics["transition_count"], 0)


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    unittest.main()
