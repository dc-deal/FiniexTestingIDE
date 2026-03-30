# Worker & Parameter Validation Tests Documentation

## Overview

The worker test suite validates the parameter validation system, schema integrity, default application, factory integration, and indicator computation across all workers and decision logics.

**Test Location:** `tests/worker_tests/`

**Components Covered:**
- 6 Workers: RsiWorker, EnvelopeWorker, MacdWorker, ObvWorker, HeavyRsiWorker, BacktestingSampleWorker
- 3 Decision Logics: SimpleConsensus, AggressiveTrend, BacktestingDeterministic

**Total Tests:** 231

---

## Test Files

### test_parameter_schema.py (~124 Tests)

Validates that every component's `get_parameter_schema()` returns well-formed, internally consistent `InputParamDef` declarations and that every worker's `get_output_schema()` returns valid `OutputParamDef` declarations. All schema tests are parametrized across all 9 components.

#### TestSchemaStructure (27 Tests)

| Test | Parametrized | Description |
|------|-------------|-------------|
| `test_schema_returns_dict` | ×9 | `get_parameter_schema()` returns a `dict` |
| `test_schema_values_are_parameter_defs` | ×9 | All values are `InputParamDef` instances |
| `test_schema_keys_are_strings` | ×9 | All keys are strings |

#### TestInputParamDefValidity (54 Tests)

| Test | Parametrized | Description |
|------|-------------|-------------|
| `test_param_types_are_supported` | ×9 | `param_type` is one of `int`, `float`, `bool`, `str`, `list` |
| `test_min_less_than_max` | ×9 | `min_val < max_val` when both are set |
| `test_defaults_within_bounds` | ×9 | Non-REQUIRED defaults fall within `[min_val, max_val]` |
| `test_defaults_match_declared_type` | ×9 | Default value matches declared `param_type` |
| `test_choices_contain_valid_values` | ×9 | All choices match declared `param_type` |
| `test_defaults_in_choices` | ×9 | Default value is in `choices` list (when choices defined) |

#### TestWorkerSpecificSchemas (5 Tests)

| Test | Description |
|------|-------------|
| `test_rsi_has_no_algorithm_params` | RSI schema is empty (periods handled by `validate_config()`) |
| `test_obv_has_no_algorithm_params` | OBV schema is empty (same pattern as RSI) |
| `test_envelope_has_deviation` | Envelope declares `deviation` with default 2.0, range 0.5–5.0 |
| `test_macd_has_three_required_periods` | MACD declares `fast_period`, `slow_period`, `signal_period` as REQUIRED |
| `test_heavy_rsi_has_artificial_load` | HeavyRSI declares `artificial_load_ms` with default 0 |

#### TestDecisionLogicSpecificSchemas (4 Tests)

| Test | Description |
|------|-------------|
| `test_simple_consensus_has_rsi_thresholds` | SimpleConsensus has `rsi_oversold`, `rsi_overbought` with defaults |
| `test_aggressive_trend_has_rsi_thresholds` | AggressiveTrend has `rsi_buy_threshold`, `rsi_sell_threshold` |
| `test_backtesting_deterministic_has_trade_sequence` | BacktestingDeterministic has `trade_sequence` parameter |
| `test_all_logics_have_lot_size` | All non-backtesting decision logics declare `lot_size` |

#### TestOutputSchemaStructure (30 Tests)

| Test | Parametrized | Description |
|------|-------------|-------------|
| `test_output_schema_returns_dict` | ×6 | `get_output_schema()` returns a `dict` |
| `test_output_schema_values_are_output_param_defs` | ×6 | All values are `OutputParamDef` instances |
| `test_output_schema_keys_are_strings` | ×6 | All keys are non-empty strings |
| `test_output_category_is_valid` | ×6 | Output category is `'SIGNAL'` or `'INFO'` |
| `test_output_min_less_than_max` | ×6 | `min_val < max_val` when both are set |

#### TestWorkerSpecificOutputSchemas (4 Tests)

| Test | Description |
|------|-------------|
| `test_rsi_output_schema` | RSI declares `rsi_value` as SIGNAL with 0–100 range |
| `test_envelope_output_schema` | Envelope declares `upper`, `lower`, `position` as SIGNAL |
| `test_macd_output_schema` | MACD declares `macd`, `signal`, `histogram` as SIGNAL with display |
| `test_obv_output_schema` | OBV declares `obv_value` as SIGNAL, `trend` with choices |

---

### test_parameter_validation.py (26 Tests)

Tests the `validate_parameters()` function that enforces schema constraints at runtime.

#### TestValidParameterConfigs (6 Tests)

| Test | Description |
|------|-------------|
| `test_all_params_provided` | Full config with all parameters passes validation |
| `test_only_required_provided` | Config with only REQUIRED parameters passes (optionals use defaults) |
| `test_int_accepted_for_float` | Integer value accepted where float is expected (type coercion) |
| `test_boundary_values_accepted` | Exact min/max boundary values pass validation |
| `test_valid_choice` | Value matching a declared choice passes |
| `test_empty_schema_always_passes` | Components with empty schema accept any config |

#### TestMissingRequired (2 Tests)

| Test | Description |
|------|-------------|
| `test_missing_required_raises` | Missing REQUIRED parameter raises `ParameterValidationError` in strict mode |
| `test_missing_required_raises_even_non_strict` | Missing REQUIRED parameter raises even in non-strict mode (always fatal) |

#### TestTypeErrors (6 Tests)

| Test | Description |
|------|-------------|
| `test_string_for_int_raises` | String where int expected raises (always fatal) |
| `test_string_for_int_raises_non_strict` | Type errors are fatal even in non-strict mode |
| `test_float_for_int_raises` | Float where int expected raises (no implicit truncation) |
| `test_string_for_float_raises` | String where float expected raises |
| `test_int_for_bool_raises` | Integer where bool expected raises (no truthy coercion) |
| `test_string_for_bool_raises` | String where bool expected raises |

#### TestBoundaryStrict (5 Tests)

| Test | Description |
|------|-------------|
| `test_below_min_raises` | Value below `min_val` raises in strict mode |
| `test_above_max_raises` | Value above `max_val` raises in strict mode |
| `test_float_below_min_raises` | Float below minimum raises |
| `test_float_above_max_raises` | Float above maximum raises |
| `test_the_envelope_bug` | Regression test: `deviation=0.02` (below min 0.5) is caught |

#### TestBoundaryNonStrict (3 Tests)

| Test | Description |
|------|-------------|
| `test_below_min_warns` | Value below `min_val` warns (not raises) in non-strict mode |
| `test_above_max_warns` | Value above `max_val` warns in non-strict mode |
| `test_multiple_violations_all_warned` | Multiple boundary violations each produce separate warnings |

#### TestChoicesValidation (2 Tests)

| Test | Description |
|------|-------------|
| `test_invalid_choice_strict_raises` | Value not in `choices` raises in strict mode |
| `test_invalid_choice_non_strict_warns` | Invalid choice warns in non-strict mode |

#### TestContextName (2 Tests)

| Test | Description |
|------|-------------|
| `test_context_name_in_missing_required` | Error message includes component name for debugging |
| `test_context_name_in_type_error` | Type error message includes component name |

---

### test_worker_defaults.py (22 Tests)

Tests the `apply_defaults()` function that fills missing optional parameters from schema defaults.

#### TestApplyDefaultsCore (7 Tests)

| Test | Description |
|------|-------------|
| `test_missing_optionals_filled` | Missing optional parameters receive schema defaults |
| `test_existing_values_preserved` | Explicitly provided values are never overwritten |
| `test_required_params_not_injected` | REQUIRED parameters are not injected (must be user-provided) |
| `test_original_config_unchanged` | Input dict is not mutated (returns new copy) |
| `test_extra_keys_preserved` | Keys not in schema (e.g., `periods`) pass through unchanged |
| `test_empty_config_gets_all_defaults` | Empty config receives all optional defaults |
| `test_empty_schema_returns_copy` | Components with empty schema return input copy |

#### TestRealWorkerDefaults (15 Tests)

| Test | Parametrized | Description |
|------|-------------|-------------|
| `test_envelope_default_deviation` | — | Envelope gets `deviation=2.0` when not provided |
| `test_heavy_rsi_default_load` | — | HeavyRSI gets `artificial_load_ms=0` when not provided |
| `test_macd_no_defaults_for_required` | — | MACD gets no defaults (all params REQUIRED) |
| `test_simple_consensus_all_defaults` | — | SimpleConsensus fills all 10 parameters from defaults |
| `test_aggressive_trend_all_defaults` | — | AggressiveTrend fills all 6 parameters from defaults |
| `test_backtesting_sample_worker_default` | — | BacktestingSampleWorker fills `computation_weight` |
| `test_defaults_produce_valid_config` | ×9 | Defaults-only config passes `validate_parameters()` |

---

### test_factory_integration.py (21 Tests)

Tests end-to-end factory workflows: config → validation → instantiation for both WorkerFactory and DecisionLogicFactory.

#### TestWorkerFactoryValidConfigs (6 Tests)

| Test | Description |
|------|-------------|
| `test_create_rsi_worker` | RSI worker created with valid periods config |
| `test_create_envelope_worker` | Envelope worker created with explicit deviation |
| `test_create_envelope_worker_default_deviation` | Envelope created without deviation (default 2.0 applied) |
| `test_create_macd_worker` | MACD worker created with all three required periods |
| `test_create_heavy_rsi_worker` | HeavyRSI worker created with artificial load parameter |
| `test_create_obv_worker` | OBV worker created with valid periods config |

#### TestWorkerFactoryMissingRequired (2 Tests)

| Test | Description |
|------|-------------|
| `test_macd_missing_fast_period` | Factory rejects MACD config missing `fast_period` |
| `test_macd_missing_all_required` | Factory rejects MACD config missing all required params |

#### TestWorkerFactoryBoundaryStrict (4 Tests)

| Test | Description |
|------|-------------|
| `test_envelope_deviation_too_low` | Factory rejects `deviation=0.1` (below min 0.5) |
| `test_envelope_deviation_too_high` | Factory rejects `deviation=10.0` (above max 5.0) |
| `test_macd_fast_period_zero` | Factory rejects `fast_period=0` (below min 1) |
| `test_heavy_rsi_negative_load` | Factory rejects `artificial_load_ms=-5` (below min 0) |

#### TestWorkerFactoryBoundaryNonStrict (1 Test)

| Test | Description |
|------|-------------|
| `test_envelope_deviation_too_low_warns` | Non-strict mode warns but creates worker with out-of-range deviation |

#### TestDecisionLogicFactoryValidConfigs (4 Tests)

| Test | Description |
|------|-------------|
| `test_create_simple_consensus` | SimpleConsensus created with explicit config values |
| `test_create_aggressive_trend` | AggressiveTrend created with explicit thresholds |
| `test_create_simple_consensus_defaults_only` | SimpleConsensus created with empty config (all defaults) |
| `test_create_backtesting_deterministic` | BacktestingDeterministic created with trade sequence |

#### TestDecisionLogicFactoryBoundaryStrict (3 Tests)

| Test | Description |
|------|-------------|
| `test_consensus_rsi_oversold_too_high` | Factory rejects `rsi_oversold=101` (above max 100) |
| `test_consensus_lot_size_zero` | Factory rejects `lot_size=0` (below min 0.01) |
| `test_consensus_min_confidence_above_one` | Factory rejects `min_confidence=1.5` (above max 1.0) |

#### TestDecisionLogicFactoryBoundaryNonStrict (1 Test)

| Test | Description |
|------|-------------|
| `test_consensus_oversold_too_high_warns` | Non-strict mode warns but creates logic with out-of-range value |

---

### worker_computation_tests/ (38 Tests)

Unit tests for indicator computation logic. Each test creates a worker with known input data and validates mathematical correctness.

---

#### test_rsi_computation.py (8 Tests)

##### TestRSIBasicComputation (4 Tests)

| Test | Description |
|------|-------------|
| `test_rsi_known_values` | RSI computed from known price series matches expected value |
| `test_rsi_all_gains` | Monotonically rising prices produce RSI = 100 |
| `test_rsi_all_losses` | Monotonically falling prices produce RSI = 0 |
| `test_rsi_equal_gains_losses` | Equal gains and losses produce RSI ≈ 50 |

##### TestRSIOutputFields (2 Tests)

| Test | Description |
|------|-------------|
| `test_rsi_output_avg_gain_loss` | `avg_gain` and `avg_loss` via `get_signal()` match hand calculation |
| `test_rsi_output_bars_used` | `bars_used` output matches number of close prices used |

##### TestRSIBoundaryAndRange (2 Tests)

| Test | Description |
|------|-------------|
| `test_rsi_always_between_0_and_100` | RSI value is always in [0, 100] range |
| `test_rsi_with_large_period` | RSI works correctly with large period (14) |

---

#### test_envelope_computation.py (9 Tests)

##### TestEnvelopeBasicComputation (3 Tests)

| Test | Description |
|------|-------------|
| `test_envelope_bands_default_deviation` | Bands computed with default deviation (2.0) |
| `test_envelope_bands_custom_deviation` | Bands computed with custom deviation value |
| `test_envelope_output_keys` | Result outputs dict contains `upper`, `middle`, `lower`, `position`, `std_dev`, `bars_used` |

##### TestEnvelopePosition (3 Tests)

| Test | Description |
|------|-------------|
| `test_position_at_middle` | Price at middle produces position ≈ 0.5 |
| `test_position_above_upper_clamped` | Price above upper band produces position clamped to 1.0 |
| `test_position_below_lower_clamped` | Price below lower band produces position clamped to 0.0 |

##### TestEnvelopeOutputFields (1 Test)

| Test | Description |
|------|-------------|
| `test_envelope_std_dev_output` | `std_dev` via `get_signal()` matches hand-calculated population std dev |

##### TestEnvelopeRegression (2 Tests)

| Test | Description |
|------|-------------|
| `test_band_width_sanity_check` | Band width matches expected value, regression guard against deviation bug |
| `test_constant_prices_zero_std` | Constant prices produce zero-width bands (upper = lower = middle) |

---

#### test_macd_computation.py (11 Tests)

##### TestEMACalculation (4 Tests)

| Test | Description |
|------|-------------|
| `test_ema_exact_period_returns_sma` | EMA with exactly N bars returns SMA |
| `test_ema_less_than_period_returns_mean` | EMA with fewer bars returns simple mean |
| `test_ema_iterative_calculation` | EMA iteratively matches manual calculation |
| `test_ema_period_5` | EMA with period 5 matches known reference values |

##### TestMACDStructure (4 Tests)

| Test | Description |
|------|-------------|
| `test_macd_returns_worker_result` | Compute returns a `WorkerResult` |
| `test_macd_output_keys` | Outputs dict contains `macd`, `signal`, `histogram`, `fast_ema`, `slow_ema`, `bars_used` |
| `test_macd_values_are_float` | All MACD output values are Python floats |
| `test_macd_bars_used_output` | `bars_used` output matches input bar count |

##### TestMACDDirection (3 Tests)

| Test | Description |
|------|-------------|
| `test_macd_rising_prices_positive` | Rising prices produce positive MACD line |
| `test_macd_falling_prices_negative` | Falling prices produce negative MACD line |
| `test_macd_histogram_equals_macd_minus_signal` | Histogram = MACD line − Signal line |

---

#### test_obv_computation.py (10 Tests)

##### TestOBVBasicComputation (4 Tests)

| Test | Description |
|------|-------------|
| `test_obv_mixed_direction` | OBV correctly accumulates volume on mixed price movements |
| `test_obv_all_up` | All rising prices accumulate positive volume |
| `test_obv_all_down` | All falling prices accumulate negative volume |
| `test_obv_flat_price` | Unchanged prices do not add volume |

##### TestOBVEdgeCases (3 Tests)

| Test | Description |
|------|-------------|
| `test_obv_insufficient_bars` | Returns neutral result with < 2 bars |
| `test_obv_zero_volume` | Zero volume bars produce OBV = 0 |
| `test_obv_exactly_two_bars_up` | Minimum case: 2 bars with rising price |

##### TestOBVOutputFields (3 Tests)

| Test | Description |
|------|-------------|
| `test_obv_output_fields` | Outputs contain `obv_value`, `bars_used`, `total_volume`, `trend`, `has_volume` |
| `test_obv_has_volume_false_when_zero` | `has_volume=False` when all volumes are zero (Forex) |
| `test_obv_forex_warning` | Forex `TradingContext` triggers volume warning in logger |

---

## Architecture Notes

### Test Design Philosophy

The worker test suite uses a **layered validation** approach:

1. **Schema layer** (`test_parameter_schema.py`): Every component's `InputParamDef` declarations are internally consistent — types match, bounds are valid, defaults are within range. Worker `OutputParamDef` declarations are validated for structure, categories, and bounds.

2. **Validation layer** (`test_parameter_validation.py`): The `validate_parameters()` function correctly enforces all constraints — missing required, type mismatches, boundary violations, strict vs non-strict modes.

3. **Defaults layer** (`test_worker_defaults.py`): The `apply_defaults()` function correctly fills optional parameters without overwriting explicit values or injecting REQUIRED placeholders.

4. **Factory layer** (`test_factory_integration.py`): End-to-end creation through `WorkerFactory` and `DecisionLogicFactory` — valid configs produce working instances, invalid configs are rejected with clear errors.

5. **Computation layer** (`worker_computation_tests/`): Indicator math is correct — known inputs produce known outputs, edge cases are handled, output fields are populated correctly.

### Key Data Flow

```
InputParamDef (input schema)    OutputParamDef (output schema)
  └→ validate_parameters()         └→ get_output_schema()
       └→ apply_defaults()              └→ WorkerResult(outputs={...})
            └→ WorkerFactory                  └→ result.get_signal('key')
                 └→ Worker.compute()
```

### Parametrized Components

All 9 components tested across schema and defaults tests:

| Component | Type | Parameters |
|-----------|------|------------|
| RsiWorker | Worker | (no algorithm params) |
| EnvelopeWorker | Worker | `deviation` |
| MacdWorker | Worker | `fast_period`, `slow_period`, `signal_period` |
| ObvWorker | Worker | (no algorithm params) |
| HeavyRsiWorker | Worker | `artificial_load_ms` |
| BacktestingSampleWorker | Worker | `computation_weight` |
| SimpleConsensus | DecisionLogic | 10 parameters (RSI thresholds, envelope thresholds, lot_size, etc.) |
| AggressiveTrend | DecisionLogic | 6 parameters (RSI thresholds, lot_size, etc.) |
| BacktestingDeterministic | DecisionLogic | `trade_sequence`, `lot_size` |
