# User Algo Workspace & Loading Mechanics

## Overview

The `user_algos/` directory is the canonical workspace for user-authored algorithms. Each strategy lives in its own subdirectory with all related files co-located — worker, decision logic, and scenario set(s).

> For Worker vs DecisionLogic concepts, see [quickstart_guide.md](quickstart_guide.md).
> For the reference system and contract model, see [worker_naming_doc.md](worker_naming_doc.md).

---

## Directory Structure

```
user_algos/                      ← user algo workspace (gitignored by default)
└── my_algo/                     ← one directory per strategy
    ├── my_strategy.py           ← decision logic
    ├── my_range_worker.py       ← worker
    └── my_algo_eurusd.json      ← scenario set

python/framework/
├── workers/core/                       ← CORE workers (read-only, framework)
├── decision_logic/core/                ← CORE decision logics (read-only)
└── factory/
    ├── worker_factory.py               ← path-based loader for workers
    └── decision_logic_factory.py       ← path-based loader for decision logics
```

`user_algos/` is tracked as an empty stub via `.gitkeep`. Contents are gitignored so each user can independently manage their own algo files — **in a git repository of their own**, created once with `git init` inside `user_algos/` (with a `.gitignore` for `__pycache__/` and `*.pyc`). A run records the commit of that repository; code under no version control makes every backtest report warn that the run cannot be reproduced, and refuses a real-money start (see the quickstart's Step 4).

---

## On-Demand Loading Flow

Workers and decision logics are **not pre-scanned at startup**. They are loaded on-demand when their path appears in a scenario set or AutoTrader profile.

```
┌──────────────────────────────────────────────────────────┐
│                 SCENARIO RUN                              │
│                                                          │
│  Config: "decision_logic_type":                          │
│    "user_algos/my_algo/my_strategy.py"                   │
│                   │                                      │
│                   ▼                                      │
│  DecisionLogicFactory.resolve_logic_class()              │
│    ├── starts with "CORE/" → registry lookup ✓           │
│    └── otherwise → _load_path_logic(path)                │
│          ├── resolve path (abs or relative to cwd)       │
│          ├── load via spec_from_file_location()          │
│          ├── introspect: find exactly 1                  │
│          │   AbstractDecisionLogic subclass              │
│          ├── cache in registry (absolute path key)       │
│          └── inject _source_path on instance            │
│                   │                                      │
│  Worker paths from worker_instances resolved same way    │
└──────────────────────────────────────────────────────────┘
```

### Class Detection via Introspection

The factory scans all classes in the loaded module and filters for subclasses of `AbstractWorker` or `AbstractDecisionLogic` (excluding the abstract base classes themselves).

- **Exactly 1 found** → used
- **0 found** → `ValueError`
- **2+ found** → `ValueError`

No filename-to-classname convention. Any class name works.

---

## Path Resolution

| Context | Relative base |
|---------|---------------|
| `decision_logic_type` in scenario JSON | Project root (cwd) |
| `worker_instances.*` in scenario JSON | Project root (cwd) |
| `get_required_workers()` return values | Directory of the decision logic file |
| Absolute paths | Always used as-is |

---

## External Algo Directories

Additional directories can be configured in `user_configs/app_config.json`:

```json
{
    "paths": {
        "user_algo_dirs": ["user_algos/", "/path/to/external/algos"]
    }
}
```

`user_algo_dirs` is walked by the config directory (#554, `config_directory_cli.py list`, `GET /api/v1/directory`) to discover the scenario sets and AutoTrader profiles in these directories. Workers and decision logics are still loaded from the explicit paths in those configs — no directory scanning.

The default value is `["user_algos/"]`.

---

## Hot-Reload / Rescan

Both factories expose `rescan()` for hot-reload in long-running processes (prepared for REPL integration):

```
REPL> reload                          ← user triggers manually
        │
        ▼
factory.rescan():
  1. Registry: keep CORE/* entries, remove all path-based entries
  2. sys.modules: delete all user_loaded.* entries
  3. Next run re-loads files from disk on demand
```

**Why manual?** Determinism. Code must not change mid-run. The user decides when to reload.

**`sys.modules` invalidation:** Path-loaded modules are cached under `user_loaded.worker.*` and `user_loaded.logic.*`. `rescan()` clears these so the next load reads the file fresh.

---

## Import Mechanics

External files are loaded via `importlib.util.spec_from_file_location()` — no `sys.path`
manipulation. Framework imports inside user files (e.g.,
`from python.framework.workers.abstract_worker import AbstractWorker`) resolve normally because
`/app` is in `sys.path` at runtime.

**File load errors** are caught and rethrown as `ValueError` with clear messages:
- `SyntaxError` → file has broken Python
- `ImportError` → missing dependency
- Wrong class count → 0 or 2+ AbstractXxx subclasses found

---

## Git Tracking Your Algo

`user_algos/` contents are gitignored. Track your strategies in a repository of your own,
independent of the main project — one at `user_algos/` (the layout the quickstart uses) or one per
algo directory, never both:

```bash
cd user_algos            # or user_algos/my_algo, for one repository per algo
git init
printf '__pycache__/\n*.pyc\n' > .gitignore
git add -A && git commit -m "initial algo commit"
```

The ignore file is not optional: Python writes bytecode beside your files on the first import, and
without it that bytecode makes the repository count as uncommitted code — a run from it is
recorded as running uncommitted code, and a real-money session refuses to start. Never both
layouts: a repository inside another shows there as one untracked directory, so code from the outer
one never counts as committed, and its patch cannot restore what lies in the inner one. Why a run
records your commit:
[A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true).
