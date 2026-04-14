"""SmartCRDT Git-Agent Orchestrator.

Central coordination hub that glues together :class:`CommitNarrator`,
:class:`MonorepoAwareness`, :class:`FleetBridge`, and
:class:`CRDTCoordinator` into a single ``SmartCRDTAgent`` facade.

Python 3.9+ stdlib only — zero external dependencies.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from commit_narrator import CommitNarrator
from monorepo_awareness import MonorepoAwareness
from fleet_bridge import FleetBridge
from crdt_coordinator import CRDTCoordinator

_AGENT_VERSION = "0.1.0"
_SUPPORTED_COMMANDS = (
    "claim_task", "narrate_staged", "narrate_diff", "deposit_bottle",
    "scan_bottles", "health_check", "analyze_crdt_impact",
    "get_monorepo_health", "run_workshop", "onboard",
)


class SmartCRDTAgent:
    """Main orchestrator for the SmartCRDT git-agent fleet.

    Wraps all four subsystems behind a unified ``agent.run(cmd)``
    dispatcher so callers need only a single entry point.

    Parameters
    ----------
    repo_root : str, optional
        Path to the monorepo checkout.  When ``None`` the agent operates
        in lightweight mode with no filesystem I/O.
    """

    def __init__(self, repo_root: Optional[str] = None) -> None:
        """Initialise all four subsystems with the given *repo_root*."""
        self._repo_root: Optional[str] = repo_root
        self._session: int = 0
        self._claimed_tasks: Dict[str, Dict[str, Any]] = {}

        self.narrator = CommitNarrator(repo_root=repo_root or ".")
        self.monorepo = MonorepoAwareness(repo_root=repo_root)
        self.fleet = FleetBridge(repo_root=repo_root)
        self.crdt = CRDTCoordinator()

    @property
    def repo_root(self) -> Optional[str]:  # pragma: no cover
        return self._repo_root

    # ------------------------------------------------------------------
    # Task claiming
    # ------------------------------------------------------------------

    def claim_task(self, task_id: str, branch: Optional[str] = None) -> dict:
        """Claim a fleet task and record it locally.

        Returns ``{"success", "task_id", "branch", "message"}``.
        """
        success = self.fleet.claim_task(task_id, branch=branch)
        if success:
            self._claimed_tasks[task_id] = {
                "branch": branch,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
            return {
                "success": True, "task_id": task_id,
                "branch": branch or "unassigned",
                "message": f"Successfully claimed task {task_id}",
            }
        return {
            "success": False, "task_id": task_id,
            "branch": branch or "unassigned",
            "message": f"Failed to claim task {task_id}: not found or already claimed",
        }

    # ------------------------------------------------------------------
    # Commit narration
    # ------------------------------------------------------------------

    def narrate_staged(self, task_id: Optional[str] = None) -> str:
        """Narrate staged git changes via ``git diff --cached``.

        Returns a conventional-commit message with CRDT merge-safety
        notes, or a placeholder when nothing is staged.
        """
        diff_text = self._run_git(["diff", "--cached"])
        if not diff_text.strip():
            return "(no staged changes found — stage files with git add)"
        return self.narrator.narrate(diff_text, task_id=task_id)

    def narrate_diff(self, diff_text: str, task_id: Optional[str] = None) -> str:
        """Narrate an arbitrary unified diff string."""
        if not diff_text or not diff_text.strip():
            return "(empty diff — nothing to narrate)"
        return self.narrator.narrate(diff_text, task_id=task_id)

    # ------------------------------------------------------------------
    # Fleet bridge
    # ------------------------------------------------------------------

    def deposit_bottle(
        self, recipient: str, body: str, bottle_type: str, subject: str,
    ) -> str:
        """Send a fleet bottle and return the created file path."""
        return self.fleet.deposit(
            recipient=recipient, body=body,
            bottle_type=bottle_type, subject=subject,
            session=self._session,
        )

    def scan_bottles(self) -> List[dict]:
        """Check for incoming fleet messages (unread first)."""
        return self.fleet.scan()

    # ------------------------------------------------------------------
    # Health & monitoring
    # ------------------------------------------------------------------

    def health_check(self, session: int = 0) -> dict:
        """Generate a combined health response for fleet coordination.

        Merges fleet-bridge health, monorepo status, and active task
        state into one JSON-serialisable dict.
        """
        fleet_health = self.fleet.generate_health_response(
            session=session,
            tasks_in_progress=list(self._claimed_tasks.keys()),
        )
        mono_health = self.monorepo.health_check()
        return {
            "agent": "smartcrdt-git-agent",
            "version": _AGENT_VERSION,
            "status": "active",
            "session": session,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fleet": {
                "unread_bottles": fleet_health.get("unread_bottles", 0),
                "directories_ok": fleet_health.get("directories_ok", False),
            },
            "monorepo": {
                "status": mono_health.get("status", "unknown"),
                "total_packages": mono_health.get("total_packages", 0),
                "orphaned_count": mono_health.get("orphaned_count", 0),
            },
            "crdt_types_supported": len(self.crdt.get_supported_types()),
            "tasks_claimed": len(self._claimed_tasks),
            "tasks_in_progress": list(self._claimed_tasks.keys()),
        }

    # ------------------------------------------------------------------
    # CRDT coordination
    # ------------------------------------------------------------------

    def analyze_crdt_impact(self, crdt_type: str, operation: str) -> dict:
        """Analyse CRDT merge implications for an operation.

        Returns ``merge_analysis``, ``classification``, ``semantics``,
        and ``supported_types`` keys.  On unknown *crdt_type* an
        ``error`` key is returned instead.
        """
        try:
            merge_result = self.crdt.analyze_merge(
                crdt_type=crdt_type, operation=operation,
                ctx={"replica_id": "agent-local", "state": {}, "peer_states": {}},
            )
        except ValueError as exc:
            return {"error": str(exc), "supported_types": self.crdt.get_supported_types()}

        classification = self.crdt.classify_operation(crdt_type, operation)
        sem = self.crdt.get_semantics(crdt_type)
        return {
            "merge_analysis": merge_result,
            "classification": classification,
            "semantics": {
                "name": sem["name"], "family": sem["family"],
                "convergence": sem["convergence"],
                "commutative": sem["commutative"], "idempotent": sem["idempotent"],
            },
            "supported_types": self.crdt.get_supported_types(),
        }

    # ------------------------------------------------------------------
    # Monorepo awareness
    # ------------------------------------------------------------------

    def get_monorepo_health(self) -> dict:
        """Full monorepo health assessment with category & coverage data."""
        base = self.monorepo.health_check()
        base["step_count"] = self.monorepo.step_count
        base["total_packages"] = self.monorepo.total_packages
        return base

    # ------------------------------------------------------------------
    # Workshop recipes
    # ------------------------------------------------------------------

    def run_workshop(self, recipe_name: str) -> dict:
        """Execute a named multi-step workshop recipe.

        Supported recipes:

        * **full-audit** — monorepo health + dependency graph + test
          coverage + fleet scan.
        * **crdt-review** — enumerate types, generate test vectors.
        * **fleet-sync** — scan bottles, read context/tasks, deposit
          status report.
        """
        steps: List[str] = []
        results: Dict[str, Any] = {}

        if recipe_name == "full-audit":
            for step_name, fn in [
                ("monorepo_health", self.get_monorepo_health),
                ("test_coverage", self.monorepo.refresh_test_coverage),
                ("fleet_scan", self.scan_bottles),
            ]:
                steps.append(step_name)
                results[step_name] = fn()
            if self._repo_root:
                steps.append("dependency_graph")
                results["dependency_graph"] = self.monorepo.build_dependency_graph(
                    self._repo_root,
                )

        elif recipe_name == "crdt-review":
            types = self.crdt.get_supported_types()
            steps.append("enumerate_types")
            results["types"] = types
            for ct in types:
                steps.append(f"test_vectors_{ct}")
                results[f"test_vectors_{ct}"] = self.crdt.generate_test_vectors(ct, 3)

        elif recipe_name == "fleet-sync":
            steps.append("scan_bottles")
            bottles = self.scan_bottles()
            results["incoming_bottles"] = bottles
            steps.append("read_context")
            results["fleet_context"] = self.fleet.read_context()
            steps.append("read_tasks")
            tasks = self.fleet.read_tasks()
            results["available_tasks"] = [t for t in tasks if t.get("claimed_by") is None]
            steps.append("deposit_status")
            unread = sum(1 for b in bottles if b.get("unread"))
            results["status_report"] = self.deposit_bottle(
                recipient="fleet", bottle_type="report",
                subject="Fleet sync status",
                body=(f"Fleet sync complete. Unread bottles: {unread}. "
                      f"Unclaimed tasks: {len(results['available_tasks'])}."),
            )

        else:
            return {
                "recipe": recipe_name, "steps_completed": [], "results": {},
                "error": f"Unknown recipe '{recipe_name}'. "
                         f"Supported: full-audit, crdt-review, fleet-sync",
            }

        return {"recipe": recipe_name, "steps_completed": steps, "results": results}

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------

    def onboard(self, repo_root: str) -> dict:
        """Onboard to a SmartCRDT clone.

        1. Point all subsystems at *repo_root*.
        2. Build the dependency graph.
        3. Read existing fleet context.
        4. Scan for pending bottles.
        5. Return onboarding summary.
        """
        self._repo_root = repo_root
        self.narrator = CommitNarrator(repo_root=repo_root)
        self.monorepo = MonorepoAwareness(repo_root=repo_root)
        self.fleet = FleetBridge(repo_root=repo_root)

        summary: Dict[str, Any] = {"repo_root": repo_root}

        # Build dependency graph.
        try:
            dep_graph = self.monorepo.build_dependency_graph(repo_root)
            summary["dependency_graph"] = {
                "status": "ok",
                "packages_indexed": self.monorepo.total_packages,
                "edges": sum(len(v) for v in dep_graph.values()),
            }
        except Exception as exc:  # pragma: no cover — defensive
            summary["dependency_graph"] = {"status": "error", "detail": str(exc)}

        # Read existing fleet context.
        context = self.fleet.read_context()
        summary["fleet_context"] = {
            "status": "ok" if context else "empty",
            "keys": list(context.keys()) if context else [],
        }

        # Scan for existing bottles.
        bottles = self.scan_bottles()
        summary["fleet_scan"] = {
            "total_bottles": len(bottles),
            "unread_bottles": sum(1 for b in bottles if b.get("unread")),
        }

        # Monorepo health.
        summary["monorepo_health"] = self.monorepo.health_check().get("status", "unknown")
        summary["onboarded_at"] = datetime.now(timezone.utc).isoformat()
        return summary

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    def run(self, command: str, **kwargs: Any) -> Any:
        """Dispatch *command* to the matching agent method.

        Raises :exc:`AttributeError` for unknown commands.
        """
        handler = getattr(self, command, None)
        if handler is None or not callable(handler):
            raise AttributeError(
                f"Unknown command '{command}'. "
                f"Supported: {', '.join(_SUPPORTED_COMMANDS)}"
            )
        return handler(**kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_git(args: List[str]) -> str:
        """Run a git subprocess and return stdout (empty string on failure)."""
        try:
            result = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=30,
            )
            return result.stdout or ""
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_agent(repo_root: Optional[str] = None) -> SmartCRDTAgent:
    """Create and return a configured :class:`SmartCRDTAgent`.

    Recommended entry-point::

        from agent import create_agent
        agent = create_agent("/path/to/smartcrdt")
        msg = agent.narrate_staged(task_id="T-42")
    """
    return SmartCRDTAgent(repo_root=repo_root)
