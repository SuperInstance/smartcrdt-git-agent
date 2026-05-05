# SmartCRDT Git Agent

> Git operations via CRDT semantics. Conflict-free, eventually-consistent git workflow for multi-agent fleets.

**Part of the [SuperInstance fleet](https://github.com/SuperInstance)** — git-agent with CRDT conflict resolution for concurrent agent operations.

## What It Does

Git operations with automatic conflict resolution:
- **Concurrent pushes** — two agents editing the same file? CRDT merge, no conflicts.
- **No lockfiles** — agents never wait for each other
- **Fleet sync** — git push propagates to all vessel repos automatically

## Architecture

```
Agent → SmartCRDT → git commit (CRDT semantics) → fleet repo
```

## Components

- `pelagic-twin/` — vessel shadow repo with CRDT sync
- `fleet/` — fleet-wide coordination layer
- `skills/` — agent skill definitions for git operations

## Related

- [PLATO](https://github.com/SuperInstance/plato-room-phi) — tile-based knowledge layer
- [git-agent](https://github.com/SuperInstance/git-agent) — original git-agent architecture