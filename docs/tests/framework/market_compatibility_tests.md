# Market Compatibility Tests

## Purpose

Verifies that workers declaring an incompatible market activity metric are rejected at pre-flight time, before any subprocess bridge. Protects against the failure mode where (e.g.) OBV silently runs on a forex broker and returns zeros because forex ticks carry no real volume.

## What Is Tested

### `test_classmethod_mandatory.py`

Contract-level tests on `AbstractWorker.get_required_activity_metric()`.

| Test | Description |
|------|-------------|
| `test_core_workers_declare_activity_metric` | ×6 — Every CORE worker returns its expected metric: RSI/Envelope/MACD/HeavyRSI/BacktestingSample → `None`, OBV → `'volume'` |
| `test_missing_override_raises_not_implemented` | A subclass that does not override the method raises `NotImplementedError` with an actionable message (mentions class name, method name, `'volume'`, `'tick_count'`) |

### `test_activity_metric_lookup.py`

Tests `MarketConfigManager` resolves broker → metric correctly using the real `configs/market_config.json`.

| Test | Description |
|------|-------------|
| `test_forex_broker_returns_tick_count` | `mt5` → `'tick_count'` |
| `test_crypto_broker_returns_volume` | `kraken_spot` → `'volume'` |
| `test_unknown_broker_raises_value_error` | Unknown broker string raises `ValueError` |

### `test_happy_path.py`

Compatible combinations must pass validation without errors.

| Test | Description |
|------|-------------|
| `test_rsi_on_forex_broker_passes` | RSI (metric=None) on MT5 |
| `test_rsi_on_crypto_broker_passes` | RSI (metric=None) on kraken_spot |
| `test_obv_on_crypto_broker_passes` | OBV (metric=volume) on kraken_spot |
| `test_mixed_workers_on_crypto_broker_passes` | RSI + Envelope + OBV together on kraken_spot |
| `test_empty_worker_instances_passes` | Scenario without workers does not crash the validator |

### `test_validator_skip_incompatible.py`

Incompatible combinations must produce structured errors without raising exceptions — matches the existing "skip and report" pattern for out-of-range `start_date`.

| Test | Description |
|------|-------------|
| `test_obv_on_forex_broker_is_rejected` | OBV on MT5 → one error mentioning instance, type, required metric, broker metric, market type |
| `test_error_message_is_actionable` | Error text contains "remove" or "switch" — tells the user how to fix the scenario |
| `test_multiple_incompatible_workers_all_reported` | Two OBV instances on MT5 → two distinct errors, one per instance |
| `test_mixed_valid_and_invalid_workers_only_invalid_reported` | RSI + Envelope + OBV on MT5 → only OBV reported, RSI/Envelope absent from error messages |
| `test_unknown_worker_type_reported_not_raised` | Unknown `CORE/does_not_exist` surfaces as validator error, does not bubble `ValueError` out of the validator |
| `test_unknown_broker_reports_single_error` | Unknown broker short-circuits with a single error, no per-worker iteration |

## Test Design Philosophy

The suite uses the **real** `MarketConfigManager` and `WorkerFactory` (both session-scoped fixtures in `conftest.py`) because the validation logic is a thin shim over these two components. Mocking either would let broken config assumptions slip through — the tests must break if someone reclassifies `kraken_spot` as forex, or deletes the OBV registration from `WorkerFactory._load_core_workers()`.

`ScenarioDataValidator` is instantiated with empty `data_coverage_reports` since market-compatibility validation has no dependency on tick coverage. The `app_config` is a `MagicMock` returning `'standard'` warmup mode and `['seamless']` allowed gap categories — the validator constructor calls these once at init, they are never re-read during compatibility checks.

`make_scenario()` in `conftest.py` builds minimal `SingleScenario` objects with just enough fields to pass the validator's interface — no real coverage report, no tick loading, no subprocess.

## Files

- `tests/framework/market_compatibility/conftest.py` — Shared fixtures and `make_scenario()` helper
- `tests/framework/market_compatibility/test_classmethod_mandatory.py`
- `tests/framework/market_compatibility/test_activity_metric_lookup.py`
- `tests/framework/market_compatibility/test_happy_path.py`
- `tests/framework/market_compatibility/test_validator_skip_incompatible.py`

Modules under test:

- `python/framework/workers/abstract_worker.py` — classmethod contract
- `python/framework/validators/scenario_data_validator.py` — `validate_worker_market_compatibility()`
- `python/framework/exceptions/market_compatibility_error.py` — structured error
- `python/configuration/market_config_manager.py` — metric lookup

## Running

```bash
pytest tests/framework/market_compatibility/ -v --tb=short
```

VS Code: **"🧩 Pytest: Market Compatibility (All)"** launch configuration.

## Related

- Architecture: [`docs/architecture/market_capabilities.md`](../../architecture/market_capabilities.md)
- User guide: [`docs/user_guides/worker_naming_doc.md`](../../user_guides/worker_naming_doc.md)
