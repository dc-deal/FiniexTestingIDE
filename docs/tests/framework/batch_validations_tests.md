# Batch Validations Test Suite

## Purpose

Unit tests for Phase 0 batch pipeline validation components:
`ScenarioValidator` (symbol detection and registration checks) and
`BrokerDataPreparator` (`get_valid_broker_scenario_map` filtering).

## What Is Tested

### `test_scenario_validator.py`

#### `TestDetectQuoteCurrency` / `TestDetectBaseCurrency`

Symbol splitting via known-quote-suffix detection — the fallback used before broker
config is available.

| Test | Description |
|------|-------------|
| `test_standard_usd_pair` | EURUSD → quote `USD` |
| `test_standard_jpy_pair` | USDJPY → quote `JPY` |
| `test_standard_eur_pair` | GBPEUR → quote `EUR` |
| `test_crypto_dashusd_7char` | DASHUSD (7 chars) → quote `USD`, base `DASH` — regression for #265 |
| `test_lowercase_symbol` | Input case-insensitive |
| `test_fallback_unknown_suffix` | No known suffix → last 3 / all-but-last-3 chars |

#### `TestValidateScenarioSymbols`

Phase 0 step that checks each scenario's symbol against the authoritative broker config.

| Test | Description |
|------|-------------|
| `test_valid_symbol_no_validation_result` | Symbol in broker config → no `validation_result` appended |
| `test_unknown_symbol_marks_scenario_invalid` | Symbol absent → `ValidationResult(is_valid=False)` appended, error logged |
| `test_broker_not_in_map_skips_scenario` | Broker type not in map → silent skip (upstream error handles it) |

### `test_broker_data_preparator.py`

#### `TestGetValidBrokerScenarioMap`

Validates that the broker map passed to Phase 7 reporting contains only
`(broker_type, symbol)` pairs from valid scenarios — not symbol name alone.

| Test | Description |
|------|-------------|
| `test_same_symbol_different_brokers_only_valid_broker_survives` | DASHUSD on KRAKEN (valid) + MT5 (invalid) → only KRAKEN entry in result. Core regression for the name-only filter bug. |
| `test_all_valid_scenarios_full_map_returned` | All scenarios valid → map returned unchanged |
| `test_all_invalid_scenarios_empty_map_returned` | No valid scenarios → empty dict |

## Why This Matters

The `(broker_type, symbol)` pair filter is critical: if the map were filtered by symbol
name alone, an invalid scenario on broker A would keep broker A's entry alive whenever
the same symbol happens to be valid on broker B — causing `BrokerSummary` to render
symbols from invalid scenarios.

## Test Approach

All tests use `unittest.mock.MagicMock` — no real broker configs, parquet files, or
subprocess infrastructure required.

`TestGetValidBrokerScenarioMap` sets `_broker_scenario_map` directly on the preparator
after construction (white-box unit test setup — no `prepare()` call needed).

## Files

- `tests/framework/batch_validations/test_scenario_validator.py`
- `tests/framework/batch_validations/test_broker_data_preparator.py`

## Running

```bash
pytest tests/framework/batch_validations/ -v --tb=short
```

VS Code: **"Pytest: Batch Validations (All)"** launch configuration.
