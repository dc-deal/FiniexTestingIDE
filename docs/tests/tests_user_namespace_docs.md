# Test Suite: USER Namespace Discovery

**Location:** `tests/user_namespace/`
**Run:** `pytest tests/user_namespace/ -v`
**Dependencies:** None (mock-based, no data, no tick loop)

## Purpose

Validates the USER namespace auto-discovery system: startup scan, hot-reload/rescan, error handling, naming conventions, external directories, and integration with real USER modules.

## Test Classes

### TestWorkerScan (8 tests)
- Empty directory scanned without crash
- Valid worker file discovered and registered as `USER/{stem}`
- `TEMPLATE_` prefixed files skipped by scanner
- Syntax errors → file skipped, warning logged
- Import errors → file skipped, warning logged
- Wrong base class (not AbstractWorker) → skipped
- Multiple workers in one directory all discovered
- Class naming convention: `my_custom_rsi.py` → `MyCustomRsiWorker`

### TestDecisionLogicScan (2 tests)
- Valid decision logic discovered and registered
- Naming convention: `my_strategy.py` → `MyStrategy` (no suffix)

### TestExternalDirectories (3 tests)
- Worker from external directory registered as `USER/`
- Non-existent external directory → no crash
- Name collision between directories → last wins, warning logged

### TestRescan (4 tests)
- `rescan()` removes stale USER entries from registry
- `rescan()` discovers newly added files
- `rescan()` clears `python.workers.user.*` from `sys.modules`
- DecisionLogicFactory `rescan()` works the same way

### TestOnDemandFallback (2 tests)
- USER worker loads via on-demand fallback
- USER decision logic loads via on-demand fallback

### TestRealUserModules (5 tests)
- `python/workers/user/envelope_modified.py` auto-discovered as `USER/envelope_modified`
- `python/decision_logic/user/aggressive_trend_modified.py` auto-discovered as `USER/aggressive_trend_modified`
- TEMPLATE files not registered in any factory
- CORE workers still present after USER scan
- CORE decision logics still present after USER scan

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `mock_logger` | session | MagicMock logger for factory instantiation |

## Helpers

| Helper | Description |
|--------|-------------|
| `write_module(dir, filename, code)` | Write a Python module to a tmp directory |
| `_scan_dir(factory, dir, is_worker)` | Manually scan a directory via `spec_from_file_location` |
| `get_user_entries(factory)` | Filter registry for `USER/` entries only |
| `cleanup_test_modules()` | Remove `test_user.*` from `sys.modules` |

## Test Module Templates

Pre-defined source code strings in `conftest.py`:
- `VALID_WORKER_CODE` — parameterized valid AbstractWorker subclass
- `VALID_LOGIC_CODE` — parameterized valid AbstractDecisionLogic subclass
- `NOT_A_WORKER_CODE` — class without correct base class
- `SYNTAX_ERROR_CODE` — intentionally broken Python
- `IMPORT_ERROR_CODE` — imports a nonexistent module
