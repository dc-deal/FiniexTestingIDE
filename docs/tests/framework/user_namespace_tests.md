# Test Suite: Path-Based Loading

**Location:** `tests/framework/user_namespace/`
**Run:** `pytest tests/framework/user_namespace/ -v`
**Dependencies:** None (mock-based, no data, no tick loop)

## Purpose

Validates path-based worker and decision logic loading: on-demand file loading, introspection-based class detection, CORE namespace integrity, rescan/hot-reload, error handling, and WorkerOrchestrator worker-ref normalization.

## Test Classes

### TestPathWorkerLoading (8 tests)
- Worker loaded by absolute path — class found via introspection
- Worker loaded by relative path with explicit base_path
- Missing file → ValueError with clear message
- Syntax error in file → ValueError
- File with zero AbstractWorker subclasses → ValueError
- File with two AbstractWorker subclasses → ValueError
- File with one worker + helper class → loads correctly (helper ignored)
- Second call with same path returns cached result

### TestPathDecisionLogicLoading (5 tests)
- Decision logic loaded by absolute path
- File with zero AbstractDecisionLogic subclasses → ValueError
- Missing file → ValueError
- `create_logic()` injects `_source_path` on the returned instance
- CORE decision logic has `_source_path = None`

### TestCoreRegistration (4 tests)
- All CORE workers present after factory init
- All CORE decision logics present after factory init
- Unknown `CORE/` worker → ValueError (not treated as path)
- Unknown `CORE/` logic → ValueError

### TestRescan (3 tests)
- `rescan()` removes path-loaded workers, keeps CORE entries
- `rescan()` clears `user_loaded.*` from `sys.modules`
- DecisionLogicFactory `rescan()` works the same way

### TestWorkerOrchestratorNormalization (5 tests)
- `CORE/rsi` ref returned unchanged
- Absolute path returned unchanged
- Relative ref with base_path resolves correctly
- Relative ref without base_path resolves against cwd
- DL-relative ref and config project-root ref normalize to same absolute path

### TestUserAlgoIntegration (2 tests, skip if `user_algos/` is empty)
- First decision logic found in `user_algos/` loads and yields one `AbstractDecisionLogic` subclass
- `get_required_worker_instances()` returns a dict of `str → str` (path or `CORE/` reference)

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `mock_logger` | session | MagicMock logger for factory instantiation |

## Helpers

| Helper | Description |
|--------|-------------|
| `write_module(dir, filename, code)` | Write a Python module to a tmp directory |
| `cleanup_user_loaded()` | Remove `user_loaded.*` from `sys.modules` |

## Test Module Templates (conftest.py)

- `VALID_WORKER_CODE` — parameterized valid AbstractWorker subclass
- `VALID_LOGIC_CODE` — parameterized valid AbstractDecisionLogic subclass
- `NOT_A_WORKER_CODE` — class without any correct base class
- `SYNTAX_ERROR_CODE` — intentionally broken Python
- `IMPORT_ERROR_CODE` — imports a nonexistent module
