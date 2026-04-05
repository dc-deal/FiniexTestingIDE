# User Algo Workspace & Loading Mechanics

## Overview

The `user_algos/` directory is the canonical workspace for user-authored algorithms. Each strategy lives in its own subdirectory with all related files co-located — worker, decision logic, and scenario config(s).

> For Worker vs DecisionLogic concepts, see [quickstart_guide.md](quickstart_guide.md).
> For the reference system and contract model, see [worker_naming_doc.md](worker_naming_doc.md).

---

## Directory Structure

```
user_algos/                      ← user algo workspace (gitignored by default)
└── my_algo/                     ← one directory per strategy
    ├── my_strategy.py           ← decision logic
    ├── my_range_worker.py       ← worker
    └── my_algo_eurusd.json      ← scenario config

python/framework/
├── workers/core/                       ← CORE workers (read-only, framework)
├── decision_logic/core/                ← CORE decision logics (read-only)
└── factory/
    ├── worker_factory.py               ← path-based loader for workers
    └── decision_logic_factory.py       ← path-based loader for decision logics
```

`user_algos/` is tracked as an empty stub via `.gitkeep`. Contents are gitignored so each user can independently manage their own algo files (or create a nested git repo for their strategies).

---

## On-Demand Loading Flow

Workers and decision logics are **not pre-scanned at startup**. They are loaded on-demand when their path appears in a scenario config.

```
┌──────────────────────────────────────────────────────────┐
│                 SCENARIO RUN                              │
│                                                          │
│  Config: "decision_logic_type":                          │
│    "user_algos/my_algo/my_strategy.py"                   │
│                   │                                      │
│                   ▼                                      │
│  DecisionLogicFactory._resolve_logic_class()             │
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
| `get_required_worker_instances()` return values | Directory of the decision logic file |
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

`user_algo_dirs` is used by `ScenarioSetFinder` to discover scenario configs in these directories. Workers and decision logics are still loaded from the explicit paths in those configs — no directory scanning.

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

External files are loaded via `importlib.util.spec_from_file_location()` — no `sys.path` manipulation. Framework imports inside user files (e.g., `from python.framework.workers.abstract_worker import AbstractWorker`) resolve normally because `/app` is in `sys.path` at runtime.

**File load errors** are caught and rethrown as `ValueError` with clear messages:
- `SyntaxError` → file has broken Python
- `ImportError` → missing dependency
- Wrong class count → 0 or 2+ AbstractXxx subclasses found

---

## Git Tracking Your Algo

`user_algos/` contents are gitignored. To track your strategy:

```bash
cd user_algos/my_algo
git init
git add .
git commit -m "initial algo commit"
```

Each algo subdirectory can be its own git repo, independent of the main project.
