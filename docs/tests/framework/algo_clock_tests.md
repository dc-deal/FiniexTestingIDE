# Algo Clock Convention Tests

**Suite:** `tests/framework/algo_clock/` · **Mark:** `framework`, `unit`

Enforces the §9 wall-clock rule: decision logic and workers must read time only via
`DecisionTradingApi.get_current_time()` — never `datetime.now()`, `datetime.utcnow()`, or
`time.time()`. A direct wall-clock call breaks backtest reproducibility and decouples
timing from the tick cadence that gates async resolution.

## Tests

| Test | Checks |
|---|---|
| `test_no_wall_clock_in_decision_logic_or_workers` | AST-scans `python/framework/decision_logic/` + `python/framework/workers/` for forbidden wall-clock calls; fails with the offending `file:line` |

AST-based (not text grep) — no false positives from comments, strings, or docstrings.

## Scope

This is the **CI plane**: it covers the shipped CORE algo surface in the repository.
USER algos live in `user_algos/` (gitignored) and never reach CI — they are covered at
**runtime** by the startup validator (#359, `algo_clock_validator.py`), which scans every
algo actually loaded for the run (CORE + USER) before it starts. Both planes share the
same AST core (`find_wall_clock_calls`). See
[algo_clock_validator_tests.md](algo_clock_validator_tests.md).

## Run

```
pytest tests/framework/algo_clock/ -v
```
Or the launch entry `🧩 Pytest: Algo Clock Convention (All)`.
