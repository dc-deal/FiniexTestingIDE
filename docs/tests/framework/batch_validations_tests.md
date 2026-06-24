# Batch Validations Test Suite

## Purpose

Unit tests for batch pipeline validation and configuration components:
`ScenarioValidator` (symbol detection and registration checks),
`BrokerDataPreparator` (`get_valid_broker_scenario_map` filtering),
`MarketConfigManager` (`ConfigMode` parsing),
`BrokerConfigFactory` (symbol integrity validation and config hash computation), and
`KrakenConfigFetcher` (runtime cache merge behavior and lazy symbol addition).

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

#### `TestValidateSwapModes`

Phase 0 step (#407) rejecting a symbol whose `swap_mode` the swap engine does not model
(`points` / `none` are supported; the rest fail per-scenario, §33 — the batch is not aborted).

| Test | Description |
|------|-------------|
| `test_points_mode_passes` | `swap_mode=points` → no `validation_result` appended |
| `test_none_mode_passes` | `swap_mode=none` (swap-free / spot) → passes (books nothing) |
| `test_percentage_mode_marks_invalid` | `swap_mode=percentage` → `ValidationResult(is_valid=False)`, error names the mode |
| `test_unknown_mode_marks_invalid` | `UNKNOWN` (unparseable string mapped by the adapter) → invalid |
| `test_broker_not_in_map_skips_scenario` | Broker type not in map → silent skip |

### `test_broker_data_preparator.py`

#### `TestGetValidBrokerScenarioMap`

Validates that the broker map passed to Phase 7 reporting contains only
`(broker_type, symbol)` pairs from valid scenarios — not symbol name alone.

| Test | Description |
|------|-------------|
| `test_same_symbol_different_brokers_only_valid_broker_survives` | DASHUSD on KRAKEN (valid) + MT5 (invalid) → only KRAKEN entry in result. Core regression for the name-only filter bug. |
| `test_all_valid_scenarios_full_map_returned` | All scenarios valid → map returned unchanged |
| `test_all_invalid_scenarios_empty_map_returned` | No valid scenarios → empty dict |

### `test_market_config_manager.py`

#### `TestConfigModeParsing`

Parsing and validation of the `config_mode` field introduced in #252.

| Test | Description |
|------|-------------|
| `test_dynamic_mode_parsed_correctly` | `kraken_spot` with `config_mode=dynamic` → `ConfigMode.DYNAMIC` |
| `test_static_default_when_omitted` | `mt5_forex` without `config_mode` field → `ConfigMode.STATIC` (default) |
| `test_invalid_config_mode_raises` | `config_mode=turbo` → `ValueError` with mode name in message |
| `test_get_config_mode_getter` | `get_config_mode('kraken_spot')` returns `ConfigMode.DYNAMIC` |
| `test_unknown_broker_raises` | `get_config_mode('unknown_broker')` → `ValueError` |

Uses `unittest.mock.patch` on `MarketConfigFileLoader.get_config` — no file I/O.

### `test_broker_config_factory.py`

#### `TestSymbolIntegrityValidation`

`_validate_symbol_integrity()` checks that `base_currency` + `quote_currency` match the symbol key.

| Test | Description |
|------|-------------|
| `test_valid_config_passes` | Correct BTCUSD base/quote → no error |
| `test_wrong_base_currency_raises` | DASHUSD with `base=ETH` → `ValueError` naming the symbol |
| `test_wrong_quote_currency_raises` | BTCUSD with `quote=EUR` → `ValueError` naming the symbol |
| `test_7char_symbol_validates_correctly` | DASHUSD → base `DASH`, quote `USD` passes |
| `test_missing_currency_fields_skipped` | Entry without `base_currency` → no crash |

#### `TestConfigHashComputation`

`_inject_symbols_hash()` computes a stable 8-char SHA256 of the symbols block.

| Test | Description |
|------|-------------|
| `test_hash_is_8_chars` | Hash injected into `_config_meta.symbols_hash` is exactly 8 chars |
| `test_hash_stable_for_same_symbols` | Same symbols dict → same hash across calls |
| `test_hash_changes_when_symbol_spec_changes` | `volume_min` change → different hash |
| `test_hash_stable_when_only_meta_changes` | Different `last_fetched` timestamps → same hash |

All tests call static methods directly with in-memory dicts — no file I/O.

### `test_kraken_config_fetcher.py`

#### `TestMergeWithCache`

`_merge_with_cache()` behavior: no tombstoning of existing symbols, per-symbol `_last_fetched` timestamps.

| Test | Description |
|------|-------------|
| `test_existing_symbol_not_tombstoned` | ETHUSD in cache + DOTUSD fetched → ETHUSD stays `_active: true` |
| `test_fresh_symbol_gets_active_and_last_fetched` | Fresh symbol gets `_active: true` and valid ISO `_last_fetched` field |
| `test_existing_symbol_last_fetched_preserved` | Existing symbol's `_last_fetched` unchanged when a different symbol is fetched |
| `test_no_cache_returns_fresh_symbols_only` | No existing cache → result contains only the fresh symbol |

#### `TestFetchWithCacheSymbolCheck`

Symbol-presence check added to `fetch_broker_config_with_cache()`.

| Test | Description |
|------|-------------|
| `test_fresh_cache_with_symbol_skips_api` | Cache fresh + symbol present → no API call |
| `test_fresh_cache_missing_symbol_triggers_fetch` | Cache fresh + symbol absent → API called, symbol merged, existing symbols preserved |

---

## Why This Matters

The `(broker_type, symbol)` pair filter is critical: if the map were filtered by symbol
name alone, an invalid scenario on broker A would keep broker A's entry alive whenever
the same symbol happens to be valid on broker B — causing `BrokerSummary` to render
symbols from invalid scenarios.

`_validate_symbol_integrity()` guards against copy-paste errors in broker config JSON and
schema drift in refreshed runtime cache files — both would silently produce wrong P&L
calculations if base/quote currencies are mismatched against the symbol key.

The config hash provides a reproducibility anchor: batch summaries and the AutoTrader
live header show an 8-char seed ID so it is always clear which exact symbol specification
version was active during a session.

## Test Approach

All tests use `unittest.mock.MagicMock` — no real broker configs, parquet files, or
subprocess infrastructure required.

`TestGetValidBrokerScenarioMap` sets `_broker_scenario_map` directly on the preparator
after construction (white-box unit test setup — no `prepare()` call needed).

## Files

- `tests/framework/batch_validations/test_scenario_validator.py`
- `tests/framework/batch_validations/test_broker_data_preparator.py`
- `tests/framework/batch_validations/test_market_config_manager.py`
- `tests/framework/batch_validations/test_broker_config_factory.py`
- `tests/framework/batch_validations/test_kraken_config_fetcher.py`

## Running

```bash
pytest tests/framework/batch_validations/ -v --tb=short
```

VS Code: **"Pytest: Batch Validations (All)"** launch configuration.
