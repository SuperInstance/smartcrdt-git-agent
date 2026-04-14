# smartcrdt-git-agent

**Co-Captain of the SmartCRDT Monorepo** — A git-native AI agent specialized in CRDT-aware commit narration, monorepo coordination, fleet health monitoring, and emergent consensus across autonomous agent fleets.

## Overview

`smartcrdt-git-agent` is a zero-dependency Python orchestrator that unifies **seven subsystems** behind a single `agent.run(cmd)` facade. It monitors an 85-package pnpm monorepo, narrates every commit with awareness of CRDT merge semantics, coordinates with a fleet of autonomous agents via asynchronous message-in-a-bottle, and provides deep fleet health monitoring with circuit-breaker state machines and emergent consensus through a dream-journal CRDT engine.

The agent is designed for the **Pelagic AI fleet** — a collection of autonomous git agents that coordinate without central servers, using CRDT-based data structures and filesystem-based async messaging.

**Key capabilities:**

- **CRDT-aware commit narration** — detects 7 CRDT families across 19 types, generates conventional-commit messages with merge-safety warnings
- **Monorepo awareness** — tracks 85 packages across 12 categories, builds dependency graphs, identifies affected packages from changed files
- **Fleet coordination** — message-in-a-bottle protocol for agent-to-agent async communication with YAML front-matter
- **Fleet health monitoring** — circuit-breaker state machine (healthy → degraded → critical → necrotic) with tow protocol for failover
- **Drift logging** — append-only CRDT audit trail with vector-clock causal ordering and anomaly detection
- **Repo cartography** — fleet-wide dependency graph with impact analysis, cycle detection, topological ordering, and health scoring
- **Tidepool Oracle** — emergent consensus engine where agents "dream" decision paths and consensus crystallizes from CRDT-merged patterns

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        smartcrdt-git-agent (v0.3.0)                     │
│                          SmartCRDTAgent facade                          │
│                      agent.run(cmd, **kwargs)                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────────────┐  │
│  │   Commit     │  │    Monorepo      │  │     Fleet Bridge         │  │
│  │   Narrator   │  │    Awareness     │  │  (message-in-a-bottle)   │  │
│  │              │  │                  │  │                          │  │
│  │ • Diff parse │  │ • 85 packages    │  │ • Deposit bottles        │  │
│  │ • CRDT detect│  │ • Dep graph      │  │ • Scan bottles           │  │
│  │ • Scope      │  │ • Test coverage  │  │ • Claim tasks            │  │
│  │   resolution │  │ • Affected pkgs  │  │ • Health check           │  │
│  │ • Merge      │  │ • Health check   │  │ • Read context           │  │
│  │   warnings   │  │                  │  │ • Respond                │  │
│  └──────┬───────┘  └────────┬─────────┘  └───────────┬─────────────┘  │
│         │                   │                        │                 │
│  ┌──────┴───────────────────┴────────────────────────┴───────────────┐ │
│  │                    CRDT Coordinator (19 types)                     │ │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────┐ ┌───┐ ┌──┐ │ │
│  │  │Counter │ │  Set   │ │  Reg   │ │ Clock  │ │Gossip│ │Map│ │Seq│ │ │
│  │  │ G/PN/  │ │ AW/RW/ │ │ LWW/   │ │ Lam/   │ │ Anti/│ │   │ │RGA│ │ │
│  │  │ Bounded│ │ OR-Set │ │ MV-Reg │ │ VC/HLC │ │Plum/ │ │Crdt│ │Tre│ │ │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ └──────┘ └───┘ └──┘ │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────────────┐  │
│  │    Drift     │  │  Repo            │  │   Necrosis Detector     │  │
│  │    Log       │  │  Cartographer    │  │   (circuit-breaker)      │  │
│  │              │  │                  │  │                          │  │
│  │ • Vector clk │  │ • Impact analysis│  │ • healthy→degraded→     │  │
│  │ • Causal     │  │ • Cycle detect   │  │   critical→necrotic     │  │
│  │   chains     │  │ • Topo sort     │  │ • Tow protocol          │  │
│  │ • Anomaly    │  │ • Fleet health   │  │ • Beachcomb scan        │  │
│  │   detection  │  │ • Cluster map    │  │ • Forensics             │  │
│  │ • Metrics    │  │ • Merge ordering │  │ • Fleet pulse (0–1)     │  │
│  └──────────────┘  └──────────────────┘  └───────────┬─────────────┘  │
│                                                       │                 │
│  ┌────────────────────────────────────────────────────┴─────────────┐  │
│  │                    Tidepool Oracle (v0.3.0)                       │  │
│  │                   Emergent Consensus Engine                       │  │
│  │                                                                 │  │
│  │  Agent A ──→ Dream Journal ──┐                                   │  │
│  │  Agent B ──→ Dream Journal ──┼──→ CRDT Merge ──→ Fleet Journal   │  │
│  │  Agent N ──→ Dream Journal ──┘         │              │          │  │
│  │                                     consult()     get_wisdom()     │  │
│  │                                   recommendations  report          │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                    Workshop Manager (6 recipes)                  │  │
│  │     5-Level Bootcamp: Greenhorn → Deckhand → Navigator →        │  │
│  │                         Captain → Fleet Admiral                 │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                         CLI (13 commands)                       │  │
│  │  narrate │ fleet │ mono │ crdt │ workshop │ claim │ onboard       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘

                         Python 3.9+ stdlib only
                         Zero external dependencies
```

## CRDT-Git Model

The agent bridges two worlds — **CRDT merge semantics** and **git operations** — to provide merge-safe commit narration and conflict-aware development.

### How CRDT types map to git workflows

```
  CRDT Merge Semantics              Git Workflow
  ──────────────────────            ─────────────────
  G-Counter (increment)        →    Add-only lines, feature counts
  PN-Counter (inc/dec)         →    Reversible changes, vote tallies
  OR-Set (add/remove tags)     →    Member lists, feature flags
  LWW-Register (timestamp)     →    Config, metadata (last write wins)
  MV-Register (concurrent)     →    Conflict-aware data, audit trails
  Vector Clock (causal order)   →    Branch ordering, dependency tracking
  HLC (physical+logical)       →    Distributed event ordering
  Gossip / Anti-Entropy        →    Fleet message propagation
  CRDT Map (nested composite)  →    Document models, game state
  RGA / Treedoc (sequence)     →    Collaborative text editing
```

### Conflict Resolution Pipeline

```
  git diff --cached
       │
       ▼
  ┌─────────────────┐
  │  Diff Parser    │ ← Extract file paths, insertions, deletions
  └────────┬────────┘
           │
       ┌───┴───┐
       ▼       ▼
  ┌────────┐ ┌────────────┐
  │ Path   │ │  Content   │
  │ Match  │ │  Regex     │ ← 7 CRDT family patterns
  │ (FILE_ │ │  Scan      │
  │PATTERNS│ │  (_COMPILED│
  │)       │ │  _KW)      │
  └───┬────┘ └────┬───────┘
      └──────┬─────┘
             ▼
  ┌─────────────────┐
  │  CRDT Merge     │ ← analyze_merge(crdt_type, operation, ctx)
  │  Analysis       │    → concurrent detection, causal ordering,
  └────────┬────────┘    → hazard detection (clock-skew, lost-update)
           │
           ▼
  ┌─────────────────┐
  │  Conflict       │ ← detect_conflicts(crdt_type, ops)
  │  Detection      │    → concurrent-write, lost-update-risk,
  └────────┬────────┘    → ordering-ambiguity
           │
           ▼
  ┌─────────────────┐
  │  Resolution     │ ← recommend_resolution(crdt_type, conflict)
  │  Strategy       │    → counter: merge-counters
  └────────┬────────┘    → set: crdt-set-merge
           │             → register: lww-tiebreak
           │             → map: recursive-merge
           │             → sequence: positional-merge
           ▼
  ┌─────────────────┐
  │  Merge-Safety   │ → Conventional commit with CRDT-aware body
  │  Commit Message │    feat(counter): add bounded counter [T-042]
  └─────────────────┘    Merge-semantics notes:
                           * Bounded counter semantics detected.
                           * Verify overflow handling across replicas.
```

### CRDT Types Tracked (19 across 7 families)

| Family | Types | Merge Semantics |
|--------|-------|-----------------|
| **Counter** | G-Counter, PN-Counter, Bounded Counter | Element-wise max; commutative, idempotent |
| **Set** | Add-Wins, Remove-Wins, Observed-Remove (OR-Set) | Tag union; concurrent add/remove resolution |
| **Register** | LWW-Register, Multi-Value Register | Timestamp tiebreak vs. concurrent value retention |
| **Clock** | Lamport Clock, Vector Clock, HLC | Max-merge with causal ordering guarantees |
| **Gossip** | Anti-Entropy, Plumtree, HyParView | O(log n) delivery with lazy anti-entropy recovery |
| **Map** | CRDT Map | Recursive nested CRDT merge per key |
| **Sequence** | RGA, Treedoc, Logoot, Yjs-style | Ordered character lists with unique positional IDs |

## Quick Start

### Prerequisites

- Python 3.9+ (stdlib only — no pip install needed)
- git (for commit narration and diff parsing)

### Installation

```bash
git clone <smartcrdt-repo-url>
cd smartcrdt-git-agent
```

### Running Tests

```bash
# Full test suite (113+ tests, zero external deps)
python -m pytest tests/test_all.py -v

# Property-based CRDT tests
python -m pytest tests/test_property_based.py -v

# New subsystem tests (v0.2.0+)
python -m pytest tests/test_new_subsystems.py -v
```

### CLI Usage

```bash
# Onboard to a SmartCRDT clone
python cli.py --onboard /path/to/SmartCRDT

# Narrate staged changes (CRDT-aware commit messages)
python cli.py --pretty narrate --staged
python cli.py --pretty narrate --staged --task T-042

# Fleet coordination
python cli.py fleet scan                          # Scan for incoming bottles
python cli.py fleet deposit --to fleet --type report --subject "Status" --body "..."
python cli.py fleet health                        # Health check JSON

# Monorepo awareness
python cli.py --pretty mono health               # Full monorepo health
python cli.py --pretty mono packages --category crdt-core
python cli.py --pretty mono deps <package>        # Dependencies + reverse deps
python cli.py mono affected file1.ts,file2.ts     # Affected package impact

# CRDT merge analysis
python cli.py --pretty crdt analyze --type g-counter --operation increment
python cli.py --pretty crdt semantics lww-register
python cli.py --pretty crdt conflicts --type or-set --operation add

# Workshop & bootcamp
python cli.py --pretty workshop list              # List 6 available recipes
python cli.py --pretty workshop bootcamp --level 3 # Navigator level
python cli.py workshop run add-counter            # Execute a recipe

# Task claiming
python cli.py claim --task T-001 --branch feat/counter
```

### Programmatic API

```python
from agent import create_agent

# Create the agent (point to a repo or run lightweight)
agent = create_agent("/path/to/smartcrdt")

# Narrate staged changes with CRDT awareness
msg = agent.narrate_staged(task_id="T-042")
print(msg)
# → feat(counter): add bounded counter with max value [T-042]
#   Merge-semantics notes:
#     * Bounded counter semantics detected.

# Analyze CRDT merge impact
result = agent.analyze_crdt_impact("g-counter", "increment")
print(result["merge_analysis"]["hazards"])
# → [] (commutative — no hazards)

# Get monorepo health
health = agent.get_monorepo_health()
print(f"Packages: {health['total_packages']}, Status: {health['status']}")

# Record a dream in the Tidepool Oracle (emergent consensus)
agent.record_dream(
    agent_id="agent-7",
    scenario="merge-conflict in crdt-coordinator",
    action="apply three-way merge with vector-clock tiebreak",
    predicted_outcome="clean convergence in 2 rounds",
    confidence=0.82,
    tags=["merge-strategy", "crdt"],
)
guidance = agent.consult_oracle("merge-conflict in crdt-coordinator")
print(guidance["recommendation"]["action"])
```

## Integration

### Fleet Integration (message-in-a-bottle)

The agent coordinates with other fleet agents through a filesystem-based async protocol. No network services are required.

**Protocol**: message-in-a-bottle (async fire-and-forget)

```
  message-in-a-bottle/
  ├── for-fleet/          ← Outgoing fleet-wide messages
  ├── from-fleet/         ← Incoming fleet context
  ├── for-any-vessel/     ← Open broadcasts
  └── for-oracle1/        ← Direct channel to lighthouse keeper
```

**Bottle format** (YAML front-matter + Markdown body):

```markdown
---
Bottle-To: fleet
Bottle-From: smartcrdt-git-agent
Bottle-Type: report
Session: 1
Timestamp: 2026-04-15T12:00:00+00:00
Subject: Fleet sync status
---

Fleet sync complete. Unread bottles: 3. Unclaimed tasks: 5.
```

**Bottle types**: `report` | `directive` | `response` | `insight`

### Commit Message Convention

```
type(scope): subject [T-XXX]

Merge-semantics notes:
  * warning about CRDT implications

Affected packages: pkg-a, pkg-b
```

**Types**: `feat` | `fix` | `test` | `docs` | `chore` | `refactor` | `perf`

**Branch format**: `smartcrdt-git-agent/T-XXX`

### Task Lifecycle

```
  TASKS.md                CLAIMED.md              Git Branch
  ─────────               ──────────               ──────────
  1. [T-001] Task    →   T-001 | agent | branch  →  smartcrdt-git-agent/T-001
  2. [T-002] Task        (claimed_at timestamp)     (work happens here)
```

### Health Check Response

```json
{
  "agent": "smartcrdt-git-agent",
  "version": "0.3.0",
  "status": "active",
  "session": 1,
  "fleet": { "unread_bottles": 0, "directories_ok": true },
  "monorepo": { "status": "healthy", "total_packages": 85, "orphaned_count": 0 },
  "crdt_types_supported": 19,
  "tasks_claimed": 2,
  "tasks_in_progress": ["T-042", "T-043"]
}
```

### Workshop & Bootcamp Integration

New fleet agents onboard through a structured 5-level bootcamp:

| Level | Name | Focus |
|-------|------|-------|
| 1 | **Greenhorn** | Repo layout, CRDT concepts, running tests |
| 2 | **Deckhand** | Core CRDT implementations, unit testing |
| 3 | **Navigator** | Implementing CRDT variants, gossip fundamentals |
| 4 | **Captain** | CRDT design, cross-package coordination, code review |
| 5 | **Fleet Admiral** | Architecture governance, fleet coordination, mentoring |

**Available recipes** (6):

| Recipe | Difficulty | Est. Time | Description |
|--------|-----------|-----------|-------------|
| `add-test` | Beginner | 30 min | Add tests to an existing CRDT package |
| `add-counter` | Intermediate | 45 min | Implement a new counter variant |
| `add-set` | Intermediate | 50 min | Implement a new set variant |
| `add-register` | Intermediate | 40 min | Implement a new register variant |
| `add-gossip` | Advanced | 90 min | Implement a new gossip protocol |
| `fix-merge` | Advanced | 60 min | Diagnose and fix CRDT merge bugs |

### Necrosis Detection Thresholds

| State | Default Threshold | Description |
|-------|-------------------|-------------|
| **healthy** | baseline | Agent actively heartbeating |
| **degraded** | 30 min silence | No heartbeat received |
| **critical** | 2 hours silence | Prolonged silence |
| **necrotic** | 6 hours silence | Agent considered failed |
| **test attrition** | 10% drop | Test count declined significantly |

When an agent goes necrotic, the **tow protocol** automatically identifies the nearest healthy agent to take over its workload.

### Constraints

- **Zero external dependencies** — Python 3.9+ stdlib only
- **One coder per repo** — sacred fleet rule
- **Push after every commit** — never accumulate
- **All test-driven** — 113+ tests must pass before any push

## License

MIT

---

<img src="callsign1.jpg" width="128" alt="callsign">
