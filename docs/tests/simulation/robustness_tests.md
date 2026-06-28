# Robustness Validation Tests

`tests/simulation/robustness/` — multi-window + In-Sample/Out-of-Sample validation (#367),
sim-only. Unit-level, real types (`BatchExecutionSummary` / `ProcessResult` /
`ProcessTickLoopResult` / `PortfolioStats` / `SingleScenario`), synthetic per-window net-P&L —
no full pipeline run.

See the feature: [Robustness Validation guide](../../user_guides/robustness_validation_guide.md).

## test_robustness.py (32 tests)

### `TestAssignRoles` — the time-ordered split policy
| Test | Description |
|------|-------------|
| `test_split_10_at_30pct` | 10 windows, 0.3 → 7 IS + 3 OOS (trailing) |
| `test_always_one_each_for_two` | 2 windows → 1 IS + 1 OOS guaranteed |
| `test_single_window_is_in_sample` | a single window cannot split → In-Sample |
| `test_zero_windows` | empty input → empty roles |
| `test_trailing_order` | OOS is always the trailing fraction, never the lead |

### `TestToScenarioDict` — generator output cleanliness
| Test | Description |
|------|-------------|
| `test_no_empty_cascade_containers` | no `strategy_config` / `execution_config` / `trade_simulator_config` keys emitted |
| `test_role_omitted_when_none` | `role` absent when not assigned |
| `test_role_present_when_set` | `role` written when assigned |

### `TestParameterConstancy` — the fair-test guard
| Test | Description |
|------|-------------|
| `test_constant` | identical resolved `strategy_config` → constant |
| `test_drift_detected` | one differing param → flagged + drifting window named |
| `test_single_scenario_constant` | a single window is trivially constant |

### `TestRobustnessConfig` — the schema
| Test | Description |
|------|-------------|
| `test_defaults` | disabled, `expectancy`, 0.3 split, 3 min-windows |
| `test_rejects_unknown_key` | `extra='forbid'` rejects a typo |
| `test_metric_enum_coercion` | `'net_pnl'` string → enum |
| `test_rejects_invalid_metric` | an unknown metric is rejected |

### `TestBuildReport` — the DERIVE builder
| Test | Description |
|------|-------------|
| `test_disabled_is_empty` | robustness off → empty report |
| `test_distribution` | % profitable / mean / best / worst across windows |
| `test_in_out_of_sample_and_wfe_overfit` | IS/OOS means + WFE = OOS/IS (0.1 → overfit range) |
| `test_wfe_robust` | similar IS/OOS → WFE ≈ 0.9 |
| `test_wfe_undefined_when_is_not_profitable` | IS mean ≤ 0 → WFE None |
| `test_regime_breakdown` | per-regime mean + window count (Profile Runs) |
| `test_no_regime_breakdown_without_regimes` | manual/blocks sets → empty breakdown |
| `test_param_drift_flagged` | differing strategy across windows → `params_constant=False` |
| `test_disposition_copied` | block-splitting `agg_disposition_pct` copied as the trust-gate input |

### `TestPostRunVerdict` — the decision (validator)
| Test | Description |
|------|-------------|
| `test_disabled_emits_nothing` | robustness off → no advisory |
| `test_overfit_advisory` | WFE below `overfit_wfe_threshold` → OVERFIT warning |
| `test_robust_emits_no_overfit` | healthy WFE → no warning (ROBUST is good news) |
| `test_param_drift_advisory` | param drift → fair-test warning |
| `test_low_windows_advisory` | windows < `min_windows` → statistically-weak warning |
| `test_disposition_suppresses_verdict` | high distortion → verdict suppressed (only the trust caveat fires) |
| `test_insufficient_oos_bucket_suppresses_verdict` | overall ≥ `min_windows` but OOS bucket below it → verdict suppressed |

### `TestLoaderParsing` — config wiring
| Test | Description |
|------|-------------|
| `test_parses_robustness_block_and_roles` | top-level `robustness` block + per-scenario `role` parsed |
| `test_invalid_role_rejected` | an unknown `role` value raises |

Fixtures: `tests/fixtures/scenario_sets/robustness/` (`robustness_manual.json`,
`robustness_bad_role.json`).
