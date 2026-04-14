"""Fleet repository cartographer for the SmartCRDT git-agent.

Maps dependencies and change impact across 912+ fleet repositories.
Maintains a reactive dependency graph, computes change-impact analysis,
detects circular dependencies, orphans, and produces fleet-wide health
scores and topological build/deployment orderings.

Designed based on roundtable simulation findings for multi-repo fleet
coordination where agents need to understand cross-repo blast radius
before making changes.

Usage::

    cartographer = RepoCartographer()

    # Index repos with metadata
    cartographer.index_repo("crdt-core", {"language": "python", "test_count": 84})
    cartographer.index_repo("api-gateway", {"language": "typescript", "test_count": 42})

    # Declare dependencies
    cartographer.add_dependency("api-gateway", "crdt-core", strength="strong")

    # Analyse change impact
    impact = cartographer.get_impact_analysis("crdt-core")
    print(impact["affected_repos"])  # ["api-gateway", ...]

    # Check for cycles and orphans
    cycles = cartographer.detect_cycles()
    orphans = cartographer.find_orphans()

    # Get fleet health
    health = cartographer.compute_fleet_health()

Python 3.9+ stdlib only — zero external dependencies.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_STRENGTHS: Tuple[str, ...] = ("strong", "weak")
_STALENESS_THRESHOLD_DAYS: int = 30
_HEALTH_WEIGHT_TEST: float = 0.4
_HEALTH_WEIGHT_FRESHNESS: float = 0.3
_HEALTH_WEIGHT_DEP_HEALTH: float = 0.3
_NO_DEP_HEALTH_FLOOR: float = 0.2
_DEFAULT_HEALTH: float = 0.5
_DEFAULT_LAST_COMMIT_DAYS_AGO: int = 7


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RepoMetadata:
    """Metadata tracked for each indexed repository.

    Attributes
    ----------
    language :
        Primary programming language (e.g. ``"python"``, ``"typescript"``).
    test_count :
        Number of known tests in the repository.
    last_commit_time :
        UTC ISO-8601 timestamp of the most recent commit, or ``None``
        which defaults to "7 days ago".
    has_tests :
        Whether the repo has any tests at all.
    extra :
        Arbitrary additional metadata supplied by the caller.
    """

    language: str = "unknown"
    test_count: int = 0
    last_commit_time: Optional[str] = None
    has_tests: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyEdge:
    """A directed dependency edge between two repositories.

    Attributes
    ----------
    strength :
        ``"strong"`` for direct API/library dependencies; ``"weak"``
        for optional, indirect, or loosely-coupled relationships.
    """

    strength: str = "strong"


# ---------------------------------------------------------------------------
# RepoCartographer
# ---------------------------------------------------------------------------


class RepoCartographer:
    """Map, analyse, and score dependencies across a fleet of repositories.

    Maintains a reactive directed graph where nodes are repositories and
    edges represent ``from_repo → to_repo`` (``from_repo`` depends on
    ``to_repo``).  All operations are pure in-memory with no I/O
    requirements.

    Parameters
    ----------
    repos :
        Optional initial mapping of ``repo_name → metadata_dict`` to
        seed the graph.
    """

    def __init__(
        self,
        repos: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        # Core data structures
        self._metadata: Dict[str, RepoMetadata] = {}
        self._dependencies: Dict[str, Dict[str, DependencyEdge]] = defaultdict(dict)
        self._dependents: Dict[str, Dict[str, DependencyEdge]] = defaultdict(dict)

        # Cache for computed health scores (invalidated on graph mutation)
        self._health_cache: Dict[str, float] = {}
        self._health_dirty: bool = True

        # Seed initial repos if provided
        if repos:
            for name, meta in repos.items():
                self.index_repo(name, meta)

    # ------------------------------------------------------------------
    # Public API — repo management
    # ------------------------------------------------------------------

    def index_repo(self, repo_name: str, metadata: Optional[Dict[str, Any]] = None) -> dict:
        """Add or update a repository in the dependency graph.

        Parameters
        ----------
        repo_name :
            Unique identifier for the repository (e.g. ``"crdt-core"``).
        metadata :
            Optional dict of metadata fields.  Recognised keys:

            * ``language`` (``str``) — primary language.
            * ``test_count`` (``int``) — number of tests.
            * ``last_commit_time`` (``str``) — ISO-8601 UTC timestamp.
            * ``has_tests`` (``bool``) — explicit flag; auto-detected from
              ``test_count > 0`` when omitted.
            * Any additional keys are stored in ``extra``.

        Returns
        -------
        dict
            Confirmation with ``"repo"``, ``"indexed"``, and ``"metadata"`` keys.
        """
        meta_input = metadata or {}
        existing = self._metadata.get(repo_name)

        # Merge: new values overwrite, old values preserved for missing keys
        lang = meta_input.get("language", existing.language if existing else "unknown")
        test_count = meta_input.get("test_count", existing.test_count if existing else 0)
        last_commit = meta_input.get(
            "last_commit_time",
            existing.last_commit_time if existing else None,
        )
        has_tests = meta_input.get(
            "has_tests",
            existing.has_tests if existing else test_count > 0,
        )

        # Build extra from any keys we don't explicitly handle
        _HANDLED_KEYS = {"language", "test_count", "last_commit_time", "has_tests"}
        extra: Dict[str, Any] = {}
        if existing:
            extra.update(existing.extra)
        for k, v in meta_input.items():
            if k not in _HANDLED_KEYS:
                extra[k] = v

        self._metadata[repo_name] = RepoMetadata(
            language=lang,
            test_count=int(test_count),
            last_commit_time=last_commit,
            has_tests=bool(has_tests),
            extra=extra,
        )

        # Ensure reverse-lookup dicts have entries even if empty
        self._dependencies.setdefault(repo_name, {})
        self._dependents.setdefault(repo_name, {})

        self._health_dirty = True

        return {
            "repo": repo_name,
            "indexed": True,
            "metadata": self._serialize_metadata(self._metadata[repo_name]),
        }

    # ------------------------------------------------------------------
    # Public API — dependency management
    # ------------------------------------------------------------------

    def add_dependency(
        self,
        from_repo: str,
        to_repo: str,
        strength: str = "strong",
    ) -> dict:
        """Add a directed dependency edge: *from_repo* depends on *to_repo*.

        If either repository has not been indexed yet it is auto-created
        with default metadata.

        Parameters
        ----------
        from_repo :
            Repository that has the dependency.
        to_repo :
            Repository being depended upon.
        strength :
            ``"strong"`` (default) or ``"weak"``.

        Returns
        -------
        dict
            Confirmation with ``"from"``, ``"to"``, ``"strength"``, and
            ``"added"`` keys.

        Raises
        ------
        ValueError
            If *strength* is not ``"strong"`` or ``"weak"``, or if
            ``from_repo == to_repo`` (self-dependency).
        """
        if strength not in _VALID_STRENGTHS:
            raise ValueError(
                f"Invalid strength {strength!r}; expected one of {_VALID_STRENGTHS}"
            )
        if from_repo == to_repo:
            raise ValueError(
                f"Self-dependency not allowed: {from_repo!r} → {to_repo!r}"
            )

        # Auto-index unknown repos
        if from_repo not in self._metadata:
            self.index_repo(from_repo)
        if to_repo not in self._metadata:
            self.index_repo(to_repo)

        edge = DependencyEdge(strength=strength)
        self._dependencies[from_repo][to_repo] = edge
        self._dependents[to_repo][from_repo] = edge

        self._health_dirty = True

        return {
            "from": from_repo,
            "to": to_repo,
            "strength": strength,
            "added": True,
        }

    def remove_dependency(self, from_repo: str, to_repo: str) -> dict:
        """Remove a directed dependency edge.

        Parameters
        ----------
        from_repo :
            Repository that was depending on *to_repo*.
        to_repo :
            Repository that was being depended upon.

        Returns
        -------
        dict
            Confirmation with ``"from"``, ``"to"``, and ``"removed"``
            (``False`` if the edge did not exist).
        """
        removed = False

        if to_repo in self._dependencies.get(from_repo, {}):
            del self._dependencies[from_repo][to_repo]
            removed = True
        if from_repo in self._dependents.get(to_repo, {}):
            del self._dependents[to_repo][from_repo]

        self._health_dirty = True

        return {"from": from_repo, "to": to_repo, "removed": removed}

    # ------------------------------------------------------------------
    # Public API — impact analysis
    # ------------------------------------------------------------------

    def get_impact_analysis(self, repo_name: str, depth: int = 3) -> dict:
        """Analyse which repos are affected by changes to *repo_name*.

        Performs a breadth-first traversal of **dependents** (repos that
        depend on *repo_name*) up to the given *depth*.  At each level the
        method records which repos are directly and transitively affected.

        Parameters
        ----------
        repo_name :
            Repository whose change is being analysed.
        depth :
            Maximum traversal depth (default 3).  A depth of 1 returns
            only direct dependents.

        Returns
        -------
        dict
            Structured impact analysis with:

            * ``repo`` — the source repository.
            * ``total_affected`` — count of uniquely affected repos.
            * ``affected_repos`` — flat list of all affected repo names.
            * ``by_depth`` — dict mapping ``depth_level → list[repo]``.
            * ``strong_edges`` — count of strong dependency edges traversed.
            * ``weak_edges`` — count of weak dependency edges traversed.
        """
        if repo_name not in self._metadata:
            return {
                "repo": repo_name,
                "total_affected": 0,
                "affected_repos": [],
                "by_depth": {},
                "strong_edges": 0,
                "weak_edges": 0,
                "error": f"Repository {repo_name!r} is not indexed",
            }

        discovered: Set[str] = {repo_name}  # nodes seen (enqueued or processed)
        by_depth: Dict[int, List[str]] = {}
        strong_count = 0
        weak_count = 0

        queue: deque[Tuple[str, int]] = deque([(repo_name, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            for dependent, edge in self._dependents.get(current, {}).items():
                if dependent in discovered:
                    continue
                discovered.add(dependent)

                if edge.strength == "strong":
                    strong_count += 1
                else:
                    weak_count += 1

                actual_depth = current_depth + 1
                by_depth.setdefault(actual_depth, []).append(dependent)

                # Continue BFS from this dependent
                if actual_depth < depth:
                    queue.append((dependent, actual_depth))

        # Collect affected repos (already deduplicated via discovered set)
        deduped_depth: Dict[int, List[str]] = {}
        seen_global: Set[str] = set()
        for d in sorted(by_depth.keys()):
            unique_at_d = [r for r in by_depth[d] if r not in seen_global]
            deduped_depth[d] = unique_at_d
            seen_global.update(unique_at_d)

        all_affected = list(seen_global)
        all_affected.sort()

        return {
            "repo": repo_name,
            "total_affected": len(all_affected),
            "affected_repos": all_affected,
            "by_depth": {str(k): v for k, v in deduped_depth.items()},
            "strong_edges": strong_count,
            "weak_edges": weak_count,
        }

    # ------------------------------------------------------------------
    # Public API — dependency chains
    # ------------------------------------------------------------------

    def get_dependency_chain(self, repo_name: str) -> dict:
        """Compute the full transitive dependency chain for *repo_name*.

        Returns every repository that *repo_name* (transitively) depends
        on, organised by distance from the root.

        Parameters
        ----------
        repo_name :
            Repository whose dependencies are being resolved.

        Returns
        -------
        dict
            ``repo``, ``total_dependencies``, ``direct_dependencies``,
            ``transitive_dependencies``, ``chain_by_depth`` (dict mapping
            depth to list of repos), and ``has_cycles`` flag.
        """
        if repo_name not in self._metadata:
            return {
                "repo": repo_name,
                "total_dependencies": 0,
                "direct_dependencies": [],
                "transitive_dependencies": [],
                "chain_by_depth": {},
                "has_cycles": False,
                "error": f"Repository {repo_name!r} is not indexed",
            }

        # Detect cycles in the subgraph reachable from repo_name via DFS.
        dfs_visited: Set[str] = set()
        on_stack: Set[str] = set()

        def _has_cycle_from(node: str) -> bool:
            """Return True if a cycle exists reachable from *node*."""
            dfs_visited.add(node)
            on_stack.add(node)
            for dep in self._dependencies.get(node, {}):
                if dep not in dfs_visited:
                    if _has_cycle_from(dep):
                        return True
                elif dep in on_stack:
                    return True
            on_stack.discard(node)
            return False

        has_cycle = _has_cycle_from(repo_name)

        # BFS to collect chain_by_depth
        visited: Set[str] = set()
        chain_by_depth: Dict[int, List[str]] = {}
        queue: deque[Tuple[str, int]] = deque([(repo_name, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            for dep, edge in self._dependencies.get(current, {}).items():
                if dep in visited:
                    continue
                actual_depth = current_depth + 1
                chain_by_depth.setdefault(actual_depth, []).append(dep)
                queue.append((dep, actual_depth))

        direct = list(self._dependencies.get(repo_name, {}).keys())
        direct.sort()
        all_deps = visited - {repo_name}
        transitive = sorted(all_deps - set(direct))

        # Deduplicate chain_by_depth (skip empty depth levels)
        deduped: Dict[int, List[str]] = {}
        seen: Set[str] = set()
        for d in sorted(chain_by_depth.keys()):
            unique = [r for r in chain_by_depth[d] if r not in seen]
            if unique:
                deduped[d] = sorted(unique)
                seen.update(unique)

        return {
            "repo": repo_name,
            "total_dependencies": len(all_deps),
            "direct_dependencies": direct,
            "transitive_dependencies": transitive,
            "chain_by_depth": {str(k): v for k, v in deduped.items()},
            "has_cycles": has_cycle,
        }

    # ------------------------------------------------------------------
    # Public API — cycle detection
    # ------------------------------------------------------------------

    def detect_cycles(self) -> List[List[str]]:
        """Find all circular dependency groups in the fleet graph.

        Uses Tarjan's strongly-connected-components algorithm.  Every
        SCC with more than one member represents a circular dependency.
        Self-loops are excluded by construction (``add_dependency``
        rejects them).

        Returns
        -------
        list[list[str]]
            A list of cycles, where each cycle is a sorted list of
            repository names forming the strongly connected component.
            Returns an empty list when no cycles exist.
        """
        index_counter: List[int] = [0]
        stack: List[str] = []
        on_stack: Set[str] = set()
        indices: Dict[str, int] = {}
        lowlinks: Dict[str, int] = {}
        sccs: List[List[str]] = []

        def strongconnect(v: str) -> None:
            """Tarjan recursive step for node *v*."""
            indices[v] = index_counter[0]
            lowlinks[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in self._dependencies.get(v, {}):
                if w not in indices:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])

            # Root of an SCC
            if lowlinks[v] == indices[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    sccs.append(sorted(scc))

        for repo in self._metadata:
            if repo not in indices:
                strongconnect(repo)

        return sorted(sccs, key=lambda c: (len(c), c))

    # ------------------------------------------------------------------
    # Public API — orphan detection
    # ------------------------------------------------------------------

    def find_orphans(self) -> List[str]:
        """Find repositories with no dependencies and no dependents.

        These repos are completely disconnected from the fleet dependency
        graph and may represent standalone tools, archived projects, or
        newly onboarded repos that have not yet been wired up.

        Returns
        -------
        list[str]
            Sorted list of orphaned repository names.
        """
        orphans: List[str] = []
        for repo in self._metadata:
            has_deps = bool(self._dependencies.get(repo))
            has_dependents = bool(self._dependents.get(repo))
            if not has_deps and not has_dependents:
                orphans.append(repo)
        return sorted(orphans)

    # ------------------------------------------------------------------
    # Public API — topological sort
    # ------------------------------------------------------------------

    def topological_sort(self) -> List[str]:
        """Compute build/deployment ordering via Kahn's algorithm.

        Repos with no remaining dependencies come first.  If the graph
        contains cycles the affected repos are appended at the end in
        alphabetical order (they cannot be linearly ordered).

        Returns
        -------
        list[str]
            Ordered list of repository names.  All indexed repos are
            included exactly once.
        """
        # Compute in-degrees
        in_degree: Dict[str, int] = {repo: 0 for repo in self._metadata}
        for repo in self._metadata:
            for dep in self._dependencies.get(repo, {}):
                if dep in in_degree:
                    in_degree[repo] = in_degree.get(repo, 0)  # ensure exists
                    # dep is what `repo` depends on, so `repo` has in-degree
                    # from dep's perspective. Actually in-degree should count
                    # how many things depend on repo for build order.
                    # Wait — for build order, repo should be built AFTER its
                    # dependencies.  So in-degree = number of dependencies.
                    pass

        # Recompute properly: in-degree = count of dependencies
        in_degree = {repo: 0 for repo in self._metadata}
        for repo in self._metadata:
            in_degree[repo] = len(self._dependencies.get(repo, {}))

        # Build reverse adjacency: dependency → list of dependents
        adj: Dict[str, List[str]] = defaultdict(list)
        for repo in self._metadata:
            for dep in self._dependencies.get(repo, {}):
                adj[dep].append(repo)

        # Kahn's algorithm
        queue: deque[str] = deque(
            sorted(r for r, deg in in_degree.items() if deg == 0)
        )
        result: List[str] = []
        visited: Set[str] = set()

        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            result.append(node)

            for dependent in sorted(adj.get(node, [])):
                if dependent in visited:
                    continue
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Append any remaining (cycle members) alphabetically
        remaining = sorted(r for r in self._metadata if r not in visited)
        result.extend(remaining)

        return result

    # ------------------------------------------------------------------
    # Public API — fleet health
    # ------------------------------------------------------------------

    def compute_fleet_health(self) -> dict:
        """Compute aggregate health metrics for the entire fleet.

        Calculates per-repo health scores and then derives fleet-wide
        statistics: mean, median, worst-repo, health distribution, and
        language-level breakdowns.

        Returns
        -------
        dict
            Fleet health report with keys:

            * ``total_repos`` — number of indexed repositories.
            * ``fleet_health_score`` — weighted mean health (0.0–1.0).
            * ``mean_health`` — arithmetic mean.
            * ``median_health`` — median value.
            * ``worst_repos`` — bottom 5 repos by health.
            * ``best_repos`` — top 5 repos by health.
            * ``distribution`` — counts by health bracket (healthy/warning/critical).
            * ``by_language`` — mean health per primary language.
            * ``repo_scores`` — full mapping of ``repo → score``.
        """
        scores = self._compute_all_health_scores()

        if not scores:
            return {
                "total_repos": 0,
                "fleet_health_score": 0.0,
                "mean_health": 0.0,
                "median_health": 0.0,
                "worst_repos": [],
                "best_repos": [],
                "distribution": {"healthy": 0, "warning": 0, "critical": 0},
                "by_language": {},
                "repo_scores": {},
            }

        sorted_repos = sorted(scores.items(), key=lambda x: x[1])
        values = [s for _, s in sorted_repos]
        n = len(values)
        mean = sum(values) / n
        median = (
            values[n // 2]
            if n % 2 == 1
            else (values[n // 2 - 1] + values[n // 2]) / 2
        )

        worst = [
            {"repo": r, "score": round(s, 4)}
            for r, s in sorted_repos[:5]
        ]
        best = [
            {"repo": r, "score": round(s, 4)}
            for r, s in sorted_repos[-5:][::-1]
        ]

        healthy = sum(1 for v in values if v >= 0.7)
        warning = sum(1 for v in values if 0.4 <= v < 0.7)
        critical = sum(1 for v in values if v < 0.4)

        # Language breakdown
        lang_scores: Dict[str, List[float]] = defaultdict(list)
        for repo, score in scores.items():
            lang = self._metadata[repo].language
            lang_scores[lang].append(score)
        by_language = {
            lang: round(sum(sv) / len(sv), 4)
            for lang, sv in sorted(lang_scores.items())
        }

        return {
            "total_repos": n,
            "fleet_health_score": round(mean, 4),
            "mean_health": round(mean, 4),
            "median_health": round(median, 4),
            "worst_repos": worst,
            "best_repos": best,
            "distribution": {
                "healthy": healthy,
                "warning": warning,
                "critical": critical,
            },
            "by_language": by_language,
            "repo_scores": {r: round(s, 4) for r, s in sorted(scores.items())},
        }

    # ------------------------------------------------------------------
    # Public API — cluster map
    # ------------------------------------------------------------------

    def get_cluster_map(self) -> dict:
        """Group repositories into dependency clusters.

        A cluster is a maximal set of repositories connected through
        dependency edges (ignoring direction — both ``A → B`` and
        ``B → A`` contribute to connectivity).  Uses union-find for
        efficient connected-component detection.

        Returns
        -------
        dict
            Cluster map with:

            * ``total_clusters`` — number of distinct clusters.
            * ``clusters`` — list of cluster dicts, each containing:
              ``id``, ``repos``, ``size``, ``leader`` (most-depended-upon
              repo), and ``internal_edges`` (count).
            * ``largest_cluster`` — the biggest cluster's details.
            * ``singleton_count`` — number of isolated repos (size 1).
        """
        parent: Dict[str, str] = {}
        rank: Dict[str, int] = {}

        def find(x: str) -> str:
            """Find root of *x* with path compression."""
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path halving
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            """Merge sets containing *a* and *b*."""
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            if rank[ra] == rank[rb]:
                rank[ra] += 1

        # Initialise union-find for all repos
        for repo in self._metadata:
            parent[repo] = repo
            rank[repo] = 0

        # Unite all connected repos (bidirectional)
        for repo, deps in self._dependencies.items():
            for dep in deps:
                if dep in parent:
                    union(repo, dep)

        # Collect clusters
        clusters_raw: Dict[str, List[str]] = defaultdict(list)
        for repo in self._metadata:
            root = find(repo)
            clusters_raw[root].append(repo)

        clusters: List[Dict[str, Any]] = []
        for root, members in clusters_raw.items():
            members_sorted = sorted(members)
            # Leader = most depended-upon repo in cluster
            leader = max(
                members_sorted,
                key=lambda r: len(self._dependents.get(r, {})),
            )
            # Count internal edges (both directions, deduplicated)
            internal_edges = 0
            member_set = set(members)
            for r in members:
                for dep in self._dependencies.get(r, {}):
                    if dep in member_set:
                        internal_edges += 1
            internal_edges = internal_edges // 2  # each edge counted from both sides

            clusters.append({
                "id": root,
                "repos": members_sorted,
                "size": len(members_sorted),
                "leader": leader,
                "internal_edges": internal_edges,
            })

        # Sort clusters by size descending, then id ascending
        clusters.sort(key=lambda c: (-c["size"], c["id"]))

        largest = clusters[0] if clusters else None
        singletons = sum(1 for c in clusters if c["size"] == 1)

        return {
            "total_clusters": len(clusters),
            "clusters": clusters,
            "largest_cluster": largest,
            "singleton_count": singletons,
        }

    # ------------------------------------------------------------------
    # Public API — individual repo health
    # ------------------------------------------------------------------

    def get_repo_health(self, repo_name: str) -> dict:
        """Compute a detailed health breakdown for a single repository.

        The health score (0.0–1.0) is a weighted combination of three
        factors:

        * **test_coverage_factor** — ``0.3 * has_tests + 0.7 * (test_count /
          max_test_count)`` capped at 1.0.
        * **freshness_factor** — ``max(0, 1 - days_since_last_commit / 30)``.
        * **dependency_health_factor** — mean health of direct dependencies,
          with a floor of 0.2 for repos with no dependencies.

        Final score: ``0.4 * test + 0.3 * freshness + 0.3 * dep_health``.

        Parameters
        ----------
        repo_name :
            Repository to score.

        Returns
        -------
        dict
            Health report with ``repo``, ``health_score``, and a breakdown
            of each factor.
        """
        if repo_name not in self._metadata:
            return {
                "repo": repo_name,
                "health_score": 0.0,
                "error": f"Repository {repo_name!r} is not indexed",
                "test_coverage_factor": 0.0,
                "freshness_factor": 0.0,
                "dependency_health_factor": 0.0,
                "rating": "unknown",
            }

        meta = self._metadata[repo_name]

        # --- test_coverage_factor ---
        max_test_count = max(
            (m.test_count for m in self._metadata.values()), default=1
        )
        max_test_count = max(max_test_count, 1)  # avoid division by zero
        has_tests_factor = 0.3 if meta.has_tests else 0.0
        test_ratio_factor = min(1.0, meta.test_count / max_test_count) * 0.7
        test_coverage_factor = min(1.0, has_tests_factor + test_ratio_factor)

        # --- freshness_factor ---
        days_since = self._days_since_last_commit(meta.last_commit_time)
        freshness_factor = max(0.0, 1.0 - days_since / _STALENESS_THRESHOLD_DAYS)

        # --- dependency_health_factor ---
        deps = self._dependencies.get(repo_name, {})
        all_scores = self._compute_all_health_scores()
        if deps:
            dep_scores = [all_scores[d] for d in deps if d in all_scores]
            dependency_health_factor = (
                sum(dep_scores) / len(dep_scores) if dep_scores else _NO_DEP_HEALTH_FLOOR
            )
        else:
            dependency_health_factor = _NO_DEP_HEALTH_FLOOR

        # --- composite ---
        health_score = (
            _HEALTH_WEIGHT_TEST * test_coverage_factor
            + _HEALTH_WEIGHT_FRESHNESS * freshness_factor
            + _HEALTH_WEIGHT_DEP_HEALTH * dependency_health_factor
        )
        health_score = max(0.0, min(1.0, health_score))

        if health_score >= 0.7:
            rating = "healthy"
        elif health_score >= 0.4:
            rating = "warning"
        else:
            rating = "critical"

        return {
            "repo": repo_name,
            "health_score": round(health_score, 4),
            "test_coverage_factor": round(test_coverage_factor, 4),
            "freshness_factor": round(freshness_factor, 4),
            "dependency_health_factor": round(dependency_health_factor, 4),
            "rating": rating,
            "days_since_last_commit": days_since,
            "language": meta.language,
            "test_count": meta.test_count,
            "dependency_count": len(deps),
            "dependent_count": len(self._dependents.get(repo_name, {})),
        }

    # ------------------------------------------------------------------
    # Public API — merge order
    # ------------------------------------------------------------------

    def suggest_merge_order(self, changed_repos: List[str]) -> List[str]:
        """Suggest an optimal merge order for a set of changed repositories.

        Dependencies must be merged before their dependents.  The
        algorithm:

        1. Collect the transitive closure of dependencies for each
           changed repo.
        2. Topologically sort the resulting subgraph.
        3. Repos not in the changed set are included only when they are
           required dependencies.

        Parameters
        ----------
        changed_repos :
            Repository names that have pending changes.

        Returns
        -------
        list[str]
            Ordered list of repo names.  Changed repos appear towards
            the end (after their dependencies).  Unchanged dependency
            repos that must be merged first are prefixed with a
            ``"~"`` marker.
        """
        if not changed_repos:
            return []

        # Filter to known repos
        known_changed = [r for r in changed_repos if r in self._metadata]
        if not known_changed:
            return sorted(changed_repos)

        # Collect all transitive dependencies of changed repos
        all_relevant: Set[str] = set(known_changed)
        queue: deque[str] = deque(known_changed)
        while queue:
            current = queue.popleft()
            for dep in self._dependencies.get(current, {}):
                if dep not in all_relevant and dep in self._metadata:
                    all_relevant.add(dep)
                    queue.append(dep)

        changed_set = set(known_changed)

        # Compute in-degrees within the relevant subgraph
        in_degree: Dict[str, int] = {r: 0 for r in all_relevant}
        for r in all_relevant:
            for dep in self._dependencies.get(r, {}):
                if dep in all_relevant:
                    in_degree[r] += 1

        # Build adjacency (dependency → dependents)
        adj: Dict[str, List[str]] = defaultdict(list)
        for r in all_relevant:
            for dep in self._dependencies.get(r, {}):
                if dep in all_relevant:
                    adj[dep].append(r)

        # Kahn's algorithm on the relevant subgraph
        topo_queue: deque[str] = deque(
            sorted(r for r, d in in_degree.items() if d == 0)
        )
        result: List[str] = []
        visited: Set[str] = set()

        while topo_queue:
            node = topo_queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            result.append(node)

            for dependent in sorted(adj.get(node, [])):
                if dependent in visited:
                    continue
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    topo_queue.append(dependent)

        # Remaining (cycle members)
        remaining = sorted(r for r in all_relevant if r not in visited)
        result.extend(remaining)

        # Mark unchanged dependency repos
        marked = [
            f"~{r}" if r not in changed_set else r
            for r in result
        ]

        return marked

    # ------------------------------------------------------------------
    # Public API — graph queries
    # ------------------------------------------------------------------

    def get_all_repos(self) -> List[str]:
        """Return a sorted list of all indexed repository names."""
        return sorted(self._metadata.keys())

    def get_dependencies(self, repo_name: str) -> Dict[str, str]:
        """Return direct dependencies of *repo_name* with strengths.

        Returns
        -------
        dict
            ``{dependency_repo: strength}`` mapping.
        """
        return {
            dep: edge.strength
            for dep, edge in self._dependencies.get(repo_name, {}).items()
        }

    def get_dependents(self, repo_name: str) -> Dict[str, str]:
        """Return direct dependents of *repo_name* with strengths.

        Returns
        -------
        dict
            ``{dependent_repo: strength}`` mapping.
        """
        return {
            dep: edge.strength
            for dep, edge in self._dependents.get(repo_name, {}).items()
        }

    def get_repo_count(self) -> int:
        """Return the number of indexed repositories."""
        return len(self._metadata)

    def get_edge_count(self) -> int:
        """Return the total number of dependency edges in the graph."""
        return sum(len(deps) for deps in self._dependencies.values())

    def remove_repo(self, repo_name: str) -> dict:
        """Remove a repository and all associated edges from the graph.

        Parameters
        ----------
        repo_name :
            Repository to remove.

        Returns
        -------
        dict
            Confirmation with ``"repo"``, ``"removed"``, and
            ``"edges_removed"`` counts.
        """
        if repo_name not in self._metadata:
            return {"repo": repo_name, "removed": False, "edges_removed": 0}

        edges_removed = 0

        # Remove outgoing edges
        edges_removed += len(self._dependencies.get(repo_name, {}))
        del self._dependencies[repo_name]

        # Remove incoming edges
        incoming = self._dependents.pop(repo_name, {})
        edges_removed += len(incoming)
        for dependent in incoming:
            self._dependencies[dependent].pop(repo_name, None)

        # Remove metadata
        del self._metadata[repo_name]
        self._health_cache.pop(repo_name, None)
        self._health_dirty = True

        return {"repo": repo_name, "removed": True, "edges_removed": edges_removed}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the full graph state to a JSON-compatible dict.

        Returns
        -------
        dict
            ``repos``, ``dependencies``, and ``dependents`` mappings.
        """
        repos = {
            name: self._serialize_metadata(meta)
            for name, meta in self._metadata.items()
        }
        deps = {
            src: {dst: edge.strength for dst, edge in edges.items()}
            for src, edges in self._dependencies.items()
            if edges
        }
        dependents = {
            src: {dst: edge.strength for dst, edge in edges.items()}
            for src, edges in self._dependents.items()
            if edges
        }
        return {"repos": repos, "dependencies": deps, "dependents": dependents}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_all_health_scores(self) -> Dict[str, float]:
        """Compute health scores for all repos iteratively (no recursion).

        Uses topological order so dependency scores are always available
        before dependents.  For repos involved in cycles, uses iterative
        fixed-point convergence (max 50 rounds).
        """
        if not self._health_dirty and len(self._health_cache) == len(self._metadata):
            return dict(self._health_cache)

        scores: Dict[str, float] = {}
        max_test_count = max(
            (m.test_count for m in self._metadata.values()), default=1
        )
        max_test_count = max(max_test_count, 1)

        # Pre-compute per-repo test and freshness factors
        test_factors: Dict[str, float] = {}
        freshness_factors: Dict[str, float] = {}
        for repo, meta in self._metadata.items():
            has_f = 0.3 if meta.has_tests else 0.0
            ratio_f = min(1.0, meta.test_count / max_test_count) * 0.7
            test_factors[repo] = min(1.0, has_f + ratio_f)
            days = self._days_since_last_commit(meta.last_commit_time)
            freshness_factors[repo] = max(0.0, 1.0 - days / _STALENESS_THRESHOLD_DAYS)

        # Process repos in topological order (dependencies first)
        topo = self.topological_sort()

        # Split into DAG-ordered and cycle members
        # Cycle members are those appended after the topo portion
        # (they have in-degree > 0 remaining after Kahn's).
        # We detect them via cycle detection for correctness.
        cycle_repos: Set[str] = set()
        for cycle in self.detect_cycles():
            cycle_repos.update(cycle)

        # First pass: compute scores for non-cycle repos in topo order
        for repo in topo:
            if repo not in cycle_repos:
                scores[repo] = self._compute_single_score(
                    repo, scores, test_factors, freshness_factors,
                )

        # Second pass: iterative fixed-point for cycle repos
        # Initialize cycle repos with their non-dep-health score
        for repo in cycle_repos:
            if repo not in scores:
                # Start with no-dep-health baseline
                scores[repo] = (
                    _HEALTH_WEIGHT_TEST * test_factors.get(repo, 0.0)
                    + _HEALTH_WEIGHT_FRESHNESS * freshness_factors.get(repo, 0.0)
                    + _HEALTH_WEIGHT_DEP_HEALTH * _NO_DEP_HEALTH_FLOOR
                )

        # Iterate until convergence (max 50 rounds)
        for _ in range(50):
            changed = False
            for repo in cycle_repos:
                new_score = self._compute_single_score(
                    repo, scores, test_factors, freshness_factors,
                )
                if abs(new_score - scores[repo]) > 1e-6:
                    scores[repo] = new_score
                    changed = True
            if not changed:
                break

        # Clamp all scores
        for repo in scores:
            scores[repo] = max(0.0, min(1.0, scores[repo]))

        self._health_cache = scores
        self._health_dirty = False
        return scores

    def _compute_single_score(
        self,
        repo: str,
        scores: Dict[str, float],
        test_factors: Dict[str, float],
        freshness_factors: Dict[str, float],
    ) -> float:
        """Compute a single repo's health score using pre-computed factors.

        Uses *scores* for dependency health (may be incomplete for cycle
        members during fixed-point iteration).
        """
        deps = self._dependencies.get(repo, {})
        if deps:
            dep_scores = [
                scores[d]
                for d in deps
                if d in scores
            ]
            dep_health = (
                sum(dep_scores) / len(dep_scores)
                if dep_scores
                else _NO_DEP_HEALTH_FLOOR
            )
        else:
            dep_health = _NO_DEP_HEALTH_FLOOR

        return (
            _HEALTH_WEIGHT_TEST * test_factors.get(repo, 0.0)
            + _HEALTH_WEIGHT_FRESHNESS * freshness_factors.get(repo, 0.0)
            + _HEALTH_WEIGHT_DEP_HEALTH * dep_health
        )

    def _get_health_score(self, repo_name: str) -> float:
        """Return the cached health score for *repo_name*.

        Ensures all scores are computed (iteratively) before returning.
        This replaces the previous recursive implementation to handle
        graphs with 1000+ repos without hitting Python's recursion limit.
        """
        scores = self._compute_all_health_scores()
        return scores.get(repo_name, 0.0)

    @staticmethod
    def _days_since_last_commit(last_commit_time: Optional[str]) -> int:
        """Compute days elapsed since *last_commit_time* (ISO-8601 UTC).

        Returns a default staleness when *last_commit_time* is ``None``
        or unparseable.
        """
        if last_commit_time is None:
            return _DEFAULT_LAST_COMMIT_DAYS_AGO
        try:
            dt = datetime.fromisoformat(last_commit_time.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - dt
            return max(0, int(delta.total_seconds() / 86400))
        except (ValueError, OverflowError, OSError):
            return _DEFAULT_LAST_COMMIT_DAYS_AGO

    @staticmethod
    def _serialize_metadata(meta: RepoMetadata) -> Dict[str, Any]:
        """Convert a :class:`RepoMetadata` to a plain dict."""
        return {
            "language": meta.language,
            "test_count": meta.test_count,
            "last_commit_time": meta.last_commit_time,
            "has_tests": meta.has_tests,
            "extra": dict(meta.extra),
        }

    def __repr__(self) -> str:
        return (
            f"RepoCartographer(repos={len(self._metadata)}, "
            f"edges={self.get_edge_count()})"
        )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def create_cartographer(
    repos: Optional[Dict[str, Dict[str, Any]]] = None,
) -> RepoCartographer:
    """Create a :class:`RepoCartographer` pre-seeded with *repos*.

    Parameters
    ----------
    repos :
        Optional ``{repo_name: metadata_dict}`` mapping.

    Returns
    -------
    RepoCartographer
        Ready-to-use cartographer instance.
    """
    return RepoCartographer(repos=repos)
