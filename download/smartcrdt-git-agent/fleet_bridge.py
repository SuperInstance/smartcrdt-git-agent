"""
fleet_bridge.py — Message-in-a-Bottle fleet coordination module.

Enables autonomous git agents to coordinate via markdown "bottles" with YAML
front-matter exchanged through shared filesystem directories. No network
services or external dependencies required.

Directory layout (relative to repo root)::

    message-in-a-bottle/
        for-fleet/          — outgoing fleet-wide messages
        from-fleet/         — incoming fleet context from other agents
        for-any-vessel/     — open broadcasts to any vessel
    for-fleet/              — SmartCRDT's own fleet context (CONTEXT.md, PRIORITY.md)
    for-oracle1/            — direct channel to lighthouse keeper

Usage::

    bridge = FleetBridge("/path/to/repo")
    bridge.deposit("agent-name", "Body text", "directive", "Deploy v2")
    bottles = bridge.scan()
    health = bridge.generate_health_response(session=1)

Python 3.9+ stdlib only.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AGENT_NAME = "smartcrdt-git-agent"
_MIB_DIR = "message-in-a-bottle"
_FOR_FLEET_DIR = "for-fleet"
_FROM_FLEET_DIR = "from-fleet"
_FOR_ANY_VESSEL_DIR = "for-any-vessel"
_FOR_ORACLE1_DIR = "for-oracle1"
_CONTEXT_FILE = "CONTEXT.md"
_PRIORITY_FILE = "PRIORITY.md"
_STATE_FILE = ".fleet_bridge_state.json"
_BOTTLE_TYPES = ("report", "directive", "response", "insight")
_FM_BOUNDARY = "---"

_RE_FM_BLOCK = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_RE_FM_ENTRY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _safe_makedirs(path: Path) -> None:
    """Create *path* and parents if they don't exist."""
    path.mkdir(parents=True, exist_ok=True)


def _parse_front_matter(text: str) -> tuple[Dict[str, str], str]:
    """Parse YAML-like front matter, returning (metadata, body)."""
    match = _RE_FM_BLOCK.match(text)
    if not match:
        return {}, text
    meta: Dict[str, str] = {m.group(1).strip(): m.group(2).strip()
                            for m in _RE_FM_ENTRY.finditer(match.group(1))}
    return meta, text[match.end():]


def _build_front_matter(fields: Dict[str, str]) -> str:
    """Serialize *fields* into a YAML front-matter block.

    Uses dict (not kwargs) so keys may contain hyphens (e.g. ``Bottle-To``).
    """
    lines = [_FM_BOUNDARY] + [f"{k}: {v}" for k, v in fields.items()]
    return "\n".join(lines)


def _bottle_filename(bottle_type: str, subject: str) -> str:
    """Derive a filesystem-safe bottle filename."""
    ts = _now_iso().replace(":", "-").replace("+", "Z").split(".")[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", subject).strip("-").lower()
    return f"{ts}_{bottle_type}_{slug}.md"


# ---------------------------------------------------------------------------
# FleetBridge
# ---------------------------------------------------------------------------

class FleetBridge:
    """Coordinate with the SmartCRDT agent fleet via message-in-a-bottle files.

    Parameters
    ----------
    repo_root :
        Path to the repository root containing ``message-in-a-bottle/``.
        Defaults to the current working directory.
    """

    def __init__(self, repo_root: str | None = None) -> None:
        """Initialize with repo root and ensure state is loaded."""
        self._root = Path(repo_root or os.getcwd()).resolve()
        self._mib = self._root / _MIB_DIR
        self._outgoing = self._mib / _FOR_FLEET_DIR
        self._incoming = self._mib / _FROM_FLEET_DIR
        self._broadcast = self._mib / _FOR_ANY_VESSEL_DIR
        self._own_context = self._root / _FOR_FLEET_DIR
        self._oracle_dir = self._root / _FOR_ORACLE1_DIR
        self._state_path = self._root / _STATE_FILE
        self._state: Dict[str, Any] = self._load_state()

    # -- state persistence --------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        """Load bridge state from JSON sidecar; empty dict if missing."""
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"read_bottles": [], "claimed_tasks": {}}

    def _save_state(self) -> None:
        """Atomically persist state to JSON sidecar."""
        _safe_makedirs(self._state_path.parent)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self._state_path)

    # -- directory helpers --------------------------------------------------

    def _ensure_directories(self) -> None:
        """Create the full message-in-a-bottle directory tree if absent."""
        for d in (self._mib, self._outgoing, self._incoming,
                  self._broadcast, self._own_context, self._oracle_dir):
            _safe_makedirs(d)

    def _resolve_source_dir(self, source_dir: str | None) -> Path:
        """Resolve scan source directory, defaulting to from-fleet/."""
        return Path(source_dir).resolve() if source_dir else self._incoming

    # -- bottle operations --------------------------------------------------

    def deposit(
        self,
        recipient: str,
        body: str,
        bottle_type: str,
        subject: str,
        session: int = 0,
    ) -> str:
        """Create and write a bottle to the outgoing directory.

        Parameters
        ----------
        recipient :
            Target agent name, ``"fleet"``, or ``"any-vessel"`` for broadcasts.
        body :
            Markdown body of the message.
        bottle_type :
            One of ``"report"``, ``"directive"``, ``"response"``, ``"insight"``.
        subject :
            Short human-readable summary used in filename and header.
        session :
            Optional session number for correlation.

        Returns
        -------
        str
            Absolute path to the newly created bottle file.
        """
        if bottle_type not in _BOTTLE_TYPES:
            raise ValueError(f"Unknown bottle type {bottle_type!r}; "
                             f"expected one of {_BOTTLE_TYPES}")
        self._ensure_directories()

        # Route to correct directory by recipient.
        rlc = recipient.lower()
        if rlc in ("any-vessel", "any"):
            target_dir = self._broadcast
        elif rlc.startswith("oracle"):
            target_dir = self._oracle_dir
        else:
            target_dir = self._outgoing

        front = _build_front_matter({
            "Bottle-To": recipient, "Bottle-From": _AGENT_NAME,
            "Bottle-Type": bottle_type, "Session": str(session),
            "Timestamp": _now_iso(), "Subject": subject,
        })
        content = f"{front}\n---\n\n{body.lstrip()}\n"
        filepath = target_dir / _bottle_filename(bottle_type, subject)

        # Handle filename collision with counter suffix.
        if filepath.exists():
            stem = filepath.stem
            counter = 1
            while filepath.exists():
                filepath = target_dir / f"{stem}_{counter}.md"
                counter += 1

        filepath.write_text(content, encoding="utf-8")
        return str(filepath)

    def scan(self, source_dir: str | None = None) -> List[Dict[str, Any]]:
        """Scan a directory for unread bottle files.

        Parameters
        ----------
        source_dir :
            Directory to scan.  Defaults to ``message-in-a-bottle/from-fleet/``.

        Returns
        -------
        list[dict]
            Each dict has parsed bottle metadata plus ``"filepath"`` and
            ``"unread"``.  Unread bottles are listed first.
        """
        src = self._resolve_source_dir(source_dir)
        if not src.is_dir():
            return []

        bottles: List[Dict[str, Any]] = []
        for fp in sorted(src.iterdir()):
            if fp.suffix.lower() != ".md" or fp.name.startswith("."):
                continue
            meta, body = self._parse_file(fp)
            meta["filepath"] = str(fp)
            meta["body_preview"] = body.strip()[:200]
            meta["unread"] = str(fp) not in self._state["read_bottles"]
            bottles.append(meta)

        bottles.sort(key=lambda b: (not b["unread"],
                                    os.path.getmtime(b["filepath"])))
        return bottles

    def read_bottle(self, filepath: str) -> Dict[str, Any]:
        """Read and fully parse a bottle file.

        Parameters
        ----------
        filepath :
            Path to the bottle markdown file.

        Returns
        -------
        dict
            ``{"metadata": {...}, "body": str, "filepath": str}``
        """
        fp = Path(filepath).resolve()
        if not fp.is_file():
            raise FileNotFoundError(f"Bottle not found: {fp}")
        meta, body = self._parse_file(fp)
        return {"metadata": meta, "body": body.strip(), "filepath": str(fp)}

    def mark_read(self, filepath: str) -> None:
        """Mark a bottle as read so future scans skip it.

        Parameters
        ----------
        filepath :
            Absolute path to the consumed bottle.
        """
        abs_path = str(Path(filepath).resolve())
        if abs_path not in self._state["read_bottles"]:
            self._state["read_bottles"].append(abs_path)
        self._save_state()

    def respond(self, original_path: str, body: str) -> str:
        """Create a response bottle replying to an existing message.

        Copies ``Session`` and ``Bottle-To`` from the original and prefixes
        the subject with ``"Re: "``.

        Parameters
        ----------
        original_path :
            Path to the bottle being responded to.
        body :
            Markdown body of the response.

        Returns
        -------
        str
            Absolute path of the new response bottle.
        """
        original = self.read_bottle(original_path)
        meta = original["metadata"]
        sender = meta.get("Bottle-From", "unknown")
        session = meta.get("Session", "0")
        subject = meta.get("Subject", "No Subject")
        resp_subject = subject if subject.startswith("Re: ") else f"Re: {subject}"
        return self.deposit(sender, body, "response", resp_subject,
                            session=int(session))

    # -- health check -------------------------------------------------------

    def generate_health_response(
        self,
        session: int = 0,
        tasks_in_progress: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce a JSON-serialisable health-check payload.

        Parameters
        ----------
        session :
            Current coordination session number.
        tasks_in_progress :
            Task IDs the agent is actively working on.
        blockers :
            Blocker descriptions, if any.

        Returns
        -------
        dict
            Health status dictionary ready for ``json.dumps``.
        """
        claimed = list(self._state.get("claimed_tasks", {}).keys())
        incoming = self.scan()
        unread_count = sum(1 for b in incoming if b.get("unread"))
        return {
            "agent": _AGENT_NAME,
            "status": "active",
            "session": session,
            "timestamp": _now_iso(),
            "tasks_in_progress": tasks_in_progress or claimed,
            "blockers": blockers or [],
            "unread_bottles": unread_count,
            "repo_root": str(self._root),
            "directories_ok": self._check_directories(),
        }

    # -- task claiming ------------------------------------------------------

    def claim_task(self, task_id: str, branch: str | None = None) -> bool:
        """Claim a task from TASKS.md by recording it in CLAIMED.md.

        Parameters
        ----------
        task_id :
            Identifier of the task to claim (e.g. ``"T-001"``).
        branch :
            Optional git branch name for the claim.

        Returns
        -------
        bool
            ``True`` if successfully claimed, ``False`` if not found or
            already claimed.
        """
        if task_id in self._state.get("claimed_tasks", {}):
            return False

        tasks = self.read_tasks()
        if not any(t.get("id") == task_id or task_id in t.get("raw", "")
                   for t in tasks):
            return False

        _safe_makedirs(self._own_context)
        claimed_path = self._own_context / "CLAIMED.md"
        timestamp = _now_iso()
        entry = f"- {task_id} | {_AGENT_NAME} | {branch or 'unassigned'} | {timestamp}\n"

        mode = "a" if claimed_path.is_file() else "w"
        with claimed_path.open(mode, encoding="utf-8") as fh:
            if mode == "w":
                fh.write("# CLAIMED TASKS\n\n")
            fh.write(entry)

        self._state.setdefault("claimed_tasks", {})[task_id] = {
            "branch": branch, "claimed_at": timestamp,
        }
        self._save_state()
        return True

    def read_tasks(self) -> List[Dict[str, Any]]:
        """Parse TASKS.md from the fleet context directory.

        Supports numbered list items with optional task IDs in brackets and
        pipe-separated claim info::

            1. [T-001] Implement auth module | agent-name | branch-name

        Returns
        -------
        list[dict]
            Parsed tasks with ``"number"``, ``"id"``, ``"description"``,
            ``"claimed_by"``, ``"branch"``, ``"raw"`` keys.
        """
        tasks_path = self._own_context / "TASKS.md"
        if not tasks_path.is_file():
            return []

        text = tasks_path.read_text(encoding="utf-8")
        tasks: List[Dict[str, Any]] = []
        in_list = False

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                in_list = False
                continue

            m_num = re.match(r"^(\d+)\.\s+(.*)", stripped)
            m_bul = re.match(r"^[-*]\s+(.*)", stripped) if not m_num else None
            match = m_num or m_bul

            if match:
                in_list = True
                number = int(match.group(1)) if m_num else len(tasks) + 1
                rest = match.group(2)

                id_m = re.search(r"\[([^\]]+)\]", rest)
                task_id = id_m.group(1) if id_m else None
                description = re.sub(r"\[([^\]]+)\]\s*", "", rest)

                parts = description.split("|", 2)
                desc_clean = parts[0].strip()
                tasks.append({
                    "number": number,
                    "id": task_id,
                    "description": desc_clean,
                    "claimed_by": parts[1].strip() if len(parts) > 1 else None,
                    "branch": parts[2].strip() if len(parts) > 2 else None,
                    "raw": stripped,
                })
            elif in_list and stripped.startswith("  ") and tasks:
                tasks[-1]["description"] += "\n" + stripped.strip()

        return tasks

    # -- fleet context management -------------------------------------------

    def read_context(self) -> Dict[str, Any]:
        """Read and parse the fleet-wide CONTEXT.md file.

        Expects ``Key: Value`` lines.  Numeric values are auto-coerced.

        Returns
        -------
        dict
            Parsed key-value pairs (keys lowercased with underscores).
        """
        ctx_path = self._own_context / _CONTEXT_FILE
        if not ctx_path.is_file():
            return {}

        context: Dict[str, Any] = {"_extra": []}
        for line in ctx_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            kv = re.match(r"^([A-Za-z][A-Za-z0-9_ ]+):\s*(.*)", stripped)
            if kv:
                key = kv.group(1).strip().lower().replace(" ", "_")
                value: Any = kv.group(2).strip()
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
                context[key] = value
            else:
                context["_extra"].append(stripped)

        if not context["_extra"]:
            del context["_extra"]
        return context

    def update_context(self, context_data: Dict[str, Any]) -> None:
        """Rewrite CONTEXT.md with the supplied key-value pairs.

        Parameters
        ----------
        context_data :
            Mapping of context keys to values.  ``"_header"`` overrides the
            section heading (default ``"Fleet Context"``).
        """
        _safe_makedirs(self._own_context)
        header = context_data.pop("_header", "Fleet Context")
        lines = [
            f"# {header}", "",
            f"Last Updated: {_now_iso()}",
        ]
        for key, value in context_data.items():
            if key.startswith("_"):
                continue
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
        lines.append("")
        (self._own_context / _CONTEXT_FILE).write_text("\n".join(lines),
                                                       encoding="utf-8")

    def update_priorities(self, priorities: List[Dict[str, Any]]) -> None:
        """Write a new PRIORITY.md from a list of priority entries.

        Each entry needs at least a ``"task"`` key; optional keys are
        ``"priority"`` (``"P0"``–``"P3"``), ``"assignee"``, and ``"notes"``.

        Parameters
        ----------
        priorities :
            Ordered list of priority assignments (highest first).
        """
        _safe_makedirs(self._own_context)
        lines = [
            "# Priority Assignments", "",
            f"Last Updated: {_now_iso()}", "",
        ]
        current_pri: Optional[str] = None
        for idx, entry in enumerate(priorities, start=1):
            priority = entry.get("priority", "P3")
            if priority != current_pri:
                lines.append(f"## {priority}")
                lines.append("")
                current_pri = priority
            notes = entry.get("notes", "")
            notes_str = f" — {notes}" if notes else ""
            lines.append(
                f"{idx}. {entry.get('task', f'Task-{idx}')} "
                f"→ {entry.get('assignee', 'unassigned')}{notes_str}"
            )
        lines.append("")
        (self._own_context / _PRIORITY_FILE).write_text("\n".join(lines),
                                                        encoding="utf-8")

    # -- internal -----------------------------------------------------------

    def _parse_file(self, filepath: Path) -> tuple[Dict[str, str], str]:
        """Read *filepath* and return ``(metadata, body)``."""
        return _parse_front_matter(filepath.read_text(encoding="utf-8"))

    def _check_directories(self) -> bool:
        """Verify all expected coordination directories exist."""
        return all(d.is_dir() for d in (
            self._mib, self._outgoing, self._incoming,
            self._broadcast, self._own_context,
        ))

    def __repr__(self) -> str:
        return f"FleetBridge(repo_root={self._root!r})"
