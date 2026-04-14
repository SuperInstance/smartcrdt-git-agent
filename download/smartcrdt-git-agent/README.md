# smartcrdt-git-agent

**Co-Captain of the SmartCRDT Monorepo** — A git-native AI agent specialized in CRDT-aware commit narration, monorepo coordination, and fleet bridge operations.

## Role

SmartCRDT's dedicated git-agent. Monitors the 81-package pnpm workspace, narrates commits with CRDT merge awareness, coordinates with the Pelagic fleet via message-in-a-bottle, and runs workshops for onboarding.

## Architecture

```
smartcrdt-git-agent/
├── agent.py              # Main orchestrator: task claim, commit narrate, fleet bridge
├── commit_narrator.py    # CRDT-aware commit message generation
├── monorepo_awareness.py # 81-package dependency graph tracking
├── fleet_bridge.py       # message-in-a-bottle async coordination
├── crdt_coordinator.py   # 7 CRDT type merge analysis engine
├── workshop_manager.py   # 6 recipes + 5-level bootcamp
├── cli.py                # 13 CLI subcommands
├── tests/
│   └── test_all.py       # 113 tests, zero external deps
├── CHARTER.md            # Fleet contract
├── CAPABILITY.toml       # Machine-readable skill registry
└── message-in-a-bottle/  # Fleet async comms
```

## Quick Start

```bash
# Run all tests
python -m pytest tests/test_all.py -v

# Onboard to a SmartCRDT clone
python cli.py --onboard /path/to/SmartCRDT

# Narrate staged changes
python cli.py narrate --staged

# Scan for fleet bottles
python cli.py fleet scan

# Check monorepo health
python cli.py mono health
```

## CRDT Types Tracked

| Type | Package | Merge Semantics |
|------|---------|-----------------|
| Counter | `@smartcrdt/counter` | Last-writer-wins increment |
| Set (OR) | `@smartcrdt/set` | Observed-remove |
| Register (LWW) | `@smartcrdt/register` | Last-writer-wins |
| Vector Clock | `@smartcrdt/vector-clock` | HLC-based ordering |
| Gossip | `@smartcrdt/gossip` | Anti-entropy propagation |
| Map | `@smartcrdt/map` | Composite CRDT |
| Sequence | `@smartcrdt/sequence` | Ordered character list |

## Fleet Integration

- **Protocol**: message-in-a-bottle (async fire-and-forget)
- **Commit format**: `type(scope): subject [T-XXX]`
- **Branch format**: `smartcrdt-git-agent/T-XXX`
- **Health check**: JSON response with session status
- **Zero external deps**: Python 3.9+ stdlib only

## Commit Message Convention

```
feat(counter): add bounded counter with max value [T-042]
fix(gossip): resolve split-brain in anti-entropy loop [T-043]
test(vector-clock): add HLC causality violation tests [T-044]
```

## License

MIT
