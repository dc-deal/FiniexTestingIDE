# Algo Clock Validator Tests

**Suite:** `tests/framework/algo_clock_validator/` · **Mark:** `framework`, `unit`

Verifies the §9 **runtime startup validator** (#359): every decision logic and worker
actually loaded for a run — CORE *and* USER — is AST-scanned for direct wall-clock reads
(`datetime.now()`, `datetime.utcnow()`, `time.time()`) before the run starts. USER algos
live in `user_algos/` (gitignored) and never reach CI, so this runtime scan is the only
path that sees them.

Validator: `python/framework/validators/algo_clock_validator.py` — member of the algo
pre-flight check family (siblings: #354 state-snapshot serializability, #249 cert).

**Call-sites:**
- Simulation batch: `RequirementsCollector._algo_clock_preflight` — a violation excludes
  the scenario via `ValidationResult(is_valid=False)`, the batch continues (§33).
- AutoTrader: `autotrader_main` startup — a violation aborts the session (§35, STARTUP FAILED).

## Tests

| Test class | Checks |
|---|---|
| `TestFindWallClockCalls` | AST core: flags `datetime.now()` / `time.time()` in fixture algos, clean file passes, no false positives from comments/strings/docstrings |
| `TestCollectAlgoClockViolations` | Class-level collection: source resolution via `inspect`, same-file dedupe, builtin (no source) skipped |
| `TestValidateAlgoClock` | Raising wrapper: clean passes, violation raises `AlgoClockViolationError` with `file:line` + `get_current_time()` guidance |
| `TestCollectorClockPreflight` | Batch pre-flight: clean/dirty logic, dirty worker via `worker_instances`, no-logic no-op, unresolvable-type best-effort skip, cache per distinct algo set |

Fixture algos (loadable USER-style files) live in `tests/fixtures/algo_clock_validator/`.

## Run

```
pytest tests/framework/algo_clock_validator/ -v
```
Or the launch entry `🧩 Pytest: Algo Clock Validator (All)`.
