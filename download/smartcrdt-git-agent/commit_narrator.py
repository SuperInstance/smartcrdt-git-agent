"""CRDT-aware commit message generator for the SmartCRDT monorepo (81 packages).

Analyses unified diffs and produces conventional-commit messages carrying
semantic meaning about CRDT data-types, merge-safety, and cross-package risks.

Zero external dependencies — Python 3.9+ stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FILE_PATTERNS: Dict[str, List[str]] = {
    "counter": ["counter", "increment", "decrement", "bounded", "pn_counter", "g-counter"],
    "set": ["or-set", "orsset", "observed-remove", "crdt-set", "add-wins", "remove-wins"],
    "register": ["lww", "register", "last-writer", "multi-value", "mv-register"],
    "vector-clock": ["vector-clock", "hlc", "happened-before", "causal", "lamport"],
    "gossip": ["gossip", "anti-entropy", "dissemination", "fanout", "plumtree", "hyparview"],
    "map": ["crdt-map", "composite", "nested-crdt", "ormap"],
    "sequence": ["sequence", "rga", "treedoc", "logoot", "yjs", "character-list"],
}

_DIFF_KEYWORDS: Dict[str, List[str]] = {
    "counter": [r"\bincrement\b", r"\bdecrement\b", r"\bbounded\b", r"\bcounter\b"],
    "set": [r"\badd\b", r"\bremove\b", r"\bobserve\b", r"\bOR-Set\b"],
    "register": [r"\blast-writer-wins\b", r"\bregister\b", r"\bassign\b"],
    "vector-clock": [r"\bhlc\b", r"\bhappened.before\b", r"\bcausal\b", r"\bvector\s*clock\b"],
    "gossip": [r"\banti.entropy\b", r"\bgossip\b", r"\bdissemination\b", r"\bfanout\b"],
    "map": [r"\bcomposite\b", r"\bnested\s*crdt\b", r"\bcrdt\s*map\b"],
    "sequence": [r"\bcharacter\b", r"\binsert\b", r"\bdelete\b", r"\bposition\b", r"\border\b"],
}

_COMPILED_KW: Dict[str, List[re.Pattern]] = {
    ct: [re.compile(p, re.I) for p in pats] for ct, pats in _DIFF_KEYWORDS.items()
}

CORE_PACKAGES: Tuple[str, ...] = (
    "crdt-core", "crdt-types", "state-machine", "merge-policy",
    "clock", "gossip-protocol", "transport", "replication",
)

_ALL_PACKAGES: Tuple[str, ...] = (
    "crdt-core", "crdt-types", "state-machine", "merge-policy",
    "clock", "gossip-protocol", "transport", "replication",
    "counter", "g-counter", "pn-counter", "bounded-counter",
    "counter-factory", "counter-serializer", "counter-benchmarks", "counter-tests",
    "or-set", "add-wins-set", "remove-wins-set", "observed-remove-set",
    "set-serializer", "set-factory", "set-benchmarks", "set-tests",
    "lww-register", "mv-register", "register-factory",
    "register-serializer", "register-benchmarks", "register-tests",
    "vector-clock", "hlc", "lamport-clock", "causal-context",
    "clock-serializer", "clock-benchmarks", "clock-tests",
    "gossip", "plumtree", "hyparview", "swim", "anti-entropy",
    "gossip-factory", "gossip-benchmarks", "gossip-tests",
    "crdt-map", "ormap", "ormap-serializer", "map-factory",
    "map-benchmarks", "map-tests",
    "sequence", "rga", "treedoc", "logoot", "yjs-adapter",
    "sequence-factory", "sequence-serializer", "sequence-benchmarks", "sequence-tests",
    "api-gateway", "cli", "config", "logging", "metrics",
    "monitoring", "tracing", "security", "auth",
    "storage-engine", "snapshot-store", "event-store",
    "webhook", "sdk-js", "sdk-python", "sdk-go",
    "docs-generator", "examples", "playground",
    "integration-tests", "e2e-tests", "compat-tests",
    "benchmark-suite", "load-tests", "chaos-tests",
    "release", "ci", "docker", "helm", "dev-tools",
    "codeowners", "contribution-guide",
)

COMMIT_TYPES: Tuple[str, ...] = ("feat", "fix", "test", "docs", "chore", "refactor", "perf")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileDiff:
    """Structured view of a single file's unified diff."""

    path: str
    is_new: bool
    is_deleted: bool
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    hunks: int = 0

    @property
    def insertions(self) -> int:
        return len(self.added_lines)

    @property
    def deletions(self) -> int:
        return len(self.removed_lines)


@dataclass(frozen=True)
class DiffAnalysis:
    """Complete analysis result for a unified diff."""

    files: List[FileDiff] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    total_insertions: int = 0
    total_deletions: int = 0
    crdt_types: List[str] = field(default_factory=list)
    affected_packages: List[str] = field(default_factory=list)
    scope: str = ""
    commit_type: str = ""
    subject: str = ""
    merge_warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CommitNarrator
# ---------------------------------------------------------------------------


class CommitNarrator:
    """Generate semantic, CRDT-aware conventional commit messages.

    Inspects file paths *and* diff content to detect CRDT data-types,
    determine affected monorepo packages, choose a conventional-commit
    type/scope, and produce a subject line conveying CRDT merge-semantics
    intent with optional safety warnings.

    Parameters
    ----------
    repo_root : str
        Repository root path. Defaults to ``"."``.
    """

    def __init__(self, repo_root: str = ".") -> None:
        self._repo_root = repo_root.rstrip("/")

    # -- Public API ---------------------------------------------------------

    def narrate(self, diff_text: str, task_id: Optional[str] = None) -> str:
        """Analyse *diff_text* and return a fully-formed commit message.

        Orchestrates parsing, CRDT detection, scope resolution, and
        formatting.  Merge-safety warnings are appended as a body when present.

        Parameters
        ----------
        diff_text : str
            Unified diff (``git diff`` output).
        task_id : str, optional
            Tracker ID such as ``"T-42"`` appended to the first line.

        Returns
        -------
        str
            Complete commit message (first line + optional body).
        """
        analysis = self._analyse(diff_text)
        first_line = self.format_commit_message(
            type_=analysis.commit_type, scope=analysis.scope,
            subject=analysis.subject, task_id=task_id,
        )
        if analysis.merge_warnings:
            block = "\n".join(f"  * {w}" for w in analysis.merge_warnings)
            body = (
                f"\n\nMerge-semantics notes:\n{block}\n\n"
                f"Affected packages: {', '.join(analysis.affected_packages)}"
            )
            return first_line + body
        return first_line

    def detect_crdt_types(self, diff_text: str) -> List[str]:
        """Detect CRDT types from file paths **and** diff content.

        Two-pass detection:

        1. **Path pass** — match changed files against :data:`FILE_PATTERNS`.
        2. **Content pass** — scan added lines for CRDT keywords via compiled
           regex patterns.

        Parameters
        ----------
        diff_text : str
            Unified diff.

        Returns
        -------
        list[str]
            Deduplicated, order-preserved CRDT type strings.
        """
        found: List[str] = []
        seen: set = set()
        files = self._extract_changed_files(diff_text)

        for fpath in files:
            lower = fpath.lower()
            for ct, pats in FILE_PATTERNS.items():
                if ct not in seen and any(p in lower for p in pats):
                    found.append(ct)
                    seen.add(ct)

        for line in diff_text.splitlines():
            if not line.startswith("+"):
                continue
            text = line[1:]
            for ct, regexes in _COMPILED_KW.items():
                if ct not in seen and any(r.search(text) for r in regexes):
                    found.append(ct)
                    seen.add(ct)
        return found

    def detect_scope(self, files: List[str]) -> str:
        """Determine the commit scope from changed file paths.

        Uses the longest common package prefix among all changed files.
        Falls back to ``"*"`` for cross-cutting changes spanning unrelated
        packages, or groups by CRDT type when all packages share one.

        Parameters
        ----------
        files : list[str]
            Repository-relative file paths.

        Returns
        -------
        str
            Scope string (empty when no package is identified).
        """
        if not files:
            return ""
        packages = list(dict.fromkeys(
            p for f in files if (p := self._resolve_package(f))
        ))
        if not packages:
            return ""
        if len(packages) == 1:
            return packages[0]

        prefix = self._common_prefix(packages)
        if prefix in _ALL_PACKAGES:
            return prefix

        crdt_groups = self._group_packages_by_crdt_type(packages)
        return next(iter(crdt_groups)) if len(crdt_groups) == 1 else "*"

    def detect_type(self, diff_text: str) -> str:
        """Determine the conventional-commit type from diff content.

        Uses file-extension inspection, added-line keyword scoring, and
        insertion/deletion ratio heuristics.

        Parameters
        ----------
        diff_text : str
            Unified diff.

        Returns
        -------
        str
            One of :data:`COMMIT_TYPES` (defaults to ``"chore"``).
        """
        lines = diff_text.splitlines()
        added: List[str] = []
        removed: List[str] = []

        for line in lines:
            if line.startswith(("--- ", "+++ ", "@@")):
                continue
            if line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])

        file_paths = self._extract_changed_files(diff_text)

        # File-extension / name heuristics.
        for fpath in file_paths:
            lo = fpath.lower()
            if lo.endswith((".md", ".rst")) or "readme" in lo or "changelog" in lo:
                return "docs"
            base = lo.split("/")[-1]
            if base.startswith("test_") or base.endswith("_test.py") or "/test" in lo:
                return "test"
            if "benchmark" in lo or "perf" in lo:
                return "perf"

        all_added = "\n".join(added).lower()
        all_content = "\n".join(added + removed).lower()
        score: Dict[str, int] = {t: 0 for t in COMMIT_TYPES}

        kw_map: Dict[str, Tuple[str, ...]] = {
            "test": ("test", "assert", "expect", "mock", "stub", "fixture"),
            "fix": ("fix", "bug", "patch", "resolve", "workaround", "hotfix"),
            "perf": ("perf", "optimi", "faster", "slow", "latency", "throughput"),
            "docs": ("docstring", "javadoc", "comment", "readme", "example"),
            "refactor": ("refactor", "rename", "restructure", "simplify", "extract"),
            "chore": ("chore", "cleanup", "format", "lint", "ci", "dependabot"),
        }
        for ct, kws in kw_map.items():
            if any(k in all_added for k in kws):
                score[ct] += 5
            if ct == "fix" and any(k in all_content for k in kws):
                score[ct] += 3

        if len(added) > len(removed) * 2 and len(added) > 3:
            score["feat"] += 3

        best = max(score, key=lambda k: score[k])
        return best if score[best] > 0 else "chore"

    def generate_subject(
        self, crdt_types: List[str], scope: str, diff_text: str,
    ) -> str:
        """Generate a CRDT-merge-aware subject line.

        Combines a CRDT type qualifier (only when the scope does not already
        convey it) with an imperative-mood summary extracted from the diff.
        Capped at 72 characters.

        Parameters
        ----------
        crdt_types : list[str]
            Detected CRDT types.
        scope : str
            Commit scope.
        diff_text : str
            Original unified diff.

        Returns
        -------
        str
            Subject line (imperative mood, ≤ 72 chars).
        """
        phrase = self._summarise_diff(diff_text)
        qualifier = ""
        if crdt_types and scope and scope != "*":
            lo = scope.lower().replace("-", "")
            if not any(ct.replace("-", "") in lo for ct in crdt_types):
                qualifier = f"{crdt_types[0]} "
        subject = f"{qualifier}{phrase}"
        return subject[:69] + "..." if len(subject) > 72 else subject

    def parse_diff(self, diff_text: str) -> Dict:
        """Parse a unified diff into structured data.

        Handles ``--- a/file`` / ``+++ b/file`` headers, ``/dev/null``
        entries (new / deleted files), and ``@@ -old,+new @@`` hunk markers.

        Parameters
        ----------
        diff_text : str
            Raw unified diff text.

        Returns
        -------
        dict
            Keys: ``files`` (list[FileDiff]), ``total_insertions``,
            ``total_deletions``, ``changed_files``.
        """
        file_diffs: List[FileDiff] = []
        current: Optional[dict] = None

        for line in diff_text.splitlines():
            if line.startswith("--- "):
                if current is not None:
                    file_diffs.append(self._build_file_diff(current))
                current = {
                    "old_path": self._parse_diff_path(line[4:].strip()),
                    "new_path": "", "added": [], "removed": [], "hunks": 0,
                }
                continue
            if current is not None and line.startswith("+++ "):
                current["new_path"] = self._parse_diff_path(line[4:].strip())
                continue
            if current is None:
                continue
            if line.startswith("@@"):
                current["hunks"] += 1
            elif line.startswith("+"):
                current["added"].append(line[1:])
            elif line.startswith("-"):
                current["removed"].append(line[1:])

        if current is not None:
            file_diffs.append(self._build_file_diff(current))

        return {
            "files": file_diffs,
            "total_insertions": sum(f.insertions for f in file_diffs),
            "total_deletions": sum(f.deletions for f in file_diffs),
            "changed_files": [f.path for f in file_diffs],
        }

    def _extract_changed_files(self, diff_text: str) -> List[str]:
        """Extract file paths from a unified diff.

        Prefers the ``b/`` path for additions/modifications; falls back to
        ``a/`` for deletions.  ``/dev/null`` is handled correctly.

        Parameters
        ----------
        diff_text : str
            Raw unified diff.

        Returns
        -------
        list[str]
            File paths relative to the repository root.
        """
        files: List[str] = []
        lines = diff_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("--- "):
                old_path = self._parse_diff_path(line[4:].strip())
                new_path = ""
                if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                    new_path = self._parse_diff_path(lines[i + 1][4:].strip())
                    i += 1
                if new_path and new_path != "/dev/null":
                    files.append(new_path)
                elif old_path and old_path != "/dev/null":
                    files.append(old_path)
            i += 1
        return files

    def assess_merge_implications(
        self, crdt_types: List[str], diff_text: str,
    ) -> List[str]:
        """Assess CRDT merge-safety implications of a diff.

        Generates warnings for: core-package modifications, multi-type
        changes, merge-semantics alterations, public API surface changes,
        and type-specific concerns (bounded counters, LWW drift, tombstones).

        Parameters
        ----------
        crdt_types : list[str]
            Detected CRDT types.
        diff_text : str
            Unified diff.

        Returns
        -------
        list[str]
            Human-readable warning strings.
        """
        warnings: List[str] = []
        files = self._extract_changed_files(diff_text)
        affected = [p for f in files if (p := self._resolve_package(f))]

        core_hit = [p for p in affected if p in CORE_PACKAGES]
        if core_hit:
            warnings.append(
                f"Core package(s) modified: {', '.join(core_hit)}. "
                "Downstream consumers may require updates."
            )

        if len(crdt_types) > 1:
            warnings.append(
                f"Multiple CRDT types affected ({', '.join(crdt_types)}). "
                "Verify cross-type invariants are preserved."
            )

        lowered = diff_text.lower()
        sem_kw = (
            "mergepolicy", "merge_policy", "resolveconflict",
            "resolve_conflict", "concurrent", "mergefunction",
            "merge_function", "tombstone",
        )
        if any(k in lowered for k in sem_kw):
            warnings.append(
                "Diff touches merge-resolution logic. "
                "Ensure concurrent-merge invariants hold under all interleavings."
            )

        has_code = any(f.lower().endswith((".ts", ".rs", ".go")) for f in files)
        if has_code and any(s in diff_text for s in ("export ", "pub ", "pub fn")):
            warnings.append(
                "Public API surface appears changed. "
                "Check semver impact on dependent packages."
            )

        if "counter" in crdt_types and "bounded" in lowered:
            warnings.append(
                "Bounded counter semantics detected. "
                "Verify overflow/underflow handling across replicas."
            )
        if "register" in crdt_types and ("last-writer" in lowered or "lww" in lowered):
            warnings.append(
                "LWW register change detected. "
                "Clock drift between replicas may cause non-deterministic outcomes."
            )
        if "set" in crdt_types and ("observe" in lowered or "tombstone" in lowered):
            warnings.append(
                "Observed-remove set change detected. "
                "Tombstone accumulation may affect long-running replicas."
            )
        if "vector-clock" in crdt_types:
            warnings.append(
                "Vector-clock component modified. "
                "Ensure causal ordering guarantees are not relaxed."
            )

        return warnings

    def format_commit_message(
        self, type_: str, scope: str, subject: str, task_id: Optional[str] = None,
    ) -> str:
        """Format a conventional commit message first line.

        Format: ``type(scope): subject [TASK-ID]``

        Parameters
        ----------
        type_ : str
            Conventional commit type.
        scope : str
            Scope string; parentheses omitted when empty.
        subject : str
            Imperative-mood subject line.
        task_id : str, optional
            Tracker ID appended in brackets.

        Returns
        -------
        str
            Formatted commit message first line.
        """
        first_line = f"{type_}({scope}): {subject}" if scope else f"{type_}: {subject}"
        if task_id:
            first_line += f" [{task_id}]"
        return first_line

    # -- Private helpers ----------------------------------------------------

    def _analyse(self, diff_text: str) -> DiffAnalysis:
        """Run the full analysis pipeline."""
        parsed = self.parse_diff(diff_text)
        changed = parsed["changed_files"]
        crdt_types = self.detect_crdt_types(diff_text)
        scope = self.detect_scope(changed)
        commit_type = self.detect_type(diff_text)
        subject = self.generate_subject(crdt_types, scope, diff_text)
        affected = list(dict.fromkeys(
            p for f in changed if (p := self._resolve_package(f))
        ))
        merge_warnings = self.assess_merge_implications(crdt_types, diff_text)
        return DiffAnalysis(
            files=parsed["files"], changed_files=changed,
            total_insertions=parsed["total_insertions"],
            total_deletions=parsed["total_deletions"],
            crdt_types=crdt_types, affected_packages=affected,
            scope=scope, commit_type=commit_type, subject=subject,
            merge_warnings=merge_warnings,
        )

    @staticmethod
    def _parse_diff_path(raw: str) -> str:
        """Strip ``a/``/``b/`` prefixes and trailing tab-timestamp."""
        for pfx in ("a/", "b/"):
            if raw.startswith(pfx):
                raw = raw[len(pfx):]
        return raw[:raw.index("\t")] if "\t" in raw else raw

    def _resolve_package(self, file_path: str) -> Optional[str]:
        """Map a file path to its monorepo package name."""
        rel = file_path
        if rel.startswith(self._repo_root + "/"):
            rel = rel[len(self._repo_root) + 1:]
        for d in ("packages/", "src/", "lib/"):
            if rel.startswith(d):
                rel = rel[len(d):]
        segments = rel.split("/")
        if not segments or segments[0] == "/dev/null":
            return None
        candidate = segments[0]
        if candidate in _ALL_PACKAGES:
            return candidate
        for pkg in _ALL_PACKAGES:
            if candidate.startswith(pkg + "-") or candidate.startswith(pkg + "_"):
                return pkg
        return candidate or None

    @staticmethod
    def _common_prefix(strings: List[str]) -> str:
        """Return the longest common prefix, trimmed to separator boundary."""
        if not strings:
            return ""
        prefix = strings[0]
        for s in strings[1:]:
            while not s.startswith(prefix):
                prefix = prefix[:-1]
                if not prefix:
                    return ""
        for sep in ("-", "."):
            if sep in prefix:
                prefix = prefix[:prefix.rindex(sep)]
                break
        return prefix

    @staticmethod
    def _group_packages_by_crdt_type(packages: List[str]) -> Dict[str, List[str]]:
        """Group package names by associated CRDT type."""
        groups: Dict[str, List[str]] = {}
        for pkg in packages:
            matched = False
            for ct, pats in FILE_PATTERNS.items():
                if any(p in pkg.lower() for p in pats):
                    groups.setdefault(ct, []).append(pkg)
                    matched = True
                    break
            if not matched:
                groups.setdefault("*", []).append(pkg)
        return groups

    @staticmethod
    def _build_file_diff(data: dict) -> FileDiff:
        """Build a :class:`FileDiff` from raw parsed data."""
        old_p, new_p = data["old_path"], data["new_path"]
        is_new = old_p == "/dev/null"
        is_deleted = new_p == "/dev/null"
        path = new_p if new_p and new_p != "/dev/null" else old_p
        return FileDiff(
            path=path, is_new=is_new, is_deleted=is_deleted,
            added_lines=data["added"], removed_lines=data["removed"],
            hunks=data["hunks"],
        )

    @staticmethod
    def _is_noise_line(ln: str) -> bool:
        """Return True if *ln* is a code-only or metadata line to skip.

        Filters out struct/function declarations, field assignments, import
        statements, and bare punctuation — but preserves content-bearing
        comments for the summariser to use.
        """
        s = ln.strip()
        if not s or s in ("{", "}", "(", ")", ";", "[", "]", "//", "#", "*/", "/*"):
            return True
        if s.startswith(("import ", "from ", "use ", "mod ", "extern ")):
            return True
        code_indicators = (
            "pub struct", "pub enum", "pub trait", "pub fn", "pub const",
            "pub type", "pub use", "impl ", "let mut", "let ", "fn ",
            "struct ", "enum ", "type ",
            "self.", "self,", "= ", "-> ",
            "::new", "::from", "::default",
            "+=", "-=", ".clone()", ".unwrap()",
            ".drain(", ".append(", ".push(", ".send(",
            ".add(", ".remove(", ".insert(", ".delete(",
        )
        if any(ci in s for ci in code_indicators):
            return True
        if s.endswith(("{", ",", ");", ")]", ")\n")):
            return True
        if re.match(r'^[a-z_]+\s*:\s*[A-Z]\w+(<.*>)?$', s):
            return True
        return False

    @staticmethod
    def _summarise_diff(diff_text: str) -> str:
        """Extract a short imperative-mood summary from added lines.

        Priority: verb-led prose → verb-led comments → substantial comments
        → any prose → default ``"update CRDT implementation"``.
        """
        is_noise = CommitNarrator._is_noise_line
        added: List[str] = []
        for ln in diff_text.splitlines():
            if not ln.startswith("+"):
                continue
            if ln.startswith("++") or ln.startswith("+diff --git"):
                continue
            stripped = ln[1:].strip()
            if stripped and not is_noise(stripped):
                added.append(stripped)

        comment_lines: List[str] = []
        prose_lines: List[str] = []
        for ln in added:
            sc = CommitNarrator._strip_comment(ln)
            if sc:
                comment_lines.append(sc)
            else:
                prose_lines.append(ln)

        verbs = (
            "add ", "implement ", "fix ", "update ", "refactor ",
            "remove ", "optimize ", "improve ", "introduce ",
            "support ", "handle ", "migrate ", "rename ",
            "deprecate ", "extract ", "simplify ", "extend ",
            "ensure ", "validate ", "guard ", "enforce ",
        )
        for ln in prose_lines:
            if any(ln.lower().startswith(v) for v in verbs):
                return CommitNarrator._truncate(ln)
        for ln in comment_lines:
            if any(ln.lower().startswith(v) for v in verbs):
                return CommitNarrator._truncate(ln)
        for ln in comment_lines:
            if len(ln) >= 10:
                return CommitNarrator._truncate(ln)
        if prose_lines:
            return CommitNarrator._truncate(prose_lines[0])
        if comment_lines:
            return CommitNarrator._truncate(comment_lines[0])
        return "update CRDT implementation"

    @staticmethod
    def _strip_comment(ln: str) -> Optional[str]:
        """Extract prose content from a comment line, or return ``None``."""
        for prefix in ("/// ", "// ", "# ", "* "):
            if ln.startswith(prefix):
                return ln[len(prefix):].strip()
        if ln.startswith('"""') or ln.startswith("'''"):
            return ln[3:].strip()
        return None

    @staticmethod
    def _truncate(s: str, limit: int = 60) -> str:
        """Truncate *s* at *limit* characters."""
        s = s.rstrip(".").split("\n")[0].strip()
        return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def narrate_commit(
    diff_text: str, task_id: Optional[str] = None, repo_root: str = ".",
) -> str:
    """One-shot helper: create a :class:`CommitNarrator` and narrate."""
    return CommitNarrator(repo_root=repo_root).narrate(diff_text, task_id=task_id)
