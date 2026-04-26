# Kraken Adapter Live Integration Tests

Validates `KrakenAdapter` Tier 3 order execution against the real Kraken API.
Tests run in dry-run mode (`validate=true`) — Kraken validates syntax and margin
but does NOT place orders. No funds are moved in Phase 1.

**Excluded from the unified test runner** (like `simulation/benchmark`).
Run explicitly, or as part of the release checklist.
Requires a real Kraken account with API credentials.

## Location

```
tests/live_adapters/
└── test_kraken_adapter_order_lifecycle.py   # Phase 1: AddOrder dry-run validation
```

## Dry-Run Limitations

Kraken's `validate=true` applies only to `AddOrder`. The lifecycle methods
require a real order reference and therefore real funds (Phase 2).

| Endpoint | Phase 1 (validate=true) | Phase 2 (real funds) |
|----------|------------------------|----------------------|
| `AddOrder` MARKET | ✅ validates syntax + margin | ✅ places order |
| `AddOrder` LIMIT | ✅ validates syntax + margin | ✅ places order |
| `QueryOrders` | ❌ DRYRUN ref doesn't exist | ✅ returns status |
| `EditOrder` | ❌ short-circuited | ✅ modifies + returns new txid |
| `CancelOrder` | ❌ short-circuited | ✅ cancels |

## Requirements

**Phase 1 (this suite):**
- Kraken account with API key (trade permission — no funds needed)
- Credentials file at `user_configs/credentials/kraken_credentials.json`:
  ```json
  { "api_key": "...", "api_secret": "..." }
  ```

**Phase 2 (not yet implemented — future issue):**
- Small ETH balance for margin validation (LIMIT order far below market, never filled)
- Same credentials file

Without credentials the tests skip gracefully. The standard test suite runs
unchanged — no Kraken access required.

## How to Run

```bash
# Run all live adapter tests (skips if no credentials)
pytest tests/live_adapters/ -v -m live_adapter

# Run only Kraken adapter tests
pytest tests/live_adapters/test_kraken_adapter_order_lifecycle.py -v
```

VS Code: **🧩 Pytest: Live Adapters (All)** in launch.json.

## Test Cases (Phase 1)

| Test | Order | Expected |
|------|-------|----------|
| `test_market_buy_dryrun` | MARKET LONG 0.001 ETHUSD | FILLED, ref `DRYRUN-*` |
| `test_market_sell_dryrun` | MARKET SHORT 0.001 ETHUSD | FILLED, ref `DRYRUN-*` |
| `test_limit_buy_dryrun` | LIMIT LONG 0.001 @ $100 | FILLED, ref `DRYRUN-*` |
| `test_limit_buy_with_sltp_dryrun` | LIMIT LONG + stop_loss + take_profit | FILLED, ref `DRYRUN-*` |
| `test_invalid_symbol_rejected` | MARKET 0.001 XXXUSD | REJECTED (Kraken API error) |
| `test_below_minimum_lot_rejected` | MARKET 0.00001 ETHUSD | REJECTED (below volume_min) |

The error cases (`XXXUSD`, below-min-lot) reach the real Kraken API — `execute_order()`
does not pre-validate. Kraken returns an error which the adapter catches and returns
as `BrokerOrderStatus.REJECTED`.

## Release Checklist

This suite is part of the release checklist. Run before cutting a new version:

```bash
pytest tests/live_adapters/ -v -m live_adapter --release-version X.Y.Z
```

On completion, a JSON receipt is written to `tests/live_adapters/reports/`:

```json
{
  "release_version": "1.2.2",
  "timestamp": "2026-04-26T20:00:00+00:00",
  "result": "passed",
  "tests_passed": 6,
  "tests_failed": 0,
  "tests_skipped": 0,
  "broker_settings": {
    "api_base_url": "https://api.kraken.com",
    "dry_run": true,
    "rate_limit_interval_s": 1.0,
    "request_timeout_s": 15
  }
}
```

Commit this report alongside the benchmark report as part of the release artifacts.

Phase 2 (real order lifecycle) is not yet implemented. When added, it will require
the same release-gate treatment with explicit preparation steps (ensure ETH balance,
LIMIT order far below market).

## Expanding This Suite

When MT5 live adapter tests are added (#209), place them alongside:

```
tests/live_adapters/
├── test_kraken_adapter_order_lifecycle.py
└── test_mt5_adapter_order_lifecycle.py
```

The `live_adapter` mark and runner exclusion apply automatically via `tests/conftest.py`.

## Related

- `python/framework/trading_env/adapters/kraken_adapter.py` — adapter under test
- `configs/brokers/kraken/kraken_spot_broker_config.json` — symbol specs
- `user_configs/broker_settings/kraken_spot.json` — dry_run flag (user-controlled)
- Issue #304 — when `dry_run` is retired, fixture key changes to `'paper_mode': True`
