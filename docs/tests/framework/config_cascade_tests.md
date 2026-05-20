# Config Cascade Tests

Black-box coverage of the 3-level scenario-set configuration cascade. Drives
`ScenarioConfigLoader.load_config()` against JSON fixtures and asserts the
merged result on the produced `SingleScenario`.

| Item | Value |
|---|---|
| Suite path | [tests/framework/config/](../../../tests/framework/config/) |
| Fixtures | [tests/fixtures/scenario_sets/cascade/](../../../tests/fixtures/scenario_sets/cascade/) |
| Pytest mark | `framework` (auto-applied via path) |
| Companion doc | [config_cascade_guide.md](../../config_cascade_guide.md) — cascade architecture |
| Tracking layers doc | [performance_tracking_layers.md](../../architecture/performance_tracking_layers.md) — context for the sub-group case |

---

## Why Black-Box

The cascade implementation involves several private helpers
(`deep_merge`, `check_unknown_keys`, `validate_merged_config`, `ScenarioCascade.merge_*`),
each of which is internal. Asserting against the **loader's public output** keeps
tests stable across refactors: if any internal helper changes shape, the assertions
still hold as long as the merged result is correct.

## Suite Coverage (execution_config lane)

| Test | What it verifies |
|---|---|
| `test_app_defaults_apply_when_no_overrides` | Level 1 baseline — app_config defaults reach the scenario when neither global nor scenario override |
| `test_global_overrides_app_defaults` | Level 2 — scenario-set `global.execution_config` overrides app defaults per-key, untouched keys stay inherited |
| `test_scenario_overrides_global_and_app` | Level 3 — `scenarios[i].execution_config` overrides global which already overrode app — 3 levels deep |
| `test_sub_group_per_key_merge` | #137 — nested `performance_tracking` sub-group merges per-key: scenario overrides `worker_decision_tracking`, `tick_loop_profiling` is inherited from global |
| `test_unknown_key_hard_fails_with_provenance` | Safety net — typo in `global.execution_config` raises `ValueError` with full provenance string, before any merge happens |

## Fixtures

Each fixture is a self-contained scenario-set JSON with one scenario. The
scenario carries the minimum required fields (`name`, `symbol`, `data_broker_type`,
date range) but no real tick data — the loader only merges, it does not load market
data. This keeps fixtures small and review-friendly.

| Fixture | Purpose |
|---|---|
| `no_overrides.json` | Empty `global.execution_config` and empty `scenarios[0].execution_config` |
| `global_overrides_app.json` | `global` sets `parallel_workers` + `tick_processing_budget_ms` |
| `scenario_overrides_global.json` | `global` + scenario both set values; scenario must win for its keys |
| `sub_group_per_key_merge.json` | `global.performance_tracking` sets both switches, scenario overrides one |
| `unknown_key_typo.json` | `global.execution_config.parallel_workerz` (typo) |

## Scope Boundaries — Other Lanes

This suite covers only the **`execution_config` lane**. The cascade applies to four
additional lanes with the same pattern:

| Lane | Levels | Special |
|---|---|---|
| `trade_simulator_config` | 3 (app → global → scenario) | atomic `balances` key (replace, not merge) |
| `order_guard` | 2 (global → scenario) | per-key merge |
| `stress_test_config` | 2 (global → scenario) | per-key, nested injection settings |
| `strategy_config.workers` | 2 (global → scenario) | per-worker-instance + per-parameter merge |

These lanes are equally critical but not exercised here. A follow-up suite
(planned, not yet scheduled) extends the same fixture/black-box approach to them.

## When to Touch This Suite

- **Cascade behavior changes** (`deep_merge`, `validate_merged_config`, the loader merge logic) — re-run, expect green
- **New `execution_config` keys added** — extend `test_app_defaults_apply_when_no_overrides` with an assertion for the new default
- **New nested sub-groups inside `execution_config`** — add a fixture and test analogous to `sub_group_per_key_merge`
- **Cascade extended to a fourth level** — fundamental redesign, the test approach generalizes but assertions need rework

If you change the cascade and these tests stay green, the merge mechanic is intact.
If they fail, the failure message points at the exact level + key that broke.
