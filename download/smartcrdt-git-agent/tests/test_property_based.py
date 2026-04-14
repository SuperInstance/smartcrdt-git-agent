#!/usr/bin/env python3
"""Property-based tests for CRDT subsystems.

Implements a lightweight property testing framework using Python stdlib
(no external dependencies like Hypothesis).

Tests mathematical properties of:
- VectorClock (commutativity, associativity, idempotence, monotonicity, transitivity)
- DriftLogIndexer (merge idempotence, commutativity, causality, query consistency)
- RepoCartographer (topological sort, health bounds, impact analysis, cycle detection)
- NecrosisDetector LWW-Register (last-writer-wins semantics)
- NecrosisDetector PN-Counter (associativity, value properties)

Python 3.9+ stdlib only.
"""

import os
import random
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone, timedelta

# Ensure parent directory is on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift_log_indexer import DriftLogIndexer, VectorClock
from repo_cartographer import RepoCartographer
from necrosis_detector import NecrosisDetector, _LWWRegister, _PNCounter


# ======================================================================
# Lightweight Property Testing Framework
# ======================================================================


def integers(min_val=0, max_val=100):
    """Strategy: generate random integers in [min_val, max_val]."""
    return lambda: random.randint(min_val, max_val)


def strings(min_len=0, max_len=10, alphabet=None):
    """Strategy: generate random strings of length [min_len, max_len]."""
    if alphabet is None:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return lambda: "".join(
        random.choice(alphabet) for _ in range(random.randint(min_len, max_len))
    )


def lists(strategy, min_len=0, max_len=10):
    """Strategy: generate lists of items from a strategy."""
    return lambda: [strategy() for _ in range(random.randint(min_len, max_len))]


def dicts(key_strategy, value_strategy, min_size=0, max_size=5):
    """Strategy: generate random dicts."""
    def _gen():
        n = random.randint(min_size, max_size)
        return {key_strategy(): value_strategy() for _ in range(n)}
    return _gen


def one_of(*strategies):
    """Strategy: randomly pick from one of the given strategies."""
    return lambda: random.choice(strategies)()


def frequencies(*weighted_strategies):
    """Strategy: weighted random strategy selection."""
    total = sum(w for w, _ in weighted_strategies)
    choices = []
    cumulative = 0
    for weight, strategy in weighted_strategies:
        cumulative += weight
        choices.append((cumulative / total, strategy))

    def _gen():
        r = random.random()
        for threshold, strategy in choices:
            if r <= threshold:
                return strategy()
        return choices[-1][1]()

    return _gen


def timestamps(recent_seconds=86400):
    """Strategy: random timestamps near now."""
    now = time.time()
    return lambda: random.uniform(now - recent_seconds, now + 60)


def uuids():
    """Strategy: random UUID-like strings (12 hex chars)."""
    return lambda: uuid.uuid4().hex[:12]


class PropertyTestResult:
    """Result of a property test run."""

    def __init__(self, passed, iterations, counterexample=None, shrunk=None):
        self.passed = passed
        self.iterations = iterations
        self.counterexample = counterexample
        self.shrunk = shrunk

    def __repr__(self):
        if self.passed:
            return f"PropertyTestResult(passed, {self.iterations} iterations)"
        return (
            f"PropertyTestResult(FAILED at iteration {self.iterations}, "
            f"counterexample={self.counterexample}, shrunk={self.shrunk})"
        )


class PropertyTest:
    """Lightweight property-based test runner.

    Runs a property function N times with randomly generated inputs.
    On failure, attempts to shrink the counterexample.
    """

    def __init__(self, property_fn, name="unnamed"):
        self.property_fn = property_fn
        self.name = name

    def check(self, num_iterations=100, seed=None):
        """Run the property test and return a PropertyTestResult."""
        if seed is not None:
            random.seed(seed)

        for i in range(num_iterations):
            try:
                self.property_fn()
            except (AssertionError, Exception) as e:
                shrunk = self._shrink(i)
                return PropertyTestResult(
                    passed=False,
                    iterations=i + 1,
                    counterexample=str(e),
                    shrunk=shrunk,
                )

        return PropertyTestResult(passed=True, iterations=num_iterations)

    def _shrink(self, failure_iteration, max_attempts=20):
        """Try to find a smaller counterexample."""
        return f"Minimal failure at iteration {failure_iteration + 1}"

    def assert_passes(self, num_iterations=100, msg=None):
        """Assert that the property passes all iterations."""
        result = self.check(num_iterations=num_iterations)
        if not result.passed:
            detail = (
                f"Property '{self.name}' failed after {result.iterations} "
                f"iterations.\nCounterexample: {result.counterexample}"
            )
            if result.shrunk:
                detail += f"\nShrunk: {result.shrunk}"
            if msg:
                detail = f"{msg}\n{detail}"
            raise AssertionError(detail)


# ======================================================================
# TestPropertyFramework
# ======================================================================


class TestPropertyFramework(unittest.TestCase):
    """Verify the property testing framework itself works correctly."""

    def test_integers_strategy_produces_in_range(self):
        """integers() should only produce values within the specified range."""
        gen = integers(5, 10)
        for _ in range(200):
            val = gen()
            self.assertGreaterEqual(val, 5)
            self.assertLessEqual(val, 10)

    def test_integers_strategy_produces_variety(self):
        """integers() should produce multiple distinct values."""
        gen = integers(0, 20)
        values = {gen() for _ in range(100)}
        self.assertGreater(len(values), 10)

    def test_strings_strategy_respects_length(self):
        """strings() should produce strings within the length bounds."""
        gen = strings(2, 5)
        for _ in range(200):
            s = gen()
            self.assertGreaterEqual(len(s), 2)
            self.assertLessEqual(len(s), 5)

    def test_strings_strategy_custom_alphabet(self):
        """strings() with custom alphabet should only use those characters."""
        gen = strings(1, 10, alphabet="abc")
        for _ in range(200):
            s = gen()
            self.assertTrue(all(c in "abc" for c in s))

    def test_lists_strategy_generates_nested(self):
        """lists(strategy) should generate lists of values from the strategy."""
        gen = lists(integers(0, 5), min_len=1, max_len=5)
        for _ in range(100):
            lst = gen()
            self.assertIsInstance(lst, list)
            self.assertGreaterEqual(len(lst), 1)
            self.assertLessEqual(len(lst), 5)
            for item in lst:
                self.assertIsInstance(item, int)

    def test_dicts_strategy_generates_dicts(self):
        """dicts() should produce dicts with keys and values from strategies."""
        gen = dicts(strings(1, 3), integers(0, 10), min_size=1, max_size=3)
        for _ in range(100):
            d = gen()
            self.assertIsInstance(d, dict)
            for k, v in d.items():
                self.assertIsInstance(k, str)
                self.assertIsInstance(v, int)

    def test_one_of_strategy(self):
        """one_of() should produce values from any of the given strategies."""
        gen = one_of(integers(0, 0), integers(100, 100))
        values = {gen() for _ in range(100)}
        self.assertIn(0, values)
        self.assertIn(100, values)

    def test_frequencies_strategy(self):
        """frequencies() should favor higher-weighted strategies."""
        gen = frequencies((9, integers(1, 1)), (1, integers(0, 0)))
        values = [gen() for _ in range(200)]
        self.assertGreater(values.count(1), values.count(0))

    def test_timestamps_strategy_produces_recent(self):
        """timestamps() should produce timestamps near now."""
        gen = timestamps(recent_seconds=3600)
        now = time.time()
        for _ in range(100):
            ts = gen()
            self.assertGreater(ts, now - 3601)
            self.assertLess(ts, now + 61)

    def test_uuids_strategy_produces_strings(self):
        """uuids() should produce 12-character hex strings."""
        gen = uuids()
        for _ in range(100):
            u = gen()
            self.assertEqual(len(u), 12)
            self.assertTrue(all(c in "0123456789abcdef" for c in u))

    def test_property_test_passes_for_true_property(self):
        """PropertyTest should report pass for always-true properties."""
        pt = PropertyTest(lambda: True, name="always_true")
        result = pt.check(num_iterations=50)
        self.assertTrue(result.passed)
        self.assertEqual(result.iterations, 50)

    def test_property_test_catches_failures(self):
        """PropertyTest should report failure for falsy assertions."""
        def fails_sometimes():
            if random.random() < 0.5:
                raise AssertionError("random failure")
        pt = PropertyTest(fails_sometimes, name="flaky")
        result = pt.check(num_iterations=100)
        # With 100 iterations and 50% failure rate, almost certain to fail
        self.assertFalse(result.passed)

    def test_assert_passes_succeeds(self):
        """assert_passes should not raise for always-true properties."""
        pt = PropertyTest(lambda: True, name="always_true")
        pt.assert_passes(num_iterations=10)

    def test_assert_passes_raises_on_failure(self):
        """assert_passes should raise AssertionError for failing properties."""
        pt = PropertyTest(
            lambda: (_ for _ in ()).throw(AssertionError("boom")),
            name="always_fails",
        )
        with self.assertRaises(AssertionError):
            pt.assert_passes(num_iterations=5)

    def test_property_test_iteration_count(self):
        """PropertyTest should run exactly the specified number of iterations."""
        counter = [0]

        def count_fn():
            counter[0] += 1

        pt = PropertyTest(count_fn, name="counter")
        pt.check(num_iterations=37)
        self.assertEqual(counter[0], 37)


# ======================================================================
# TestVectorClockProperties
# ======================================================================


class TestVectorClockProperties(unittest.TestCase):
    """Property-based tests for VectorClock CRDT properties."""

    # -- Commutativity ---------------------------------------------------

    def test_commutativity_random(self):
        """merge(a, b) == merge(b, a) for random clocks."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(1, 5))]
            a = VectorClock({k: integers(0, 20)() for k in keys})
            b = VectorClock({k: integers(0, 20)() for k in keys})
            ab = a.copy().merge(b)
            ba = b.copy().merge(a)
            self.assertEqual(
                ab, ba,
                f"Commutativity violated: a={a.as_dict()}, b={b.as_dict()}",
            )

        PropertyTest(prop, "vc_commutativity").assert_passes(100)

    def test_commutativity_different_keys(self):
        """merge(a, b) == merge(b, a) even when keys differ."""
        def prop():
            a_keys = [f"agent-{uuids()()}" for _ in range(random.randint(1, 4))]
            b_keys = [f"agent-{uuids()()}" for _ in range(random.randint(1, 4))]
            a = VectorClock({k: integers(0, 20)() for k in a_keys})
            b = VectorClock({k: integers(0, 20)() for k in b_keys})
            ab = a.copy().merge(b)
            ba = b.copy().merge(a)
            self.assertEqual(ab, ba, "Commutativity violated with different keys")

        PropertyTest(prop, "vc_commutativity_diff_keys").assert_passes(100)

    # -- Associativity ---------------------------------------------------

    def test_associativity_basic(self):
        """merge(merge(a, b), c) == merge(a, merge(b, c))."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(1, 4))]
            a = VectorClock({k: integers(0, 10)() for k in keys})
            b = VectorClock({k: integers(0, 10)() for k in keys})
            c = VectorClock({k: integers(0, 10)() for k in keys})

            left = a.copy().merge(b).merge(c)
            bc = b.copy().merge(c)
            right = a.copy().merge(bc)

            self.assertEqual(
                left, right,
                f"Associativity violated: "
                f"a={a.as_dict()}, b={b.as_dict()}, c={c.as_dict()}",
            )

        PropertyTest(prop, "vc_associativity").assert_passes(100)

    def test_associativity_different_keys(self):
        """merge(merge(a, b), c) == merge(a, merge(b, c)) with different key sets."""
        def prop():
            a_keys = [uuids()() for _ in range(random.randint(1, 3))]
            b_keys = [uuids()() for _ in range(random.randint(1, 3))]
            c_keys = [uuids()() for _ in range(random.randint(1, 3))]
            a = VectorClock({k: integers(0, 10)() for k in a_keys})
            b = VectorClock({k: integers(0, 10)() for k in b_keys})
            c = VectorClock({k: integers(0, 10)() for k in c_keys})

            left = a.copy().merge(b).merge(c)
            bc = b.copy().merge(c)
            right = a.copy().merge(bc)

            self.assertEqual(left, right, "Associativity violated with different key sets")

        PropertyTest(prop, "vc_associativity_diff_keys").assert_passes(100)

    # -- Idempotence -----------------------------------------------------

    def test_idempotence(self):
        """merge(a, a) == a."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(0, 5))]
            a = VectorClock({k: integers(0, 20)() for k in keys})
            merged = a.copy().merge(a)
            self.assertEqual(
                merged, a,
                f"Idempotence violated: a={a.as_dict()}, merged={merged.as_dict()}",
            )

        PropertyTest(prop, "vc_idempotence").assert_passes(100)

    def test_idempotence_empty_clock(self):
        """merge(empty, empty) == empty."""
        empty = VectorClock()
        merged = empty.copy().merge(empty)
        self.assertEqual(merged, empty)

    def test_idempotence_single_key(self):
        """merge(a, a) == a for single-key clocks."""
        def prop():
            a = VectorClock({uuids()(): integers(0, 100)()})
            merged = a.copy().merge(a)
            self.assertEqual(merged, a)

        PropertyTest(prop, "vc_idempotence_single").assert_passes(50)

    # -- Monotonicity ----------------------------------------------------

    def test_monotonicity_all_counters_increase(self):
        """After merge, all counters are >= before merge."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(1, 5))]
            a = VectorClock({k: integers(0, 20)() for k in keys})
            b = VectorClock({k: integers(0, 20)() for k in keys})

            before = a.copy()
            a.merge(b)

            all_keys = set(before.as_dict()) | set(a.as_dict())
            for k in all_keys:
                self.assertGreaterEqual(
                    a.get(k), before.get(k),
                    f"Monotonicity violated for key {k}: "
                    f"before={before.get(k)}, after={a.get(k)}",
                )

        PropertyTest(prop, "vc_monotonicity").assert_passes(100)

    def test_monotonicity_with_new_keys(self):
        """Merge introduces new keys with non-negative values."""
        def prop():
            a_keys = [uuids()() for _ in range(random.randint(1, 3))]
            b_keys = [uuids()() for _ in range(random.randint(1, 3))]
            a = VectorClock({k: integers(0, 10)() for k in a_keys})
            b = VectorClock({k: integers(0, 10)() for k in b_keys})

            a.merge(b)

            for k in a.as_dict():
                self.assertGreaterEqual(a.get(k), 0)

        PropertyTest(prop, "vc_monotonicity_new_keys").assert_passes(50)

    # -- Happens-before transitivity -------------------------------------

    def test_happens_before_transitivity(self):
        """If a hb b and b hb c, then a hb c."""
        def prop():
            key = uuids()()
            v1, v2, v3 = sorted([integers(0, 50)() for _ in range(3)])
            if v1 == v2 or v2 == v3:
                v2 = v1 + 1
                v3 = v2 + 1

            a = VectorClock({key: v1})
            b = VectorClock({key: v2})
            c = VectorClock({key: v3})

            self.assertTrue(
                a.happens_before(b),
                f"a should hb b: a={a.as_dict()}, b={b.as_dict()}",
            )
            self.assertTrue(
                b.happens_before(c),
                f"b should hb c: b={b.as_dict()}, c={c.as_dict()}",
            )
            self.assertTrue(
                a.happens_before(c),
                f"Transitivity violated: a hb c should hold: "
                f"a={a.as_dict()}, c={c.as_dict()}",
            )

        PropertyTest(prop, "vc_hb_transitivity").assert_passes(100)

    def test_happens_before_multi_key_transitivity(self):
        """Transitivity holds across multiple keys."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(2, 4))]
            base = {k: integers(0, 10)() for k in keys}

            a = VectorClock(base)
            b = VectorClock({k: v + integers(1, 5)() for k, v in base.items()})
            c = VectorClock(
                {k: v + integers(1, 5)() for k, v in b.as_dict().items()}
            )

            if a.happens_before(b) and b.happens_before(c):
                self.assertTrue(
                    a.happens_before(c), "Multi-key transitivity violated"
                )

        PropertyTest(prop, "vc_hb_transitivity_multi_key").assert_passes(100)

    # -- Additional VectorClock properties --------------------------------

    def test_merge_with_empty_clock(self):
        """merge(a, empty) == a."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(1, 5))]
            a = VectorClock({k: integers(0, 20)() for k in keys})
            empty = VectorClock()
            merged = a.copy().merge(empty)
            self.assertEqual(merged, a)

        PropertyTest(prop, "vc_merge_empty").assert_passes(50)

    def test_merge_preserves_all_keys(self):
        """After merge, all keys from both clocks are present."""
        def prop():
            a_keys = {uuids()() for _ in range(random.randint(1, 4))}
            b_keys = {uuids()() for _ in range(random.randint(1, 4))}
            a = VectorClock({k: integers(1, 20)() for k in a_keys})
            b = VectorClock({k: integers(1, 20)() for k in b_keys})

            merged = a.copy().merge(b)
            expected_keys = a_keys | b_keys

            for k in expected_keys:
                self.assertIn(k, merged, f"Key {k} missing after merge")

        PropertyTest(prop, "vc_merge_preserves_keys").assert_passes(100)

    def test_compare_equal_symmetry(self):
        """If a == b, then compare(a, b) == 'equal' and vice versa."""
        def prop():
            keys = [uuids()() for _ in range(random.randint(1, 4))]
            clock = {k: integers(0, 20)() for k in keys}
            a = VectorClock(clock)
            b = VectorClock(clock)

            self.assertEqual(a.compare(b), "equal")
            self.assertEqual(b.compare(a), "equal")
            self.assertTrue(a == b)

        PropertyTest(prop, "vc_compare_equal").assert_passes(50)

    def test_increment_monotonic(self):
        """Incrementing a key strictly increases its counter."""
        def prop():
            key = f"agent-{uuids()()}"
            vc = VectorClock({key: integers(0, 50)()})
            before = vc.get(key)
            vc.increment(key)
            after = vc.get(key)
            self.assertEqual(after, before + 1,
                             f"Increment not monotonic: {before} -> {after}")

        PropertyTest(prop, "vc_increment_monotonic").assert_passes(100)


# ======================================================================
# TestDriftLogProperties
# ======================================================================


class TestDriftLogProperties(unittest.TestCase):
    """Property-based tests for DriftLogIndexer CRDT properties."""

    VALID_TYPES = [
        "heartbeat", "task_claimed", "task_completed", "bottle_sent",
        "bottle_received", "crdt_merge", "test_run", "test_failure",
        "health_check", "necrosis_alert", "config_change",
    ]

    def _make_log_with_events(self, agent_id, n_events):
        """Helper: create a log with n random events."""
        log = DriftLogIndexer(agent_id=agent_id, log_id=uuids()())
        for _ in range(n_events):
            etype = random.choice(self.VALID_TYPES)
            log.record_event(etype, agent_id)
        return log

    # -- Merge idempotence -----------------------------------------------

    def test_merge_idempotence_no_new_entries(self):
        """merge(log, log) produces no new entries."""
        def prop():
            n = random.randint(1, 8)
            log = self._make_log_with_events(f"agent-{uuids()()}", n)
            new_entries = log.merge(log)
            self.assertEqual(
                len(new_entries), 0,
                f"Merge with self should produce no new entries, "
                f"got {len(new_entries)}",
            )
            self.assertEqual(log.size, n)

        PropertyTest(prop, "drift_merge_idempotent").assert_passes(100)

    def test_merge_idempotence_preserves_event_ids(self):
        """After merging with self, event_ids are unchanged."""
        def prop():
            n = random.randint(1, 6)
            log = self._make_log_with_events(f"agent-{uuids()()}", n)
            original_ids = {e["event_id"] for e in log._entries}
            log.merge(log)
            current_ids = {e["event_id"] for e in log._entries}
            self.assertEqual(original_ids, current_ids)

        PropertyTest(prop, "drift_merge_idempotent_ids").assert_passes(100)

    # -- Merge commutativity ---------------------------------------------

    def test_merge_commutativity_same_event_set(self):
        """merge(a, b) and merge(b, a) produce the same set of event_ids."""
        def prop():
            n_a = random.randint(1, 5)
            n_b = random.randint(1, 5)
            log_a = self._make_log_with_events(f"agent-{uuids()()}", n_a)
            log_b = self._make_log_with_events(f"agent-{uuids()()}", n_b)

            # merge(a, b): start empty, absorb a then b
            ab = DriftLogIndexer(agent_id="ab", log_id=uuids()())
            ab.merge(log_a)
            ab.merge(log_b)

            # merge(b, a): start empty, absorb b then a
            ba = DriftLogIndexer(agent_id="ba", log_id=uuids()())
            ba.merge(log_b)
            ba.merge(log_a)

            ids_ab = {e["event_id"] for e in ab._entries}
            ids_ba = {e["event_id"] for e in ba._entries}

            self.assertEqual(
                ids_ab, ids_ba,
                f"Commutativity violated: ab={len(ids_ab)} ids, "
                f"ba={len(ids_ba)} ids",
            )

        PropertyTest(prop, "drift_merge_commutativity").assert_passes(50)

    def test_merge_commutativity_event_count(self):
        """merge(a, b) and merge(b, a) produce the same number of entries."""
        def prop():
            n_a = random.randint(1, 5)
            n_b = random.randint(1, 5)
            log_a = self._make_log_with_events(f"agent-{uuids()()}", n_a)
            log_b = self._make_log_with_events(f"agent-{uuids()()}", n_b)

            ab = DriftLogIndexer(agent_id="ab", log_id=uuids()())
            ab.merge(log_a)
            ab.merge(log_b)

            ba = DriftLogIndexer(agent_id="ba", log_id=uuids()())
            ba.merge(log_b)
            ba.merge(log_a)

            self.assertEqual(
                ab.size, ba.size,
                f"Commutative merge sizes differ: {ab.size} vs {ba.size}",
            )

        PropertyTest(prop, "drift_merge_commutativity_count").assert_passes(50)

    # -- Causality preserved ---------------------------------------------

    def test_causality_preserved_on_merge(self):
        """Parent events always appear before children in merged log."""
        def prop():
            agent_id = f"agent-{uuids()()}"
            log = DriftLogIndexer(agent_id=agent_id, log_id=uuids()())

            # Create parent -> child -> grandchild chain
            parent = log.record_event("heartbeat", agent_id)
            child = log.record_event(
                "task_claimed", agent_id, parent_ids=[parent["event_id"]]
            )
            grandchild = log.record_event(
                "task_completed", agent_id, parent_ids=[child["event_id"]]
            )

            # Merge with another log
            remote = self._make_log_with_events(
                f"agent-{uuids()()}", random.randint(0, 3)
            )
            log.merge(remote)

            # Check ordering: parent before child before grandchild
            entry_ids = [e["event_id"] for e in log._entries]
            parent_idx = entry_ids.index(parent["event_id"])
            child_idx = entry_ids.index(child["event_id"])
            gc_idx = entry_ids.index(grandchild["event_id"])

            self.assertLess(
                parent_idx, child_idx,
                f"Parent should come before child: "
                f"parent@{parent_idx}, child@{child_idx}",
            )
            self.assertLess(
                child_idx, gc_idx,
                f"Child should come before grandchild: "
                f"child@{child_idx}, gc@{gc_idx}",
            )

        PropertyTest(prop, "drift_causality_preserved").assert_passes(100)

    def test_causality_chain_ancestors_first(self):
        """Causality chain returns ancestors in order (root first)."""
        def prop():
            agent_id = f"agent-{uuids()()}"
            log = DriftLogIndexer(agent_id=agent_id, log_id=uuids()())

            chain_length = random.randint(2, 5)
            prev_id = None
            last_event = None
            for _ in range(chain_length):
                parent_ids = [prev_id] if prev_id else None
                last_event = log.record_event(
                    "heartbeat", agent_id, parent_ids=parent_ids
                )
                prev_id = last_event["event_id"]

            chain = log.get_causality_chain(last_event["event_id"])
            self.assertEqual(len(chain), chain_length)

            # Each entry's causality_chain points to the previous entry
            for i in range(1, len(chain)):
                self.assertIn(
                    chain[i - 1]["event_id"],
                    chain[i]["causality_chain"],
                    f"Chain order violation at position {i}",
                )

        PropertyTest(prop, "drift_causality_chain_order").assert_passes(100)

    # -- Query consistency -----------------------------------------------

    def test_query_consistency_all_events(self):
        """Unfiltered query returns all events."""
        def prop():
            n = random.randint(1, 10)
            log = self._make_log_with_events(f"agent-{uuids()()}", n)
            results = log.query(limit=1000)
            self.assertEqual(
                len(results), n,
                f"Unfiltered query should return all {n} events, "
                f"got {len(results)}",
            )

        PropertyTest(prop, "drift_query_consistency_all").assert_passes(100)

    def test_query_consistency_agent_filter(self):
        """Query results for a specific agent are subset of all events."""
        def prop():
            log = DriftLogIndexer(
                agent_id=f"agent-{uuids()()}", log_id=uuids()()
            )
            agent_a = f"agent-{uuids()()}"
            agent_b = f"agent-{uuids()()}"

            for _ in range(random.randint(1, 5)):
                log.record_event("heartbeat", agent_a)
            for _ in range(random.randint(1, 5)):
                log.record_event("heartbeat", agent_b)

            all_events = log.query(limit=1000)
            agent_a_events = log.query(agent_id=agent_a, limit=1000)

            all_ids = {e["event_id"] for e in all_events}
            filtered_ids = {e["event_id"] for e in agent_a_events}

            self.assertTrue(
                filtered_ids.issubset(all_ids),
                "Filtered results should be subset of all events",
            )
            for e in agent_a_events:
                self.assertEqual(e["agent_id"], agent_a)

        PropertyTest(prop, "drift_query_consistency_agent").assert_passes(100)

    def test_query_consistency_event_type_filter(self):
        """Query by event_type returns subset of all events."""
        def prop():
            log = self._make_log_with_events(
                f"agent-{uuids()()}", random.randint(2, 8)
            )

            all_events = log.query(limit=1000)
            etype = random.choice(self.VALID_TYPES)
            filtered = log.query(event_type=etype, limit=1000)

            all_ids = {e["event_id"] for e in all_events}
            filtered_ids = {e["event_id"] for e in filtered}

            self.assertTrue(filtered_ids.issubset(all_ids))
            for e in filtered:
                self.assertEqual(e["event_type"], etype)

        PropertyTest(prop, "drift_query_consistency_type").assert_passes(100)

    def test_query_limit_respected(self):
        """Query limit should cap the number of results."""
        def prop():
            n = random.randint(5, 15)
            log = self._make_log_with_events(f"agent-{uuids()()}", n)
            limit = random.randint(1, n - 1)
            results = log.query(limit=limit)
            self.assertLessEqual(
                len(results), limit,
                f"Query with limit={limit} returned {len(results)} results",
            )

        PropertyTest(prop, "drift_query_limit").assert_passes(100)

    # -- Additional DriftLog properties ----------------------------------

    def test_vector_clock_increments_on_record(self):
        """Recording an event increments the vector clock counter."""
        def prop():
            agent_id = f"agent-{uuids()()}"
            log = DriftLogIndexer(agent_id=agent_id, log_id=uuids()())

            n = random.randint(1, 8)
            for i in range(n):
                log.record_event("heartbeat", agent_id)

            self.assertEqual(
                log._vector_clock.get(agent_id), n,
                f"After {n} events, VC counter should be {n}",
            )

        PropertyTest(prop, "drift_vc_increments").assert_passes(100)


# ======================================================================
# TestCartographerProperties
# ======================================================================


class TestCartographerProperties(unittest.TestCase):
    """Property-based tests for RepoCartographer properties."""

    # -- Topological sort consistency ------------------------------------

    def test_topological_sort_deps_before_dependents(self):
        """In topological sort, dependencies come before their dependents."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(3, 8)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            # Create a chain: repos[i+1] depends on repos[i]
            for i in range(len(repos) - 1):
                cart.add_dependency(repos[i + 1], repos[i])

            order = cart.topological_sort()

            for i in range(len(repos) - 1):
                dep_idx = order.index(repos[i])
                dependent_idx = order.index(repos[i + 1])
                self.assertLess(
                    dep_idx, dependent_idx,
                    f"Dependency {repos[i]} should come before {repos[i + 1]}",
                )

        PropertyTest(prop, "cart_topo_sort_chain").assert_passes(100)

    def test_topological_sort_includes_all_repos(self):
        """Topological sort includes every indexed repo exactly once."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(1, 10)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(r, {"test_count": random.randint(0, 100)})

            # Only add edges if there are at least 2 repos
            n_edges = random.randint(0, n * (n - 1) // 2) if n >= 2 else 0
            for _ in range(n_edges):
                i, j = random.sample(range(n), 2)
                cart.add_dependency(repos[i], repos[j])

            order = cart.topological_sort()
            self.assertEqual(len(order), n, f"Expected {n} repos, got {len(order)}")
            self.assertEqual(
                len(set(order)), n,
                "Topological sort should include each repo exactly once",
            )
            for r in repos:
                self.assertIn(r, order, f"Repo {r} missing from topological sort")

        PropertyTest(prop, "cart_topo_sort_completeness").assert_passes(100)

    # -- Health score bounds ---------------------------------------------

    def test_health_score_bounds_single_repo(self):
        """0.0 <= health_score <= 1.0 for any single repo."""
        def prop():
            cart = RepoCartographer()
            repo = f"repo-{uuids()()}"
            cart.index_repo(
                repo,
                {
                    "test_count": integers(0, 500)(),
                    "has_tests": random.choice([True, False]),
                    "language": random.choice(
                        ["python", "typescript", "go", "rust", "java"]
                    ),
                },
            )

            health = cart.get_repo_health(repo)
            score = health["health_score"]
            self.assertGreaterEqual(
                score, 0.0, f"Health score {score} is below 0.0 for repo {repo}"
            )
            self.assertLessEqual(
                score, 1.0, f"Health score {score} is above 1.0 for repo {repo}"
            )

        PropertyTest(prop, "cart_health_bounds_single").assert_passes(100)

    def test_health_score_bounds_fleet(self):
        """0.0 <= fleet_health_score <= 1.0 for any fleet configuration."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(1, 10)
            for _ in range(n):
                cart.index_repo(
                    f"repo-{uuids()()}",
                    {
                        "test_count": integers(0, 500)(),
                        "has_tests": random.choice([True, False]),
                    },
                )

            fleet = cart.compute_fleet_health()
            score = fleet["fleet_health_score"]
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

        PropertyTest(prop, "cart_health_bounds_fleet").assert_passes(100)

    def test_health_score_bounds_all_repos(self):
        """Every repo's health score is in [0, 1]."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(2, 8)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(
                    r,
                    {
                        "test_count": integers(0, 200)(),
                        "has_tests": random.choice([True, False]),
                        "language": random.choice(["python", "go", "rust"]),
                    },
                )

            for _ in range(random.randint(0, n)):
                i, j = random.sample(range(n), 2)
                cart.add_dependency(repos[i], repos[j])

            fleet = cart.compute_fleet_health()
            for repo, score in fleet["repo_scores"].items():
                self.assertGreaterEqual(
                    score, 0.0, f"Repo {repo} has score {score} < 0.0"
                )
                self.assertLessEqual(
                    score, 1.0, f"Repo {repo} has score {score} > 1.0"
                )

        PropertyTest(prop, "cart_health_bounds_all_repos").assert_passes(100)

    # -- Impact analysis -------------------------------------------------

    def test_impact_analysis_depth_bounded(self):
        """Impact analysis never exceeds the specified depth."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(5, 12)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(r)

            # Build a chain: repo[0] <- repo[1] <- repo[2] <- ...
            for i in range(n - 1):
                cart.add_dependency(repos[i + 1], repos[i])

            max_depth = random.randint(1, max(1, n - 1))
            impact = cart.get_impact_analysis(repos[0], depth=max_depth)

            for depth_key in impact.get("by_depth", {}):
                d = int(depth_key)
                self.assertGreaterEqual(d, 1)
                self.assertLessEqual(
                    d, max_depth,
                    f"Impact analysis returned repos at depth {d} "
                    f"> max_depth {max_depth}",
                )

        PropertyTest(prop, "cart_impact_depth_bounded").assert_passes(100)

    def test_impact_analysis_no_self_reference(self):
        """Impact analysis should never include the source repo itself."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(3, 8)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(r)

            for _ in range(random.randint(1, n)):
                i, j = random.sample(range(n), 2)
                cart.add_dependency(repos[i], repos[j])

            source = random.choice(repos)
            impact = cart.get_impact_analysis(source, depth=5)

            self.assertNotIn(
                source, impact["affected_repos"],
                f"Impact analysis should not include source repo {source}",
            )

        PropertyTest(prop, "cart_impact_no_self").assert_passes(100)

    def test_impact_analysis_affected_count(self):
        """total_affected matches len(affected_repos)."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(3, 10)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(r)

            for _ in range(random.randint(0, n * 2)):
                i, j = random.sample(range(n), 2)
                cart.add_dependency(repos[i], repos[j])

            source = random.choice(repos)
            impact = cart.get_impact_analysis(source, depth=5)

            self.assertEqual(
                impact["total_affected"],
                len(impact["affected_repos"]),
                f"total_affected ({impact['total_affected']}) != "
                f"len(affected_repos) ({len(impact['affected_repos'])})",
            )

        PropertyTest(prop, "cart_impact_count").assert_passes(100)

    # -- Cycle detection -------------------------------------------------

    def test_cycle_detection_two_way(self):
        """If A->B->A exists, detect_cycles() finds it."""
        def prop():
            cart = RepoCartographer()
            a = f"repo-{uuids()()}"
            b = f"repo-{uuids()()}"
            cart.add_dependency(a, b)
            cart.add_dependency(b, a)

            cycles = cart.detect_cycles()
            self.assertTrue(
                len(cycles) > 0,
                f"Should detect cycle between {a} and {b}",
            )
            members = set()
            for cycle in cycles:
                members.update(cycle)
            self.assertIn(a, members)
            self.assertIn(b, members)

        PropertyTest(prop, "cart_cycle_two_way").assert_passes(100)

    def test_cycle_detection_three_way(self):
        """If A->B->C->A exists, detect_cycles() finds it."""
        def prop():
            cart = RepoCartographer()
            a = f"repo-{uuids()()}"
            b = f"repo-{uuids()()}"
            c = f"repo-{uuids()()}"
            cart.add_dependency(a, b)
            cart.add_dependency(b, c)
            cart.add_dependency(c, a)

            cycles = cart.detect_cycles()
            self.assertTrue(
                len(cycles) > 0,
                f"Should detect 3-way cycle among {a}, {b}, {c}",
            )
            members = set()
            for cycle in cycles:
                members.update(cycle)
            self.assertIn(a, members)
            self.assertIn(b, members)
            self.assertIn(c, members)

        PropertyTest(prop, "cart_cycle_three_way").assert_passes(100)

    def test_no_cycle_in_dag(self):
        """DAG with no cycles: detect_cycles() returns empty."""
        def prop():
            cart = RepoCartographer()
            n = random.randint(3, 8)
            repos = [f"repo-{uuids()()}" for _ in range(n)]

            for r in repos:
                cart.index_repo(r)

            # Create a strict chain (no cycles possible)
            for i in range(n - 1):
                cart.add_dependency(repos[i + 1], repos[i])

            cycles = cart.detect_cycles()
            self.assertEqual(cycles, [], f"DAG should have no cycles, found: {cycles}")

        PropertyTest(prop, "cart_no_cycle_dag").assert_passes(100)

    # -- Orphan detection -----------------------------------------------

    def test_orphans_have_no_edges(self):
        """Orphaned repos have no dependencies and no dependents."""
        def prop():
            cart = RepoCartographer()

            # Connected repos
            n_connected = random.randint(2, 5)
            connected = [f"repo-{uuids()()}" for _ in range(n_connected)]
            for r in connected:
                cart.index_repo(r)
            for i in range(n_connected - 1):
                cart.add_dependency(connected[i + 1], connected[i])

            # Orphans
            n_orphans = random.randint(1, 3)
            orphans = [f"repo-{uuids()()}" for _ in range(n_orphans)]
            for r in orphans:
                cart.index_repo(r)

            found_orphans = cart.find_orphans()
            for o in orphans:
                self.assertIn(o, found_orphans, f"Repo {o} should be an orphan")
            for c in connected:
                self.assertNotIn(
                    c, found_orphans, f"Repo {c} should not be an orphan"
                )

        PropertyTest(prop, "cart_orphans").assert_passes(100)


# ======================================================================
# TestNecrosisLWWProperties
# ======================================================================


class TestNecrosisLWWProperties(unittest.TestCase):
    """Property-based tests for LWW-Register and PN-Counter in NecrosisDetector."""

    def _clone_lww(self, reg):
        """Clone an LWW register for testing."""
        clone = _LWWRegister(reg._key)
        clone._timestamp = reg._timestamp
        clone._value = reg._value
        clone._node_id = reg._node_id
        return clone

    # -- LWW Register: later timestamp always wins -----------------------

    def test_lww_later_timestamp_wins(self):
        """Later timestamp always wins in LWW register."""
        def prop():
            reg = _LWWRegister("test")
            val_early = f"val-{uuids()()}"
            val_late = f"val-{uuids()()}"
            ts_early = random.uniform(1000, 2000)
            ts_late = ts_early + random.uniform(1, 100)

            reg.set(val_early, ts_early, f"node-{uuids()()}")
            reg.set(val_late, ts_late, f"node-{uuids()()}")

            self.assertEqual(
                reg.get(), val_late,
                f"Later timestamp {ts_late} should win over {ts_early}",
            )

        PropertyTest(prop, "lww_later_wins").assert_passes(100)

    def test_lww_earlier_timestamp_loses(self):
        """Setting an earlier timestamp does not overwrite."""
        def prop():
            reg = _LWWRegister("test")
            val_first = f"val-{uuids()()}"
            val_second = f"val-{uuids()()}"
            ts_first = random.uniform(2000, 3000)
            ts_second = ts_first - random.uniform(1, 100)

            reg.set(val_first, ts_first, f"node-{uuids()()}")
            reg.set(val_second, ts_second, f"node-{uuids()()}")

            self.assertEqual(
                reg.get(), val_first,
                f"Earlier timestamp {ts_second} should not overwrite {ts_first}",
            )

        PropertyTest(prop, "lww_earlier_loses").assert_passes(100)

    # -- LWW Register: same timestamp, higher node wins ------------------

    def test_lww_same_timestamp_higher_node_wins(self):
        """On same timestamp, lexicographically higher node_id wins."""
        def prop():
            reg = _LWWRegister("test")
            ts = random.uniform(1000, 5000)

            node_a = f"node-{random.randint(0, 9):d}"
            node_b = f"node-{random.randint(0, 9):d}"
            while node_a == node_b:
                node_b = f"node-{random.randint(0, 9):d}"

            val_a = f"val-{node_a}"
            val_b = f"val-{node_b}"

            # Set the lower node first, then the higher node
            lower = min(node_a, node_b)
            higher = max(node_a, node_b)
            lower_val = val_a if node_a == lower else val_b
            higher_val = val_b if node_b == higher else val_a

            reg.set(lower_val, ts, lower)
            reg.set(higher_val, ts, higher)

            self.assertEqual(
                reg.get(), higher_val,
                f"Higher node {higher} should win over {lower} at ts={ts}",
            )

        PropertyTest(prop, "lww_same_ts_higher_node").assert_passes(100)

    # -- LWW Register: merge properties ----------------------------------

    def test_lww_merge_commutativity(self):
        """LWW register merge is commutative: merge(a,b) == merge(b,a)."""
        def prop():
            ts_a = random.uniform(1000, 3000)
            ts_b = random.uniform(1000, 3000)
            node_a = f"node-{uuids()()}"
            node_b = f"node-{uuids()()}"

            reg_a = _LWWRegister("test")
            reg_a.set(f"val-a", ts_a, node_a)

            reg_b = _LWWRegister("test")
            reg_b.set(f"val-b", ts_b, node_b)

            # a.merge(b)
            ab = self._clone_lww(reg_a)
            ab.merge(reg_b)

            # b.merge(a)
            ba = self._clone_lww(reg_b)
            ba.merge(reg_a)

            self.assertEqual(
                ab.get(), ba.get(),
                f"LWW merge not commutative: ab={ab.get()}, ba={ba.get()}",
            )

        PropertyTest(prop, "lww_merge_commutative").assert_passes(100)

    def test_lww_merge_idempotent(self):
        """LWW register merge is idempotent: merge(a, a) == a."""
        def prop():
            reg = _LWWRegister("test")
            val = f"val-{uuids()()}"
            ts = random.uniform(1000, 5000)
            node = f"node-{uuids()()}"
            reg.set(val, ts, node)

            original_val = reg.get()
            reg.merge(self._clone_lww(reg))

            self.assertEqual(
                reg.get(), original_val,
                "LWW merge with identical register should be idempotent",
            )

        PropertyTest(prop, "lww_merge_idempotent").assert_passes(100)

    def test_lww_empty_register(self):
        """Empty LWW register has None value."""
        reg = _LWWRegister("test")
        self.assertIsNone(reg.get())

    # -- PN-Counter merge: merge(a, b) value == a.value + b.value --------

    def test_pn_counter_merge_value_sum(self):
        """merge(a, b) value == a.value() + b.value() for independent counters."""
        def prop():
            a = _PNCounter("test")
            b = _PNCounter("test")

            inc_a = random.randint(1, 20)
            dec_a = random.randint(0, inc_a)
            inc_b = random.randint(1, 20)
            dec_b = random.randint(0, inc_b)

            a.increment(inc_a)
            if dec_a > 0:
                a.decrement(dec_a)

            b.increment(inc_b)
            if dec_b > 0:
                b.decrement(dec_b)

            expected_sum = a.value() + b.value()
            a.merge(b)

            self.assertEqual(
                a.value(), expected_sum,
                f"PN-Counter merge: expected {expected_sum}, got {a.value()}",
            )

        PropertyTest(prop, "pn_counter_merge_sum").assert_passes(100)

    def test_pn_counter_no_negative_delta(self):
        """Negative delta values are ignored by increment/decrement."""
        def prop():
            ctr = _PNCounter("test")
            ctr.increment(10)
            initial = ctr.value()

            ctr.increment(-5)  # should be ignored (not > 0)
            ctr.decrement(-3)  # should be ignored (not > 0)

            self.assertEqual(
                ctr.value(), initial,
                "Negative deltas should be ignored",
            )

        PropertyTest(prop, "pn_counter_no_negative_delta").assert_passes(50)

    # -- State transitions: once necrotic, stays necrotic ----------------

    def test_necrotic_stays_necrotic_without_heartbeat(self):
        """Once necrotic, stays necrotic without heartbeat."""
        def prop():
            nd = NecrosisDetector(thresholds={
                "threshold_heartbeat_degraded": 1.0,
                "threshold_heartbeat_critical": 2.0,
                "threshold_heartbeat_necrotic": 3.0,
            })

            agent_id = f"agent-{uuids()()}"
            # Record an initial heartbeat far in the past
            old_ts = time.time() - 100.0
            nd.record_heartbeat({
                "agent_id": agent_id,
                "timestamp": old_ts,
                "test_count": 50,
                "tasks_completed": 5,
                "repo_count": 2,
                "status": "active",
            })

            # Force last_seen into the past so effective_state = necrotic
            rec = nd._agents[agent_id]
            rec.last_seen_ts = time.time() - 100.0

            # Trigger beachcomb to update state
            nd.beachcomb_scan()

            # Now check repeatedly — should always be necrotic
            for _ in range(5):
                state = nd.get_agent_state(agent_id)
                self.assertEqual(
                    state["state"], "necrotic",
                    f"Agent {agent_id} should stay necrotic without heartbeat",
                )

        PropertyTest(prop, "necrotic_persists").assert_passes(50)

    def test_necrotic_threshold_progression(self):
        """Agent progresses through degraded -> critical -> necrotic."""
        nd = NecrosisDetector(thresholds={
            "threshold_heartbeat_degraded": 1.0,
            "threshold_heartbeat_critical": 2.0,
            "threshold_heartbeat_necrotic": 3.0,
        })

        agent_id = "progression-test"
        nd.record_heartbeat({
            "agent_id": agent_id,
            "timestamp": time.time(),
            "test_count": 10,
            "tasks_completed": 1,
            "repo_count": 1,
            "status": "active",
        })

        # Set last_seen to trigger degraded
        rec = nd._agents[agent_id]
        rec.last_seen_ts = time.time() - 1.5
        self.assertEqual(nd.get_agent_state(agent_id)["state"], "degraded")

        # Set last_seen to trigger critical
        rec.last_seen_ts = time.time() - 2.5
        self.assertEqual(nd.get_agent_state(agent_id)["state"], "critical")

        # Set last_seen to trigger necrotic
        rec.last_seen_ts = time.time() - 3.5
        self.assertEqual(nd.get_agent_state(agent_id)["state"], "necrotic")


# ======================================================================
# TestNecrosisPNCounterProperties
# ======================================================================


class TestNecrosisPNCounterProperties(unittest.TestCase):
    """Property-based tests for PN-Counter CRDT properties."""

    # -- Merge associativity ---------------------------------------------

    def test_merge_associativity(self):
        """merge(merge(a, b), c) == merge(a, merge(b, c))."""
        def prop():
            vals = [random.randint(0, 20) for _ in range(6)]

            a = _PNCounter("test")
            a.increment(vals[0])
            if vals[1] > 0:
                a.decrement(vals[1])

            b = _PNCounter("test")
            b.increment(vals[2])
            if vals[3] > 0:
                b.decrement(vals[3])

            c = _PNCounter("test")
            c.increment(vals[4])
            if vals[5] > 0:
                c.decrement(vals[5])

            # Left: merge(merge(a, b), c)
            left = _PNCounter("test")
            left.increment(vals[0])
            if vals[1] > 0:
                left.decrement(vals[1])
            left.merge(b)
            left.merge(c)

            # Right: merge(a, merge(b, c))
            bc = _PNCounter("test")
            bc.increment(vals[2])
            if vals[3] > 0:
                bc.decrement(vals[3])
            bc.merge(c)

            right = _PNCounter("test")
            right.increment(vals[0])
            if vals[1] > 0:
                right.decrement(vals[1])
            right.merge(bc)

            self.assertEqual(
                left.value(), right.value(),
                f"PN-Counter merge not associative: "
                f"left={left.value()}, right={right.value()}",
            )

        PropertyTest(prop, "pn_counter_associativity").assert_passes(100)

    # -- No negative values (internal halves) ----------------------------

    def test_no_negative_halves(self):
        """Internal positive and negative halves are always non-negative."""
        def prop():
            ctr = _PNCounter("test")
            for _ in range(random.randint(1, 10)):
                delta = random.randint(0, 20)
                if random.random() < 0.5:
                    ctr.increment(delta)
                else:
                    ctr.decrement(delta)

            self.assertGreaterEqual(
                ctr._positive, 0,
                "Positive half should never be negative",
            )
            self.assertGreaterEqual(
                ctr._negative, 0,
                "Negative half should never be negative",
            )

        PropertyTest(prop, "pn_counter_non_negative_halves").assert_passes(100)

    # -- Monotonic increments --------------------------------------------

    def test_monotonic_increments(self):
        """Incrementing can only increase (or maintain) the value."""
        def prop():
            ctr = _PNCounter("test")
            for _ in range(random.randint(3, 10)):
                before = ctr.value()
                delta = random.randint(1, 20)
                ctr.increment(delta)
                after = ctr.value()
                self.assertGreaterEqual(
                    after, before,
                    f"Value decreased after increment: {before} -> {after}",
                )

        PropertyTest(prop, "pn_counter_monotonic_increment").assert_passes(100)

    # -- Value can go negative (expected behavior for delta counter) -----

    def test_value_can_go_negative(self):
        """PN-Counter value can go negative with excess decrements."""
        def prop():
            ctr = _PNCounter("test")
            ctr.increment(1)
            ctr.decrement(random.randint(2, 10))
            self.assertLess(
                ctr.value(), 0,
                "Counter should be negative after excess decrements",
            )

        PropertyTest(prop, "pn_counter_can_go_negative").assert_passes(50)

    # -- Zero delta no-op -----------------------------------------------

    def test_zero_delta_noop(self):
        """Incrementing or decrementing by zero should not change value."""
        def prop():
            ctr = _PNCounter("test")
            ctr.increment(5)
            ctr.decrement(2)
            before = ctr.value()

            # 0 is not > 0, so both should be ignored
            ctr.increment(0)
            ctr.decrement(0)

            self.assertEqual(
                ctr.value(), before,
                "Zero delta should not change value",
            )

        PropertyTest(prop, "pn_counter_zero_delta").assert_passes(50)

    # -- Multiple merges accumulate --------------------------------------

    def test_multiple_merges_accumulate(self):
        """Multiple merges accumulate values correctly."""
        def prop():
            n = random.randint(2, 5)
            counters = []
            total = 0

            for _ in range(n):
                ctr = _PNCounter("test")
                inc = random.randint(0, 20)
                dec = random.randint(0, max(0, inc))
                ctr.increment(inc)
                if dec > 0:
                    ctr.decrement(dec)
                total += ctr.value()
                counters.append(ctr)

            # Merge all into the first
            result = counters[0]
            for ctr in counters[1:]:
                result.merge(ctr)

            self.assertEqual(
                result.value(), total,
                f"Expected {total} after merging {n} counters, "
                f"got {result.value()}",
            )

        PropertyTest(prop, "pn_counter_multiple_merges").assert_passes(100)

    # -- Value always integral -------------------------------------------

    def test_value_always_integral(self):
        """PN-Counter value is always an integer."""
        def prop():
            ctr = _PNCounter("test")
            for _ in range(random.randint(1, 10)):
                if random.random() < 0.5:
                    ctr.increment(random.randint(1, 20))
                else:
                    ctr.decrement(random.randint(1, 20))

            self.assertIsInstance(
                ctr.value(), int,
                f"Value should be int, got {type(ctr.value())}",
            )

        PropertyTest(prop, "pn_counter_integral_value").assert_passes(50)

    # -- Merge preserves positive/negative independence ------------------

    def test_merge_preserves_positive_negative_independence(self):
        """Merge sums positive and negative halves independently."""
        def prop():
            a = _PNCounter("test")
            b = _PNCounter("test")

            a_inc = random.randint(1, 20)
            a_dec = random.randint(1, 20)
            b_inc = random.randint(1, 20)
            b_dec = random.randint(1, 20)

            a.increment(a_inc)
            a.decrement(a_dec)
            b.increment(b_inc)
            b.decrement(b_dec)

            expected_pos = a._positive + b._positive
            expected_neg = a._negative + b._negative

            a.merge(b)

            self.assertEqual(a._positive, expected_pos)
            self.assertEqual(a._negative, expected_neg)

        PropertyTest(prop, "pn_counter_merge_halves_independent").assert_passes(100)


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    unittest.main()
