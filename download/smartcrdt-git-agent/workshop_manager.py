"""Workshop and bootcamp management for onboarding fleet agents to SmartCRDT.

Provides a structured curriculum of code recipes and a five-level bootcamp
programme so that new agents can progressively build competence with the
SmartCRDT monorepo.  Progress is persisted to a local JSON file so agents
can resume where they left off across sessions.

Usage::

    wm = WorkshopManager("/path/to/workshop")
    recipe = wm.get_recipe("add-counter")
    results = wm.run_recipe("add-counter")
    level = wm.assess_level({"merge_conflict": True, "crdt_design": False})
    path = wm.get_learning_path(level)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROGRESS_FILE = "workshop_progress.json"

# ---------------------------------------------------------------------------
# Code recipe definitions
# ---------------------------------------------------------------------------

RECIPES: List[Dict[str, Any]] = [
    {
        "name": "add-counter",
        "description": (
            "Teaches how to add a new CRDT counter variant (e.g. PN-Counter, "
            "Bounded-Counter, Monotonic-Counter) to the monorepo."
        ),
        "steps": [
            {
                "instruction": (
                    "Create a new Python package under smartcrdt/counters/ "
                    "with __init__.py, implementation.py, and test files."
                ),
                "file_pattern": "smartcrdt/counters/{variant}/__init__.py",
                "expected_outcome": "Package is importable as smartcrdt.counters.{variant}",
            },
            {
                "instruction": (
                    "Implement the counter class inheriting from CounterBase "
                    "and override increment(), decrement(), and value()."
                ),
                "file_pattern": "smartcrdt/counters/{variant}/implementation.py",
                "expected_outcome": "Class passes pyflakes and conforms to CounterBase ABC",
            },
            {
                "instruction": (
                    "Write merge logic — the class must correctly merge state "
                    "from a remote replica, satisfying the CRDT invariant that "
                    "concurrent increments are never lost."
                ),
                "file_pattern": "smartcrdt/counters/{variant}/implementation.py",
                "expected_outcome": "Merge tests pass with at least 3 concurrent replicas",
            },
            {
                "instruction": (
                    "Register the variant in smartcrdt/counters/__init__.py "
                    "so the factory can instantiate it by name."
                ),
                "file_pattern": "smartcrdt/counters/__init__.py",
                "expected_outcome": "CounterFactory.create('{variant}') returns an instance",
            },
            {
                "instruction": (
                    "Add comprehensive doctests and a README snippet in the "
                    "variant package showing usage."
                ),
                "file_pattern": "smartcrdt/counters/{variant}/README.md",
                "expected_outcome": "doctest passes with no failures",
            },
        ],
        "difficulty": "intermediate",
        "estimated_time_minutes": 45,
        "tags": ["crdt", "counter", "new-type"],
    },
    {
        "name": "add-set",
        "description": (
            "Teaches how to add a new CRDT set variant (e.g. OR-Set, LWW-Set, "
            "Remove-Wins Set) to the monorepo."
        ),
        "steps": [
            {
                "instruction": (
                    "Create a new package under smartcrdt/sets/{variant}/ with "
                    "the standard layout (__init__.py, implementation.py, tests/)."
                ),
                "file_pattern": "smartcrdt/sets/{variant}/__init__.py",
                "expected_outcome": "Package directory exists and is importable",
            },
            {
                "instruction": (
                    "Implement the set class inheriting from SetBase.  "
                    "Override add(), remove(), contains(), and merge()."
                ),
                "file_pattern": "smartcrdt/sets/{variant}/implementation.py",
                "expected_outcome": "All abstract methods implemented; mypy reports no errors",
            },
            {
                "instruction": (
                    "Ensure concurrent add/remove operations converge.  "
                    "Test with a split-brain scenario of two partitions "
                    "that independently add and remove elements."
                ),
                "file_pattern": "smartcrdt/sets/{variant}/tests/test_merge.py",
                "expected_outcome": "State converges after merge in all split-brain tests",
            },
            {
                "instruction": (
                    "Register in smartcrdt/sets/__init__.py factory and add "
                    "a __all__ entry for the public API."
                ),
                "file_pattern": "smartcrdt/sets/__init__.py",
                "expected_outcome": "SetFactory.create('{variant}') succeeds",
            },
        ],
        "difficulty": "intermediate",
        "estimated_time_minutes": 50,
        "tags": ["crdt", "set", "new-type"],
    },
    {
        "name": "add-register",
        "description": (
            "Teaches how to add a new CRDT register variant (e.g. LWW-Register, "
            "MV-Register, Multi-Value Register) to the monorepo."
        ),
        "steps": [
            {
                "instruction": (
                    "Create smartcrdt/registers/{variant}/ with the standard "
                    "package layout."
                ),
                "file_pattern": "smartcrdt/registers/{variant}/__init__.py",
                "expected_outcome": "Package is importable",
            },
            {
                "instruction": (
                    "Implement the register class with get(), set(), and merge()."
                ),
                "file_pattern": "smartcrdt/registers/{variant}/implementation.py",
                "expected_outcome": "Read/write operations behave correctly for single replica",
            },
            {
                "instruction": (
                    "Write merge tests that simulate concurrent writes from "
                    "multiple replicas and verify deterministic convergence."
                ),
                "file_pattern": "smartcrdt/registers/{variant}/tests/test_merge.py",
                "expected_outcome": "All concurrent-write tests pass",
            },
            {
                "instruction": (
                    "Register in the register factory and add type hints "
                    "compatible with the shared protocol."
                ),
                "file_pattern": "smartcrdt/registers/__init__.py",
                "expected_outcome": "RegisterFactory.create('{variant}') returns typed instance",
            },
        ],
        "difficulty": "intermediate",
        "estimated_time_minutes": 40,
        "tags": ["crdt", "register", "new-type"],
    },
    {
        "name": "add-gossip",
        "description": (
            "Teaches how to add a new gossip protocol variant (e.g. Plumtree, "
            "HyParView, Scamp, SWIM-style gossip) to the SmartCRDT fleet."
        ),
        "steps": [
            {
                "instruction": (
                    "Create smartcrdt/gossip/{variant}/ with the standard "
                    "package layout including a protocol.py module."
                ),
                "file_pattern": "smartcrdt/gossip/{variant}/__init__.py",
                "expected_outcome": "Package directory and init file created",
            },
            {
                "instruction": (
                    "Implement the protocol class inheriting from "
                    "GossipProtocolBase with broadcast(), receive(), and "
                    "get_view() methods."
                ),
                "file_pattern": "smartcrdt/gossip/{variant}/protocol.py",
                "expected_outcome": "Class compiles and passes static analysis",
            },
            {
                "instruction": (
                    "Simulate a 10-node cluster in a test and verify that a "
                    "message broadcast from one node reaches all others within "
                    "O(log N) rounds."
                ),
                "file_pattern": "smartcrdt/gossip/{variant}/tests/test_fanout.py",
                "expected_outcome": "All 10 nodes receive the message within expected rounds",
            },
            {
                "instruction": (
                    "Register the protocol in the gossip factory and write "
                    "a configuration example in the package README."
                ),
                "file_pattern": "smartcrdt/gossip/__init__.py",
                "expected_outcome": "GossipFactory.create('{variant}') returns a working protocol",
            },
            {
                "instruction": (
                    "Add integration tests that simulate node churn — nodes "
                    "joining and leaving — and confirm protocol stability."
                ),
                "file_pattern": "smartcrdt/gossip/{variant}/tests/test_churn.py",
                "expected_outcome": "Protocol remains stable under 30% churn rate",
            },
        ],
        "difficulty": "advanced",
        "estimated_time_minutes": 90,
        "tags": ["gossip", "protocol", "networking"],
    },
    {
        "name": "add-test",
        "description": (
            "Teaches how to add tests to an existing CRDT package: unit tests, "
            "property-based tests, and merge-convergence tests."
        ),
        "steps": [
            {
                "instruction": (
                    "Identify the target CRDT package and read its existing "
                    "tests to understand the conventions."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/",
                "expected_outcome": "Familiarity with test helpers, fixtures, and naming conventions",
            },
            {
                "instruction": (
                    "Write unit tests for all public methods using the "
                    "existing test helper utilities."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/test_unit.py",
                "expected_outcome": "At least 90% line coverage on implementation.py",
            },
            {
                "instruction": (
                    "Write property-based tests (hypothesis-free, pure "
                    "stdlib) that assert CRDT invariants over random operation "
                    "sequences."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/test_properties.py",
                "expected_outcome": "Properties hold for 1 000+ random operation sequences",
            },
            {
                "instruction": (
                    "Write a merge convergence test that forks a CRDT state "
                    "into N replicas, applies random operations, and verifies "
                    "all replicas converge after merging."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/test_merge.py",
                "expected_outcome": "Convergence holds for 50+ random fork-merge cycles",
            },
        ],
        "difficulty": "beginner",
        "estimated_time_minutes": 30,
        "tags": ["testing", "crdt", "quality"],
    },
    {
        "name": "fix-merge",
        "description": (
            "Teaches how to diagnose and fix CRDT merge bugs: non-convergent "
            "states, lost updates, and causal violations."
        ),
        "steps": [
            {
                "instruction": (
                    "Reproduce the bug by writing a minimal failing test "
                    "that exercises the exact concurrent operation pattern "
                    "causing divergence."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/test_regression.py",
                "expected_outcome": "Test fails deterministically, reproducing the issue",
            },
            {
                "instruction": (
                    "Trace through the merge() method with print-debugging "
                    "or a mental execution to identify where the invariant "
                    "is broken."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/implementation.py",
                "expected_outcome": "Root cause identified and documented in a comment",
            },
            {
                "instruction": (
                    "Implement the fix and verify that the regression test "
                    "now passes."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/implementation.py",
                "expected_outcome": "Regression test passes",
            },
            {
                "instruction": (
                    "Run the full test suite for the package to confirm no "
                    "regressions were introduced."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/tests/",
                "expected_outcome": "All existing tests still pass",
            },
            {
                "instruction": (
                    "Add a short docstring comment to the fixed method "
                    "explaining the merge invariant and why the original "
                    "code violated it."
                ),
                "file_pattern": "smartcrdt/{category}/{name}/implementation.py",
                "expected_outcome": "Future readers can understand the subtlety",
            },
        ],
        "difficulty": "advanced",
        "estimated_time_minutes": 60,
        "tags": ["debugging", "merge", "crdt", "invariant"],
    },
]

# ---------------------------------------------------------------------------
# Bootcamp level definitions
# ---------------------------------------------------------------------------

BOOTCAMP_LEVELS: List[Dict[str, Any]] = [
    {
        "level": 1,
        "name": "Greenhorn",
        "description": (
            "Welcome aboard!  Learn the monorepo layout, run existing tests, "
            "and understand what a CRDT is at a conceptual level."
        ),
        "prerequisites": [],
        "lessons": [
            "Hello CRDT — what conflict-free replicated data types are and why they matter.",
            "Monorepo tour — directory layout, build system, CI pipeline, and conventions.",
            "Running tests locally — how to invoke pytest, interpret output, and read logs.",
            "Git workflow — branching model, commit message format, and PR process.",
        ],
        "practical_exercises": [
            "Clone the repo and run the full test suite successfully.",
            "Read smartcrdt/counters/pncounter/ and write a short paragraph "
            "summarising how it works.",
            "Open a 'good first issue' and leave a comment with a proposed "
            "fix approach.",
        ],
        "passing_criteria": (
            "Agent can clone the repo, run all tests, and articulate the "
            "purpose of at least one CRDT type."
        ),
    },
    {
        "level": 2,
        "name": "Deckhand",
        "description": (
            "Read and understand core CRDT implementations.  Write basic "
            "unit tests and get comfortable with the codebase."
        ),
        "prerequisites": ["Greenhorn"],
        "lessons": [
            "Core abstractions — CounterBase, SetBase, RegisterBase, and shared protocols.",
            "State vs. operation-based CRDTs — trade-offs and which SmartCRDT uses.",
            "Test conventions — fixtures, parametrisation, and convergence testing.",
            "Merge semantics — how merge() achieves convergence across replicas.",
        ],
        "practical_exercises": [
            "Complete the 'add-test' recipe for an existing CRDT package.",
            "Write a property-based test (no external libs) asserting that "
            "counter increments are never lost under concurrent merge.",
            "Review another agent's PR and provide constructive feedback.",
        ],
        "passing_criteria": (
            "Agent can write passing unit and merge tests for an existing "
            "CRDT and explain the merge invariant."
        ),
    },
    {
        "level": 3,
        "name": "Navigator",
        "description": (
            "Implement a CRDT variant from a written specification, write "
            "merge tests, and understand gossip protocol fundamentals."
        ),
        "prerequisites": ["Greenhorn", "Deckhand"],
        "lessons": [
            "Implementing from spec — translating academic papers into code.",
            "Merge correctness — proving convergence with fork-join tests.",
            "Gossip fundamentals — anti-entropy, epidemic broadcast, and "
            "the gossip protocol abstraction in SmartCRDT.",
            "Factory pattern — how the monorepo discovers and instantiates "
            "CRDT variants.",
        ],
        "practical_exercises": [
            "Complete the 'add-counter' or 'add-set' recipe end-to-end.",
            "Implement an LWW-Register from the README spec and write merge tests.",
            "Read smartcrdt/gossip/plumtree/ and diagram its message flow.",
        ],
        "passing_criteria": (
            "Agent can implement a new CRDT variant from a specification, "
            "with full test coverage and merge convergence guarantees."
        ),
    },
    {
        "level": 4,
        "name": "Captain",
        "description": (
            "Design new CRDT types, coordinate changes across multiple "
            "packages, and review pull requests with authority."
        ),
        "prerequisites": ["Greenhorn", "Deckhand", "Navigator"],
        "lessons": [
            "CRDT design principles — composability, metadata budget, and "
            "merge-complexity trade-offs.",
            "Cross-package coordination — updating shared protocols, "
            "factory registrations, and documentation.",
            "Code review best practices — what to look for in CRDT code, "
            "common pitfalls, and style conventions.",
            "Performance profiling — identifying bottlenecks in merge paths "
            "and state serialisation.",
        ],
        "practical_exercises": [
            "Design a new CRDT type (e.g. a Bounded-Counter with upper/lower "
            "bounds) and write the full specification.",
            "Complete the 'fix-merge' recipe on a deliberately introduced bug.",
            "Lead a review of another agent's PR, providing at least 3 "
            "actionable suggestions.",
        ],
        "passing_criteria": (
            "Agent can design a novel CRDT type, write its specification, "
            "and coordinate the implementation across the monorepo."
        ),
    },
    {
        "level": 5,
        "name": "Fleet Admiral",
        "description": (
            "Make architecture decisions, coordinate the entire fleet of "
            "agents, and manage cross-repo integration with upstream and "
            "downstream projects."
        ),
        "prerequisites": ["Greenhorn", "Deckhand", "Navigator", "Captain"],
        "lessons": [
            "Architecture governance — RFC process, deprecation policy, and "
            "compatibility guarantees.",
            "Fleet coordination — task delegation, workload balancing, and "
            "conflict resolution across agents.",
            "Cross-repo integration — maintaining compatibility with "
            "transport layers, storage engines, and client SDKs.",
            "Onboarding mentorship — how to bring new agents from Greenhorn "
            "to Captain efficiently.",
        ],
        "practical_exercises": [
            "Write an RFC for a cross-cutting change (e.g. a new serialisation "
            "format) and shepherd it through the review process.",
            "Mentor a Greenhorn agent through Level 2, adapting the curriculum "
            "to their pace.",
            "Perform a cross-repo compatibility audit and produce a written "
            "report with recommendations.",
        ],
        "passing_criteria": (
            "Agent can independently drive architectural changes, mentor "
            "other agents, and represent the SmartCRDT project in cross-repo "
            "discussions."
        ),
    },
]

# ---------------------------------------------------------------------------
# Recipe ↔ bootcamp prerequisite mapping
# ---------------------------------------------------------------------------

_RECIPE_PREREQS: Dict[str, List[str]] = {
    "add-counter": ["Greenhorn", "Deckhand"],
    "add-set": ["Greenhorn", "Deckhand"],
    "add-register": ["Greenhorn", "Deckhand"],
    "add-gossip": ["Greenhorn", "Deckhand", "Navigator"],
    "add-test": ["Greenhorn"],
    "fix-merge": ["Greenhorn", "Deckhand", "Navigator"],
}


# ---------------------------------------------------------------------------
# WorkshopManager
# ---------------------------------------------------------------------------

class WorkshopManager:
    """Manages workshop recipes and the five-level bootcamp for SmartCRDT.

    The manager keeps an internal progress file (``workshop_progress.json``)
    inside the *workshop_dir*.  Agents call :meth:`mark_completed` to
    record finished recipes and bootcamp levels; :meth:`get_progress` and
    :meth:`get_next_recommendation` query that persisted state.

    Args:
        workshop_dir: Directory used to store the progress JSON file.
            Defaults to the current working directory.
    """

    def __init__(self, workshop_dir: Optional[str] = None) -> None:
        self._workshop_dir = workshop_dir or os.getcwd()
        self._progress_path = os.path.join(self._workshop_dir, _PROGRESS_FILE)
        self._recipes: Dict[str, Dict[str, Any]] = {
            r["name"]: r for r in RECIPES
        }
        self._levels: Dict[int, Dict[str, Any]] = {
            lv["level"]: lv for lv in BOOTCAMP_LEVELS
        }
        self._progress: Dict[str, Any] = self._load_progress()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_progress(self) -> Dict[str, Any]:
        """Load persisted progress from disk, or return empty structure."""
        if os.path.isfile(self._progress_path):
            try:
                with open(self._progress_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                # Ensure expected keys exist (forward-compat)
                data.setdefault("completed_recipes", [])
                data.setdefault("completed_levels", [])
                data.setdefault("timestamps", {})
                return data
            except (json.JSONDecodeError, KeyError):
                # Corrupt file — start fresh
                return self._empty_progress()
        return self._empty_progress()

    @staticmethod
    def _empty_progress() -> Dict[str, Any]:
        return {
            "completed_recipes": [],
            "completed_levels": [],
            "timestamps": {},
        }

    def _save_progress(self) -> None:
        """Atomically write progress to disk."""
        tmp_path = self._progress_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self._progress, fh, indent=2)
        os.replace(tmp_path, self._progress_path)

    # ------------------------------------------------------------------
    # Recipe accessors
    # ------------------------------------------------------------------

    def get_recipe(self, name: str) -> dict:
        """Return the recipe dictionary for *name*.

        Raises:
            KeyError: If no recipe with the given name exists.
        """
        if name not in self._recipes:
            raise KeyError(f"Unknown recipe: {name!r}")
        return dict(self._recipes[name])  # return a shallow copy

    def list_recipes(self) -> list:
        """Return a list of summary dicts for all registered recipes."""
        return [
            {
                "name": r["name"],
                "description": r["description"],
                "difficulty": r["difficulty"],
                "estimated_time_minutes": r["estimated_time_minutes"],
                "tags": r.get("tags", []),
                "step_count": len(r["steps"]),
            }
            for r in self._recipes.values()
        ]

    def run_recipe(self, name: str) -> dict:
        """Execute a recipe and return per-step results.

        Since this module manages onboarding *content* rather than an
        execution sandbox, the method validates the recipe, checks
        prerequisites, and returns a structured execution plan with
        timestamps.  Each step is marked with ``status: "pending"``
        so the calling agent can fill in outcomes as it works.

        Returns:
            A dict with keys ``recipe``, ``started_at``, ``steps``,
            ``prerequisites_met``, and ``warnings``.
        """
        if name not in self._recipes:
            raise KeyError(f"Unknown recipe: {name!r}")

        recipe = self._recipes[name]
        prereqs = self.get_prerequisites(name)
        completed_levels = set(self._progress.get("completed_levels", []))

        prereqs_met = all(p in completed_levels for p in prereqs)
        warnings: list = []
        if not prereqs_met:
            missing = [p for p in prereqs if p not in completed_levels]
            warnings.append(
                f"Prerequisites not met.  Complete levels: {missing}"
            )

        steps_result = []
        for idx, step in enumerate(recipe["steps"], start=1):
            steps_result.append({
                "step_number": idx,
                "instruction": step["instruction"],
                "file_pattern": step["file_pattern"],
                "expected_outcome": step["expected_outcome"],
                "status": "pending",
                "actual_outcome": None,
            })

        return {
            "recipe": name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prerequisites_met": prereqs_met,
            "warnings": warnings,
            "steps": steps_result,
        }

    # ------------------------------------------------------------------
    # Bootcamp accessors
    # ------------------------------------------------------------------

    def get_bootcamp_level(self, level: int) -> dict:
        """Return the full bootcamp level definition.

        Raises:
            KeyError: If *level* is not in the range 1–5.
        """
        if level not in self._levels:
            raise KeyError(f"Unknown bootcamp level: {level!r}  (valid: 1-5)")
        return dict(self._levels[level])

    def list_bootcamp_levels(self) -> list:
        """Return summary dicts for all five bootcamp levels, ordered by level."""
        return [
            {
                "level": lv["level"],
                "name": lv["name"],
                "description": lv["description"],
                "lesson_count": len(lv["lessons"]),
                "exercise_count": len(lv["practical_exercises"]),
                "prerequisites": lv["prerequisites"],
            }
            for lv in sorted(self._levels.values(), key=lambda l: l["level"])
        ]

    def assess_level(self, agent_skills: dict) -> int:
        """Assess which bootcamp level an agent currently qualifies for.

        The *agent_skills* dict maps skill names to booleans indicating
        whether the agent has demonstrated that skill.  Recognised skills
        include:

        - ``repo_layout`` — understands the monorepo structure
        - ``run_tests`` — can run the test suite locally
        - ``read_crdt`` — can read and explain core CRDT implementations
        - ``write_unit_tests`` — can write passing unit tests
        - ``merge_understanding`` — understands merge semantics and invariants
        - ``implement_variant`` — can implement a CRDT variant from spec
        - ``gossip_understanding`` — understands gossip protocols
        - ``crdt_design`` — can design novel CRDT types
        - ``cross_package`` — can coordinate changes across packages
        - ``code_review`` — can provide authoritative code reviews
        - ``architecture`` — can make architecture-level decisions
        - ``mentoring`` — can mentor other agents effectively
        - ``cross_repo`` — can manage cross-repo integration

        Args:
            agent_skills: Dict mapping skill names to ``bool`` values.

        Returns:
            An integer from 1 to 5 representing the assessed level.
        """
        # Scoring rubric: each level requires a threshold of matching skills.
        level_checks = {
            5: {"architecture", "mentoring", "cross_repo", "crdt_design", "code_review"},
            4: {"crdt_design", "cross_package", "code_review", "merge_understanding"},
            3: {"implement_variant", "merge_understanding", "gossip_understanding", "write_unit_tests"},
            2: {"read_crdt", "write_unit_tests", "run_tests", "merge_understanding"},
            1: {"repo_layout", "run_tests"},
        }

        true_skills = {k for k, v in agent_skills.items() if v}

        for level in range(5, 0, -1):
            required = level_checks[level]
            if required.issubset(true_skills):
                return level

        # Default: even a brand-new agent starts at level 1.
        return 1

    def get_learning_path(self, current_level: int) -> list:
        """Build a recommended learning path from *current_level* to Level 5.

        For each level above the current one the method returns a dict
        describing the lessons to study, the practical exercises to
        complete, and which recipes are most relevant.

        Args:
            current_level: The agent's current bootcamp level (1–5).

        Returns:
            A list of dicts, one per remaining level.
        """
        if current_level < 1:
            current_level = 1
        if current_level > 5:
            return []

        # Map bootcamp levels to recommended recipes
        level_recipe_map = {
            2: ["add-test"],
            3: ["add-counter", "add-set", "add-register"],
            4: ["fix-merge", "add-gossip"],
            5: [],  # Admiral level is self-directed
        }

        path: list = []
        for level_num in range(current_level + 1, 6):
            lv = self._levels[level_num]
            path.append({
                "target_level": level_num,
                "name": lv["name"],
                "lessons": lv["lessons"],
                "practical_exercises": lv["practical_exercises"],
                "recommended_recipes": level_recipe_map.get(level_num, []),
                "passing_criteria": lv["passing_criteria"],
            })
        return path

    # ------------------------------------------------------------------
    # Prerequisite lookup
    # ------------------------------------------------------------------

    def get_prerequisites(self, recipe_name: str) -> list:
        """Return the list of bootcamp levels required before running a recipe.

        Args:
            recipe_name: Name of the recipe to query.

        Raises:
            KeyError: If the recipe name is unknown.
        """
        if recipe_name not in self._recipes:
            raise KeyError(f"Unknown recipe: {recipe_name!r}")
        return list(_RECIPE_PREREQS.get(recipe_name, []))

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    def get_progress(self) -> dict:
        """Return a snapshot of the current workshop progress.

        The returned dict includes ``completed_recipes``,
        ``completed_levels``, ``timestamps``, and convenience
        summaries.
        """
        completed_recipes = self._progress.get("completed_recipes", [])
        completed_levels = self._progress.get("completed_levels", [])
        timestamps = self._progress.get("timestamps", {})

        total_recipes = len(self._recipes)
        total_levels = len(self._levels)

        return {
            "completed_recipes": list(completed_recipes),
            "completed_levels": list(completed_levels),
            "timestamps": dict(timestamps),
            "recipes_completed_count": len(completed_recipes),
            "recipes_total_count": total_recipes,
            "levels_completed_count": len(completed_levels),
            "levels_total_count": total_levels,
            "highest_completed_level": max(completed_levels) if completed_levels else 0,
        }

    def mark_completed(self, item_type: str, name: str) -> None:
        """Mark a recipe or bootcamp level as completed.

        Args:
            item_type: Either ``"recipe"`` or ``"level"``.
            name: For recipes the recipe name (e.g. ``"add-counter"``).
                For levels the level number (int) or level name (e.g.
                ``"Navigator"``).

        Raises:
            ValueError: If *item_type* is not recognised or *name* is invalid.
        """
        key = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if item_type == "recipe":
            if name not in self._recipes:
                raise ValueError(f"Unknown recipe: {name!r}")
            if name not in self._progress["completed_recipes"]:
                self._progress["completed_recipes"].append(name)
            self._progress["timestamps"][f"recipe:{name}"] = key

        elif item_type == "level":
            # Accept either integer level or level name
            level_num: Optional[int] = None
            if isinstance(name, int):
                level_num = name
            elif isinstance(name, str) and name.isdigit():
                level_num = int(name)
            else:
                for lv in self._levels.values():
                    if lv["name"].lower() == name.lower():
                        level_num = lv["level"]
                        break

            if level_num is None or level_num not in self._levels:
                raise ValueError(
                    f"Unknown bootcamp level: {name!r}  "
                    f"(valid levels: 1-5 or names: "
                    f"{', '.join(v['name'] for v in self._levels.values())})"
                )

            if level_num not in self._progress["completed_levels"]:
                self._progress["completed_levels"].append(level_num)
            self._progress["timestamps"][f"level:{level_num}"] = key

        else:
            raise ValueError(
                f"Unknown item_type: {item_type!r}  "
                f"(expected 'recipe' or 'level')"
            )

        self._save_progress()

    def get_next_recommendation(self) -> dict:
        """Recommend the next learning item based on current progress.

        The algorithm prefers advancing bootcamp levels first; if the
        current level is complete it recommends the next level.  Within a
        level it suggests the first uncompleted recipe that is eligible.

        Returns:
            A dict with ``type`` (``"level"`` or ``"recipe"``),
            ``name``, and a ``reason`` string.
        """
        completed_levels = set(self._progress.get("completed_levels", []))
        completed_recipes = set(self._progress.get("completed_recipes", []))

        # Determine current effective level (0 means no levels completed)
        current = 0
        for lvl in range(1, 6):
            if lvl in completed_levels:
                current = lvl

        # If not yet at level 5, suggest the next uncompleted level
        if current < 5:
            next_level = current + 1
            if next_level not in completed_levels:
                current_label = (
                    self._levels[current]["name"] if current else "unranked"
                )
                return {
                    "type": "level",
                    "name": next_level,
                    "display_name": self._levels[next_level]["name"],
                    "reason": (
                        f"You are {current_label} (level {current}).  "
                        f"Advance to level {next_level} ({self._levels[next_level]['name']}) "
                        f"to unlock harder recipes."
                    ),
                }

        # All levels complete — suggest remaining recipes
        for recipe in RECIPES:
            if recipe["name"] not in completed_recipes:
                prereqs = _RECIPE_PREREQS.get(recipe["name"], [])
                prereqs_met = all(
                    any(
                        self._levels[l]["name"] == p or self._levels[l]["level"] == p
                        for l in completed_levels
                    )
                    for p in prereqs
                )
                if prereqs_met:
                    return {
                        "type": "recipe",
                        "name": recipe["name"],
                        "display_name": recipe["name"],
                        "reason": (
                            f"Recipe '{recipe['name']}' is uncompleted and all "
                            f"prerequisites are satisfied.  "
                            f"Estimated time: {recipe['estimated_time_minutes']} min."
                        ),
                    }

        # Everything complete!
        return {
            "type": "none",
            "name": None,
            "display_name": None,
            "reason": (
                "Congratulations, Fleet Admiral!  All bootcamp levels and "
                "recipes are complete.  You are ready for self-directed "
                "architecture work."
            ),
        }
