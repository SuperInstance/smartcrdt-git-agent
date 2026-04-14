"""Monorepo Awareness Module for SmartCRDT pnpm workspace.

Package registry, dependency graph analysis, test coverage tracking,
health checks, and affected-package detection.  Python 3.9+ stdlib only.
"""
from __future__ import annotations
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set


class MonorepoAwareness:
    """Tracks the SmartCRDT monorepo package ecosystem.

    Maintains a registry of workspace packages, categories, dependencies,
    test coverage, and health metrics for rapid impact analysis.
    """

    CATEGORIES = (
        "crdt-core", "infrastructure", "ai-integration", "ui", "cli",
        "testing", "performance", "security", "learning", "native",
        "vljepa", "other",
    )

    # ── Package registry (85 packages, 12 categories) ──
    _RAW: List[tuple] = [
        ("crdt-core", "core", "counter", "set", "register",
         "vector-clock", "gossip", "map", "sequence"),
        ("infrastructure", "config", "manager", "manifest", "registry",
         "resolver", "persistence", "state", "container-cache",
         "worker-pool"),
        ("ai-integration", "langchain", "langgraph", "langgraph-state",
         "langgraph-patterns", "langgraph-errors", "langgraph-debug",
         "llamaindex", "embeddings", "vector-db", "coagents",
         "collaboration"),
        ("ui", "a2ui", "progressive-render"),
        ("cli", "cli", "app-cli", "config-cli", "manifest-cli",
         "compatibility-cli"),
        ("testing", "integration-tests", "performance-tests"),
        ("performance", "performance-optimizer", "backpressure",
         "scale-strategy", "preload-strategy"),
        ("security", "security-audit", "sanitization", "privacy", "sso"),
        ("learning", "learning", "federated-learning"),
        ("native", "crdt-native", "wasm", "webgpu-compute",
         "webgpu-memory", "webgpu-multi", "webgpu-profiler"),
        ("vljepa", "vljepa", "vljepa-abtesting", "vljepa-analytics",
         "vljepa-curriculum", "vljepa-dataset", "vljepa-edge",
         "vljepa-evolution", "vljepa-federation", "vljepa-multimodal",
         "vljepa-optimization", "vljepa-orpo", "vljepa-preference",
         "vljepa-quantization", "vljepa-registry", "vljepa-synthetic",
         "vljepa-testing", "vljepa-training", "vljepa-transfer",
         "vljepa-video", "vljepa-worldmodel"),
        ("other", "cascade", "downloader", "observability", "health-check",
         "semver", "sse-client", "sse-reconnect", "sse-server", "swarm",
         "compatibility", "superinstance", "utils"),
    ]

    @classmethod
    def _build_defs(cls) -> List[Dict[str, str]]:
        """Expand _RAW tuples into a flat list of {name, category} dicts."""
        out: List[Dict[str, str]] = []
        for cat, *names in cls._RAW:
            for n in names:
                out.append({"name": n, "category": cat})
        return out

    def __init__(self, repo_root: Optional[str] = None) -> None:
        """Initialise with optional *repo_root*; auto-builds dep graph."""
        self._step_count: int = 0
        self.repo_root: str = repo_root or ""
        self._packages: Dict[str, Dict[str, Any]] = {
            p["name"]: {**p, "path": "", "has_tests": False}
            for p in self._build_defs()
        }
        self._deps: Dict[str, Set[str]] = defaultdict(set)
        self._rdeps: Dict[str, Set[str]] = defaultdict(set)
        if self.repo_root:
            self.build_dependency_graph(self.repo_root)

    # ── Properties ──
    @property
    def step_count(self) -> int:
        """Number of analysis steps performed so far."""
        return self._step_count

    @property
    def total_packages(self) -> int:
        """Total number of registered packages."""
        return len(self._packages)

    # ── Package registry ──
    def get_packages(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return packages, optionally filtered by *category*."""
        self._step_count += 1
        pkgs = list(self._packages.values())
        if category:
            pkgs = [p for p in pkgs if p["category"] == category]
        return sorted(pkgs, key=lambda p: p["name"])

    def get_package_info(self, pkg_name: str) -> Dict[str, Any]:
        """Return metadata for a single package (empty dict if unknown)."""
        self._step_count += 1
        return self._packages.get(pkg_name, {})

    def get_category_summary(self) -> Dict[str, int]:
        """Return ``{category: count}`` for every category."""
        self._step_count += 1
        summary: Dict[str, int] = defaultdict(int)
        for p in self._packages.values():
            summary[p["category"]] += 1
        return dict(summary)

    # ── Dependency graph ──
    def build_dependency_graph(self, repo_root: str) -> Dict[str, List[str]]:
        """Scan ``packages/*/package.json`` files and build the dep graph.

        Reads ``dependencies``, ``devDependencies``, ``peerDependencies``
        and keeps only refs that match known workspace packages.

        Returns:
            Mapping of package name → sorted list of internal deps.
        """
        self._step_count += 1
        self.repo_root = repo_root
        self._deps.clear()
        self._rdeps.clear()
        scope = "@smartcrdt/"

        for pkg_name, meta in self._packages.items():
            pkg_dir = self._resolve_pkg_dir(pkg_name, repo_root)
            meta["path"] = pkg_dir
            pj = os.path.join(pkg_dir, "package.json")
            if not os.path.isfile(pj):
                continue
            with open(pj, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            internal: Set[str] = set()
            for field in ("dependencies", "devDependencies", "peerDependencies"):
                for dep in data.get(field, {}):
                    base = dep.removeprefix(scope)
                    if base in self._packages:
                        internal.add(base)
            self._deps[pkg_name] = internal

        for pkg, deps in self._deps.items():
            for dep in deps:
                self._rdeps[dep].add(pkg)
        return {k: sorted(v) for k, v in sorted(self._deps.items())}

    def get_dependencies(self, pkg: str) -> List[str]:
        """Direct internal dependencies of *pkg*."""
        self._step_count += 1
        return sorted(self._deps.get(pkg, set()))

    def get_reverse_dependencies(self, pkg: str) -> List[str]:
        """Packages that directly depend on *pkg*."""
        self._step_count += 1
        return sorted(self._rdeps.get(pkg, set()))

    def get_transitive_dependents(self, pkg: str) -> List[str]:
        """Full downstream tree — every package transitively depending on *pkg*."""
        self._step_count += 1
        visited: Set[str] = set()
        stack = list(self._rdeps.get(pkg, set()))
        while stack:
            cur = stack.pop()
            if cur not in visited:
                visited.add(cur)
                stack.extend(self._rdeps.get(cur, set()))
        return sorted(visited)

    def dependency_exists(self, pkg_a: str, pkg_b: str) -> bool:
        """Return ``True`` if *pkg_b* directly depends on *pkg_a*."""
        self._step_count += 1
        return pkg_a in self._deps.get(pkg_b, set())

    # ── Test coverage ──
    def refresh_test_coverage(self) -> Dict[str, Any]:
        """Walk each package dir and detect test files.

        Looks for ``__tests__``, ``tests/``, ``test/``, ``spec/``,
        or ``*.test.ts`` / ``*.spec.ts`` etc.  Returns a summary dict
        with ``total``, ``tested``, ``untested``, ``coverage_pct``,
        and ``untested_packages``.
        """
        self._step_count += 1
        tested = 0
        untested_pkgs: List[str] = []
        for name, meta in self._packages.items():
            d = meta["path"] or self._resolve_pkg_dir(name, self.repo_root)
            has = self._has_tests(d)
            meta["has_tests"] = has
            if has:
                tested += 1
            else:
                untested_pkgs.append(name)
        total = len(self._packages)
        return {
            "total": total,
            "tested": tested,
            "untested": total - tested,
            "coverage_pct": round(tested / total * 100, 1) if total else 0,
            "untested_packages": sorted(untested_pkgs),
        }

    @staticmethod
    def _has_tests(pkg_dir: str) -> bool:
        """Heuristic: does *pkg_dir* contain any test artefacts?"""
        if not pkg_dir or not os.path.isdir(pkg_dir):
            return False
        markers = ("__tests__", "tests", "test", "spec")
        for entry in os.listdir(pkg_dir):
            full = os.path.join(pkg_dir, entry)
            if os.path.isdir(full) and entry in markers:
                return True
            if os.path.isfile(full) and any(
                entry.endswith(s)
                for s in (".test.ts", ".test.js", ".spec.ts", ".spec.js")
            ):
                return True
        return False

    # ── Health check ──
    def health_check(self) -> Dict[str, Any]:
        """Return an overall monorepo health assessment.

        Checks orphaned packages, missing dependencies, category
        distribution, and test coverage.  Status is ``\"healthy\"``
        when there are no missing deps and ≤ 3 orphaned packages.
        """
        self._step_count += 1
        all_names = set(self._packages)
        orphaned = sorted(
            n for n in all_names
            if not self._deps.get(n) and not self._rdeps.get(n)
        )
        missing: Set[str] = set()
        for deps in self._deps.values():
            missing.update(deps - all_names)
        coverage = self.refresh_test_coverage()
        return {
            "status": ("healthy" if not missing and len(orphaned) <= 3
                       else "attention"),
            "total_packages": len(all_names),
            "orphaned_packages": orphaned,
            "orphaned_count": len(orphaned),
            "missing_dependencies": sorted(missing),
            "missing_dependency_count": len(missing),
            "categories": self.get_category_summary(),
            "test_coverage": coverage,
            "dependency_graph_edges": sum(len(v) for v in self._deps.values()),
        }

    # ── Affected package detection ──
    def identify_affected_packages(self, changed_files: List[str]) -> List[Dict[str, Any]]:
        """Determine which packages are affected by changed files.

        Maps each file to its package via the ``packages/<name>/`` prefix,
        then collects all transitive dependents via BFS with depth tracking.

        Args:
            changed_files: Absolute or repo-root-relative file paths.

        Returns:
            List sorted by descending impact level.  Each entry has
            ``name``, ``category``, ``impact_level``, ``directly_changed``.
        """
        self._step_count += 1
        if not self.repo_root:
            return []
        directly_changed: Set[str] = set()
        for fpath in changed_files:
            rel = os.path.relpath(fpath, self.repo_root)
            parts = rel.split(os.sep)
            if len(parts) >= 2 and parts[0] == "packages":
                pkg_name = parts[1]
                if pkg_name in self._packages:
                    directly_changed.add(pkg_name)
        impact: Dict[str, int] = {}
        for pkg in directly_changed:
            visited: Set[str] = set()
            queue: List[tuple[str, int]] = [(pkg, 0)]
            while queue:
                cur, depth = queue.pop(0)
                if cur in visited:
                    continue
                visited.add(cur)
                impact[cur] = max(impact.get(cur, -1), depth)
                for rdep in self._rdeps.get(cur, set()):
                    queue.append((rdep, depth + 1))
        return [
            {
                "name": name,
                "category": self._packages[name]["category"],
                "impact_level": depth,
                "directly_changed": name in directly_changed,
            }
            for name, depth in sorted(impact.items(), key=lambda x: -x[1])
        ]

    # ── Internal helpers ──
    @staticmethod
    def _resolve_pkg_dir(pkg_name: str, repo_root: str) -> str:
        """Resolve a package name to ``<repo_root>/packages/<name>``."""
        return os.path.join(repo_root, "packages", pkg_name)
