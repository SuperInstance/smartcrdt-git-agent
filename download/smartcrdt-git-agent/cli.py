#!/usr/bin/env python3
"""cli.py — CLI interface for the SmartCRDT git-agent.

Provides subcommands for commit narration, fleet coordination,
monorepo awareness, CRDT analysis, workshop management, and onboarding.

Usage::

    python cli.py --pretty narrate --staged
    python cli.py fleet scan
    python cli.py mono packages --category crdt-core
    python cli.py crdt analyze --type g-counter --operation increment
    python cli.py workshop list
    python cli.py workshop bootcamp --level 3
    python cli.py --onboard /path/to/repo
    python cli.py claim --task T-001

Output is JSON by default; pass ``--pretty`` before the subcommand for
indented human-readable output.  Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

_VERSION = "0.1.0"

# Sub-subcommand mappings used by the dispatch table to provide helpful
# error messages when a command group is invoked without a subcommand.
_REQUIRED_SUBCOMMANDS = {
    "fleet": "fleet_command",
    "mono": "mono_command",
    "crdt": "crdt_command",
    "workshop": "workshop_command",
}


# ── Output helpers ──────────────────────────────────────────────────────

def _emit(data: Any, pretty: bool = False) -> None:
    """Serialize *data* as JSON to stdout."""
    kw: Dict[str, Any] = {"ensure_ascii": False}
    if pretty:
        kw["indent"] = 2
    json.dump(data, sys.stdout, **kw)
    sys.stdout.write("\n")


def _error(msg: str, code: int = 1) -> None:
    """Print error to stderr and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# ── Lazy agent factory ──────────────────────────────────────────────────

_agent_cache: Optional[Any] = None


def _agent(repo_root: Optional[str] = None):
    """Lazily create the SmartCRDT agent singleton."""
    global _agent_cache
    if _agent_cache is None:
        from agent import create_agent          # noqa: lazy import
        _agent_cache = create_agent(repo_root=repo_root)
    return _agent_cache


# ── Command handlers ────────────────────────────────────────────────────

def _cmd_narrate(args: argparse.Namespace) -> None:
    ag = _agent()
    if args.staged:
        result = ag.narrate_staged(task_id=args.task)
    elif args.diff is not None:
        result = ag.narrate_diff(args.diff, task_id=args.task)
    else:
        _error("narrate requires --staged or --diff <text>")
    _emit({"narration": result}, args.pretty)


def _cmd_fleet(args: argparse.Namespace) -> None:
    """Handle fleet subcommands: scan, deposit, health."""
    ag = _agent()
    if args.fleet_command == "scan":
        bottles = ag.scan_bottles()
        _emit({"bottles": bottles, "count": len(bottles)}, args.pretty)
    elif args.fleet_command == "deposit":
        path = ag.deposit_bottle(
            recipient=args.to, body=args.body,
            bottle_type=args.type, subject=args.subject)
        _emit({"status": "deposited", "filepath": path}, args.pretty)
    elif args.fleet_command == "health":
        _emit(ag.health_check(), args.pretty)


def _cmd_mono(args: argparse.Namespace) -> None:
    """Handle mono subcommands: health, packages, deps, affected."""
    ag = _agent()
    if args.mono_command == "health":
        _emit(ag.get_monorepo_health(), args.pretty)
    elif args.mono_command == "packages":
        pkgs = ag.monorepo.get_packages(category=args.category)
        _emit({"packages": pkgs, "count": len(pkgs),
                "category": args.category or "all"}, args.pretty)
    elif args.mono_command == "deps":
        info = ag.monorepo.get_package_info(args.package)
        if not info:
            _error(f"Unknown package: {args.package}")
        deps = ag.monorepo.get_dependencies(args.package)
        rdeps = ag.monorepo.get_reverse_dependencies(args.package)
        trans = ag.monorepo.get_transitive_dependents(args.package)
        _emit({"package": args.package, "category": info.get("category"),
                "dependencies": deps, "reverse_dependencies": rdeps,
                "transitive_dependents": trans}, args.pretty)
    elif args.mono_command == "affected":
        files = [f.strip() for f in args.files.split(",") if f.strip()]
        if not files:
            _error("affected requires at least one file path")
        affected = ag.monorepo.identify_affected_packages(files)
        _emit({"affected_packages": affected, "changed_files": files}, args.pretty)


def _cmd_crdt(args: argparse.Namespace) -> None:
    """Handle crdt subcommands: analyze, semantics, conflicts."""
    ag = _agent()
    if args.crdt_command == "analyze":
        result = ag.analyze_crdt_impact(
            crdt_type=args.type, operation=args.operation)
        _emit(result, args.pretty)
    elif args.crdt_command == "semantics":
        try:
            _emit(ag.crdt.get_semantics(args.type), args.pretty)
        except ValueError as exc:
            _error(str(exc))
    elif args.crdt_command == "conflicts":
        try:
            ops = [
                {"operation": args.operation or "set",
                 "replica_id": "r1", "timestamp": 1000},
                {"operation": args.operation or "set",
                 "replica_id": "r2", "timestamp": 1001}]
            conflicts = ag.crdt.detect_conflicts(args.type, ops)
            _emit({"crdt_type": args.type, "sample_operations": ops,
                    "conflicts": conflicts, "conflict_count": len(conflicts)},
                   args.pretty)
        except ValueError as exc:
            _error(str(exc))


def _cmd_workshop(args: argparse.Namespace) -> None:
    """Handle workshop subcommands: list, run, bootcamp."""
    from workshop_manager import WorkshopManager    # noqa: lazy import
    wm = WorkshopManager()
    if args.workshop_command == "list":
        recipes = wm.list_recipes()
        _emit({"recipes": recipes, "count": len(recipes)}, args.pretty)
    elif args.workshop_command == "run":
        try:
            _emit(wm.run_recipe(args.name), args.pretty)
        except KeyError as exc:
            _error(str(exc))
    elif args.workshop_command == "bootcamp":
        if args.level is not None:
            try:
                lv = wm.get_bootcamp_level(int(args.level))
                path = wm.get_learning_path(int(args.level))
                _emit({"level": lv, "next_steps": path}, args.pretty)
            except KeyError as exc:
                _error(str(exc))
        else:
            levels = wm.list_bootcamp_levels()
            _emit({"bootcamp_levels": levels,
                    "total_levels": len(levels)}, args.pretty)


def _cmd_claim(args: argparse.Namespace) -> None:
    """Handle claim subcommand: claim a fleet task by ID."""
    result = _agent().claim_task(args.task, branch=args.branch)
    _emit(result, args.pretty)


def _cmd_onboard(args: argparse.Namespace) -> None:
    """Onboard to a SmartCRDT clone repository."""
    global _agent_cache
    if _agent_cache is not None:
        _error("Agent already initialised; onboard must be the first action")
    from agent import create_agent              # noqa: lazy import
    _agent_cache = create_agent(repo_root=args.onboard)
    _emit(_agent_cache.onboard(args.onboard), args.pretty)


# ── Parser construction ─────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """Build the full CLI argument parser with all 13 subcommands."""
    p = argparse.ArgumentParser(
        prog="smartcrdt-git-agent",
        description="SmartCRDT git-agent: fleet-aware CRDT monorepo assistant.",
        epilog=(
            "examples:\n"
            "  %(prog)s --pretty narrate --staged\n"
            "  %(prog)s --pretty fleet scan\n"
            "  %(prog)s --pretty mono health\n"
            "  %(prog)s --pretty crdt analyze --type g-counter --operation increment\n"
            "  %(prog)s --pretty workshop bootcamp --level 3\n"
            "  %(prog)s --onboard /path/to/smartcrdt\n"
            "  %(prog)s claim --task T-001\n",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pretty", action="store_true", default=False,
                   help="Emit human-readable indented JSON")
    p.add_argument("--onboard", metavar="REPO_ROOT",
                   help="Onboard to a SmartCRDT clone at REPO_ROOT")
    p.add_argument("--version", action="version", version=f"%(prog)s {_VERSION}")
    sub = p.add_subparsers(dest="command")

    # narrate ──────────────────────────────────────────────────────────
    n = sub.add_parser("narrate", help="Narrate git changes with CRDT awareness")
    n.add_argument("--staged", action="store_true",
                   help="Narrate staged changes (git diff --cached)")
    n.add_argument("--diff", type=str, metavar="TEXT",
                   help="Narrate a specific diff string")
    n.add_argument("--task", type=str, metavar="ID",
                   help="Reference a fleet task ID (e.g. T-042)")

    # fleet ────────────────────────────────────────────────────────────
    f = sub.add_parser("fleet", help="Fleet coordination via message-in-a-bottle")
    fs = f.add_subparsers(dest="fleet_command")
    fs.add_parser("scan", help="Scan for incoming fleet bottles")
    d = fs.add_parser("deposit", help="Deposit a fleet bottle")
    d.add_argument("--to", required=True, help="Recipient agent name")
    d.add_argument("--type", required=True,
                   help="Bottle type (report|directive|response|insight)")
    d.add_argument("--subject", required=True, help="Bottle subject line")
    d.add_argument("--body", required=True, help="Bottle body (markdown)")
    fs.add_parser("health", help="Generate fleet health check response")

    # mono ─────────────────────────────────────────────────────────────
    m = sub.add_parser("mono", help="Monorepo awareness and package management")
    ms = m.add_subparsers(dest="mono_command")
    ms.add_parser("health", help="Monorepo health check")
    pk = ms.add_parser("packages", help="List monorepo packages")
    pk.add_argument("--category", type=str, metavar="CAT",
                    help="Filter by category (e.g. crdt-core, infrastructure)")
    dp = ms.add_parser("deps", help="Show package dependencies")
    dp.add_argument("package", type=str, help="Package name")
    af = ms.add_parser("affected", help="Show packages affected by changed files")
    af.add_argument("files", type=str, metavar="FILE1,FILE2,...",
                    help="Comma-separated list of changed file paths")

    # crdt ─────────────────────────────────────────────────────────────
    c = sub.add_parser("crdt", help="CRDT merge analysis and conflict detection")
    cs = c.add_subparsers(dest="crdt_command")
    ca = cs.add_parser("analyze", help="Analyze CRDT merge implications")
    ca.add_argument("--type", required=True, help="CRDT type (e.g. g-counter)")
    ca.add_argument("--operation", required=True,
                    help="Operation name (e.g. increment)")
    se = cs.add_parser("semantics", help="Get CRDT type semantics")
    se.add_argument("type", type=str, help="CRDT type identifier")
    cf = cs.add_parser("conflicts", help="Detect CRDT conflicts")
    cf.add_argument("--type", required=True, help="CRDT type")
    cf.add_argument("--operation", type=str, default="set",
                    help="Operation to test (default: set)")

    # workshop ─────────────────────────────────────────────────────────
    w = sub.add_parser("workshop", help="Workshop recipes and bootcamp management")
    ws = w.add_subparsers(dest="workshop_command")
    ws.add_parser("list", help="List available workshop recipes")
    wr = ws.add_parser("run", help="Run a workshop recipe")
    wr.add_argument("name", type=str, help="Recipe name (e.g. add-counter)")
    wb = ws.add_parser("bootcamp", help="Show bootcamp info")
    wb.add_argument("--level", type=int, metavar="N",
                    help="Show specific bootcamp level (1-5)")

    # claim ────────────────────────────────────────────────────────────
    cl = sub.add_parser("claim", help="Claim a fleet task")
    cl.add_argument("--task", required=True, metavar="TASK_ID",
                    help="Task ID (e.g. T-001)")
    cl.add_argument("--branch", type=str, default=None,
                    help="Git branch for the task")
    return p


# ── Entry point ─────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    """Parse arguments, dispatch to handler, and return exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --onboard is a top-level flag, not a subcommand.
    if args.onboard:
        _cmd_onboard(args)
        return 0

    # Dispatch to the appropriate handler.
    dispatch = {
        "narrate": _cmd_narrate,
        "fleet": _cmd_fleet,
        "mono": _cmd_mono,
        "crdt": _cmd_crdt,
        "workshop": _cmd_workshop,
        "claim": _cmd_claim,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 0

    # Check that a sub-subcommand was provided for grouped commands.
    sub_attr = _REQUIRED_SUBCOMMANDS.get(args.command)
    if sub_attr and getattr(args, sub_attr, None) is None:
        parser.parse_args([args.command, "--help"])
        return 1

    handler(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
