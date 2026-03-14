# USER Modules & Hot-Reload Mechanics

## Overview

The USER namespace allows custom workers and decision logics without modifying framework code. Files placed in the USER directories are **auto-discovered at startup** — no factory registration needed.

> For Worker vs DecisionLogic concepts (what they do, how they interact), see [quickstart_guide.md](quickstart_guide.md).
> For naming conventions and namespace details, see [worker_naming_doc.md](worker_naming_doc.md).

---

## Directory Structure

```
python/
├── workers/
│   └── user/                          ← USER workers (default, always scanned)
│       ├── TEMPLATE_worker.py         ← Copy-paste starting point (skipped by scanner)
│       ├── envelope_modified.py       ← Example: modified USER envelope worker
│       └── .gitignore
├── decision_logic/
│   └── user/                          ← USER decision logics (default, always scanned)
│       ├── TEMPLATE_logic.py          ← Copy-paste starting point (skipped by scanner)
│       ├── aggressive_trend_modified.py ← Example: modified USER decision logic
│       └── .gitignore
└── framework/
    ├── workers/core/                  ← CORE workers (read-only, framework code)
    ├── decision_logic/core/           ← CORE decision logics (read-only)
    └── factory/
        ├── worker_factory.py          ← Scan + registry for workers
        └── decision_logic_factory.py  ← Scan + registry for decision logics
```

**Default paths are ALWAYS scanned** — `python/workers/user/` and `python/decision_logic/user/` are built-in. Additional external directories can be configured (see below).

---

## Auto-Discovery Flow

```
┌─────────────────────────────────────────────────────────┐
│                    STARTUP (factory init)                │
│                                                         │
│  1. Factory.__init__()                                  │
│  2. _load_core_workers() / _load_core_logics()          │
│     → Hardcoded CORE/ modules registered                │
│  3. _scan_user_namespace()                              │
│     ├── python/workers/user/*.py                        │
│     │   ├── TEMPLATE_worker.py  → Skip (TEMPLATE_)      │
│     │   ├── envelope_modified.py → OK → USER/envelope_modified │
│     │   └── broken_file.py      → SyntaxError → Skip+Log│
│     ├── external_dir_1/*.py (if configured)             │
│     └── external_dir_2/*.py (if configured)             │
│  4. Registry complete, runs can start                   │
│                                                         │
│  Registry: { "CORE/rsi": RSIWorker,                     │
│              "CORE/envelope": EnvelopeWorker,            │
│              "USER/envelope_modified": EnvelopeModifiedWorker, │
│              ... }                                       │
└─────────────────────────────────────────────────────────┘
```

### What Gets Scanned
- All `*.py` files in scan directories
- Skipped: files starting with `TEMPLATE_` or `__`

### What Gets Registered
- Classes that inherit from `AbstractWorker` (workers) or `AbstractDecisionLogic` (decision logics)
- Registered as `USER/{filename_without_extension}`

### What Gets Skipped (with warning log)
- `SyntaxError` — broken Python file
- `ImportError` — missing dependency
- **Class name mismatch** — expected class not found; warning shows expected vs. found names and links to docs
- Wrong base class — class doesn't inherit from the expected abstract

---

## Class Naming Convention

This is a **global project pattern** — all CORE and USER modules follow the same convention. The filename determines the expected class name. See [worker_naming_doc.md](worker_naming_doc.md) for the full reference.

The scanner derives the expected class name from the filename:

| File | Expected Class | Rule |
|------|---------------|------|
| `my_custom_rsi.py` | `MyCustomRsiWorker` | PascalCase + "Worker" suffix |
| `envelope_modified.py` | `EnvelopeModifiedWorker` | PascalCase + "Worker" suffix |
| `my_strategy.py` | `MyStrategy` | PascalCase, **no suffix** (decision logic) |
| `aggressive_trend_modified.py` | `AggressiveTrendModified` | PascalCase, no suffix |

**Workers** always get the "Worker" suffix appended (unless already present).
**Decision Logics** use plain PascalCase — no suffix.

---

## External Directories

Keep strategies in a separate repo by configuring additional scan paths in `app_config.json`:

```json
{
    "paths": {
        "user_worker_dirs": ["/home/user/my-strategies/workers"],
        "user_decision_logic_dirs": ["/home/user/my-strategies/decision_logic"]
    }
}
```

- Default paths (`python/workers/user/`, `python/decision_logic/user/`) are **always scanned regardless** of this setting
- The arrays only add **additional** directories
- Empty arrays (default) = only default paths scanned
- External directories that don't exist → warning log, no crash
- Name collision between default and external → external wins, warning logged

**Import mechanism for external dirs:** Uses `importlib.util.spec_from_file_location()` — no `sys.path` pollution.

---

## Config Example

Mixing CORE and USER modules in a single scenario:

```json
{
    "strategy_config": {
        "decision_logic_type": "USER/aggressive_trend_modified",
        "worker_instances": {
            "rsi_fast": "CORE/rsi",
            "envelope_main": "USER/envelope_modified"
        },
        "workers": {
            "rsi_fast": { "periods": { "M5": 14 } },
            "envelope_main": { "periods": { "M30": 20 }, "deviation": 2.0 }
        },
        "decision_logic_config": {
            "rsi_buy_threshold": 35,
            "rsi_sell_threshold": 65,
            "lot_size": 0.1
        }
    }
}
```

Parameters are changed between runs via config — no code changes needed. This is the core principle of the parameter-centric IDE.

---

## Hot-Reload / Rescan Mechanics

Both factories expose a `rescan()` method for hot-reload in long-running processes (prepared for Issue #21 REPL shell — not yet connected to a command, but the mechanism is ready).

```
┌─────────────────────────────────────────────────────────┐
│              LONG-RUNNING PROCESS (REPL / Server)       │
│                                                         │
│  User edits my_worker.py on disk                        │
│        │                                                │
│        ▼                                                │
│  REPL> reload         ← User triggers manually          │
│        │                                                │
│        ▼                                                │
│  factory.rescan():                                      │
│    1. Registry: remove all USER/* entries                │
│    2. sys.modules: delete all user module caches         │
│       ├── python.workers.user.*                         │
│       └── user_ext.workers.* (external)                 │
│    3. _scan_user_namespace() re-runs                    │
│       ├── my_worker.py (changed) → re-imported ✓        │
│       ├── new_file.py            → discovered ✓         │
│       └── broken.py              → SyntaxError → Log    │
│    4. Registry updated                                  │
│        │                                                │
│        ▼                                                │
│  REPL> run config.json  ← uses new version              │
└─────────────────────────────────────────────────────────┘
```

### Why Manual Reload (Not Automatic)?

**Determinism.** During a run, code must not change. The user explicitly decides when to reload modules. This aligns with the project's core principle: execution is deterministic and reproducible.

### sys.modules Invalidation

Python caches imported modules in `sys.modules`. Without clearing these entries, `importlib.import_module()` returns the old cached version even after the file changed on disk.

The `rescan()` method clears **all** user module entries — including helper modules imported by user workers. This ensures the entire dependency chain is re-imported fresh:

```python
# If user/my_worker.py imports user/my_helpers.py:
# Both entries are cleared from sys.modules during rescan.
# When my_worker.py is re-imported, it triggers a fresh import of my_helpers.py too.
```

### Compile Error Detection

During scan/rescan, every file import is wrapped in `try/except`. Syntax errors and import errors are caught per-file — the framework continues with all other valid modules:

```
WARNING: Skipping USER/broken_file: SyntaxError: invalid syntax (broken_file.py, line 42)
WARNING: Skipping USER/bad_import: ImportError: No module named 'nonexistent'
```

A broken file never crashes the framework. Fix the file, rescan, and it will be picked up.

---

## Performance

| Operation | Time |
|-----------|------|
| Directory listing (`Path.glob()`) | ~0.1-0.5ms |
| Module import (`importlib`) per file | ~1-5ms |
| 10 USER modules total | ~15-50ms |
| `rescan()` (clear + re-import) | Same as initial scan |

Scan time is negligible — not noticeable during startup or reload.

---

## Design Decisions

1. **No CORE override** — `USER/rsi` and `CORE/rsi` are separate registry keys. No implicit override. The user explicitly chooses which namespace to reference in config.

2. **No shared scan utility** — WorkerFactory and DecisionLogicFactory each have their own `_scan_user_namespace()`. The differences (base class check, naming convention with/without "Worker" suffix) make a shared abstraction premature for just 2 callers.

3. **Manual reload only** — No filesystem watcher. `rescan()` is explicit. Code doesn't change mid-run. Determinism preserved.

4. **On-demand fallback preserved** — If a config references a `USER/` module that wasn't found during scan (e.g., added after factory init without rescan), the existing `_load_custom_worker()` / `_load_custom_logic()` fallback still attempts to load it dynamically.

5. **Default paths always implicit** — `user_worker_dirs` / `user_decision_logic_dirs` in `app_config.json` only lists ADDITIONAL directories. Default `python/workers/user/` and `python/decision_logic/user/` are always scanned regardless.

6. **External imports via spec_from_file_location** — External directories use `importlib.util.spec_from_file_location()` instead of manipulating `sys.path`. This avoids polluting the import namespace and prevents accidental cross-directory imports.

---

## Template Files

Two template files are provided as copy-paste starting points:

- `python/workers/user/TEMPLATE_worker.py` — Minimal `AbstractWorker` subclass with all abstract methods stubbed
- `python/decision_logic/user/TEMPLATE_logic.py` — Minimal `AbstractDecisionLogic` subclass with all abstract methods stubbed

Templates are **skipped by the scanner** (TEMPLATE_ prefix) and are version-tracked.

**Usage:**
```bash
cp python/workers/user/TEMPLATE_worker.py python/workers/user/my_indicator.py
# Edit my_indicator.py: rename class, implement compute()
# Reference in config: "USER/my_indicator"
```
