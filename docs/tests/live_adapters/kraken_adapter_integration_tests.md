# Kraken Adapter Live Integration Tests

Validates `KrakenAdapter` Tier 3 order execution against the real Kraken API.
Requires a real Kraken account with API credentials.

**Excluded from the unified test runner** (like `simulation/benchmark`).
Run explicitly, or as part of the release checklist.

## Layer & Relation to the Live Field Study (#332)

This suite is the **adapter-integration layer** of the test pyramid:

```
Unit / mock          → code correctness
Adapter integration  → broker transport, isolated   ← THIS suite
UAT (full pipeline)  → Live Field Study (#332)
Soak / endurance     → long-running stability (out of scope)
```

**What only this layer proves:** the **raw `BrokerResponse` contract** at the adapter
boundary — `status`, `broker_ref` (real vs. `DRYRUN-`), `rejection_reason`,
`executed_price`, `filled_lots` — asserted *directly*. The Field Study observes outcomes
through the *pipeline* (positions/orders via the #348 event channel); it does **not**
assert the raw adapter response. A bug in the executor's response→state translation is
therefore masked in the Field Study but **caught here** (especially `_fill`).

**Diagnostic value:** a red Field Study → run this suite to localize (adapter transport
vs. bot logic). **Cost ladder:** `_dry` (free) → `_live` (~free, no fills) → `_fill`
(fees only).

**Per-adapter mandate:** every new adapter ships this suite (release-gate). The granular
adapter contract scales *per broker*; the Field Study (#332) is the integrated gate on
top, and the account-protection guards are certified separately (#358) — also per adapter.

## Location

```
tests/live_adapters/
├── test_kraken_adapter_order_lifecycle_dry.py    # AddOrder dry-run (validate=true), no funds
├── test_kraken_adapter_order_lifecycle_live.py   # Full lifecycle, real orders, no fills
└── test_kraken_adapter_order_lifecycle_fill.py   # Fill validation, real MARKET execution
```

## Dry-Run Limitations

Kraken's `validate=true` applies only to `AddOrder`. The lifecycle methods
require a real order reference and therefore real funds (Phase 2).

| Endpoint | Phase 1 (validate=true) | Phase 2 (real orders) |
|----------|------------------------|----------------------|
| `AddOrder` MARKET | ✅ validates syntax + margin | ✅ places order |
| `AddOrder` LIMIT | ✅ validates syntax + margin | ✅ places order |
| `QueryOrders` | ❌ DRYRUN ref doesn't exist | ✅ returns status |
| `AmendOrder` | ❌ short-circuited | ✅ amends in-place (same txid) |
| `CancelOrder` | ❌ short-circuited | ✅ cancels |

## Requirements

**Phase 1 (dry-run, no funds):**
- Kraken account with API key (trade permission)
- Credentials file at `user_configs/credentials/kraken_credentials.json`:
  ```json
  { "api_key": "...", "api_secret": "..." }
  ```

**Live (_live, real orders):**
- Same credentials file
- ~$10 USD available on the account (reserved during test, returned on cancel)

**Fill (_fill, real execution):**
- Same credentials file
- Sufficient balance for fees only (~$0.012 per run — no capital required beyond fees)

Without credentials the tests skip gracefully. The standard test suite runs
unchanged — no Kraken access required.

## How to Run

```bash
# Run all live adapter tests (Phase 1 + Phase 2)
pytest tests/live_adapters/ -v -m live_adapter

# Run dry-run tests only (no funds needed)
pytest tests/live_adapters/test_kraken_adapter_order_lifecycle_dry.py -v

# Run live tests only (real orders, funds required)
pytest tests/live_adapters/test_kraken_adapter_order_lifecycle_live.py -v
```

VS Code: **🧩 Pytest: Live Adapters (All)** in launch.json.

## Test Cases — Phase 1

| Test | Order | Expected |
|------|-------|----------|
| `test_market_buy_dryrun` | MARKET LONG 0.001 ETHUSD | FILLED, ref `DRYRUN-*` |
| `test_market_sell_dryrun` | MARKET SHORT 0.001 ETHUSD | FILLED, ref `DRYRUN-*` |
| `test_limit_buy_dryrun` | LIMIT LONG 0.1 @ $100 | FILLED, ref `DRYRUN-*` |
| `test_limit_buy_with_sltp_dryrun` | LIMIT LONG + stop_loss + take_profit | FILLED, ref `DRYRUN-*` |
| `test_invalid_symbol_rejected` | MARKET 0.001 XXXUSD | REJECTED (Kraken API error) |
| `test_below_minimum_lot_rejected` | MARKET 0.00001 ETHUSD | REJECTED (below volume_min) |

The error cases (`XXXUSD`, below-min-lot) reach the real Kraken API — `execute_order()`
does not pre-validate. Kraken returns an error which the adapter catches and returns
as `BrokerOrderStatus.REJECTED`.

## Test Cases — Phase 2

| Step | Action | Expected |
|------|--------|----------|
| 1 | `execute_order` LIMIT 0.1 ETH @ $100 | `PENDING`, real txid (not `DRYRUN-*`) |
| 2 | `check_order_status(txid)` | `PENDING` (Kraken `open` → PENDING) |
| 3 | `modify_order(txid, new_price=110.0)` | `PENDING`, same txid (Kraken AmendOrder amends in-place) |
| 4 | `check_order_status(txid)` | `PENDING` |
| 5 | `cancel_order(txid)` | `CANCELLED` |

Lot size 0.1 ETH × $100 = $10 meets Kraken's ~$5 cost minimum. The order is placed
far below market (~$2000+) and is never filled. A `try/finally` block in the test
cancels the order even if an assertion fails mid-way.

## Coverage Matrix

| Capability | Dry (_dry) | Live (_live) | Fill (_fill) |
|------------|:----------:|:------------:|:------------:|
| MARKET buy | ✅ | — | ✅ (real fill) |
| MARKET sell | ✅ | — | ✅ (real fill) |
| LIMIT buy | ✅ | ✅ (lifecycle) | — |
| LIMIT + SL/TP kwargs | ✅ | — | — |
| `QueryOrders` → PENDING | — | ✅ (×2) | — |
| `QueryOrders` → FILLED + fill_price + filled_lots | — | — | ✅ (×2) |
| `AmendOrder` → same txid | — | ✅ | — |
| `CancelOrder` | — | ✅ | — |
| Invalid symbol → REJECTED | ✅ | — | — |
| Below `volume_min` → REJECTED | ✅ | — | — |

LIMIT sell is not separately tested — mechanics are identical to LIMIT buy, only
the `type` field differs. Pure stop orders are not supported by Kraken (`stop_orders=False`
in `OrderCapabilities`); the adapter uses `StopLimit` instead.

## Release Checklist

Both phases are part of the release checklist. Run before cutting a new version:

```bash
pytest tests/live_adapters/ -v -m live_adapter --release-version X.Y.Z
```

On completion, a JSON receipt is written to `tests/live_adapters/reports/`:

```json
{
  "release_version": "1.3.0",
  "git_commit": "f2b9321",
  "timestamp": "2026-04-26T21:09:25+00:00",
  "result": "passed",
  "tests_passed": 7,
  "tests_failed": 0,
  "tests_skipped": 0,
  "tests_run": ["test_market_buy_dryrun", "...", "test_limit_order_lifecycle"],
  "connection_settings": {
    "dry_run": true,
    "api_base_url": "https://api.kraken.com",
    "rate_limit_interval_s": 1.0,
    "request_timeout_s": 15,
    "poll_interval_ms": 5000
  }
}
```

Note: `connection_settings` in the report reflects `market_config.json`, not per-test overrides.
Phase 2 explicitly calls `enable_live(..., dry_run=False)` at runtime.

Commit this report alongside the benchmark report as release artifacts.

## Reference Pattern for New Adapters

This Kraken suite is the **reference implementation** for all future live adapter test suites.
When adding a new broker (e.g. MT5, #209), mirror this three-file structure exactly:

> **§26 note (docs):** the broker-agnostic methodology (this section + *Layer & Relation
> to the Live Field Study* above) currently lives in this Kraken doc. When the **2nd
> adapter (MT5, #209)** lands, extract it into
> `docs/tests/live_adapters/adapter_integration_overview.md` (a wrapper/overview doc) and
> leave thin per-broker docs (`kraken_…`, `mt5_…`) that reference it; cross-link
> `adapter_development_guide.md` + #238. Do not create the wrapper prematurely (1 adapter today).

```
tests/live_adapters/
├── test_kraken_adapter_order_lifecycle_dry.py    ← reference: dry-run template
├── test_kraken_adapter_order_lifecycle_live.py   ← reference: lifecycle template
├── test_kraken_adapter_order_lifecycle_fill.py   ← reference: fill validation template
├── test_mt5_adapter_order_lifecycle_dry.py
├── test_mt5_adapter_order_lifecycle_live.py
└── test_mt5_adapter_order_lifecycle_fill.py
```

**Checklist per new adapter (_dry):**
- AddOrder with validate/paper equivalent for all supported order types
- Error cases: invalid symbol → REJECTED, below `volume_min` → REJECTED
- Fixture forces dry-run/paper mode; credentials guard skips if absent

**Checklist per new adapter (_live):**
- Full lifecycle: place LIMIT far below market → query → modify (if supported) → cancel
- Verify `modify_order` returns the expected `broker_ref` (Kraken `AmendOrder` keeps it; cancel-replace brokers return a new one)
- `try/finally` block cancels the order on assertion failure — no open orders left

**Checklist per new adapter (_fill):**
- MARKET buy (minimum lot) → poll `check_order_status` until FILLED
- Assert `fill_price > 0` and `filled_lots > 0` — validates QueryOrders response parsing
- MARKET sell (same lot) immediately after — net exposure back to zero
- Cost: spread + fees only, no capital required beyond that

**Key lessons from Kraken implementation:**
- `modify_order` requires `symbol` — brokers need pair resolution at modification time
- `_live` fixture must explicitly set `dry_run=False` — never rely on config file default
- Rate limit at 0.5s in test fixtures (vs 1.0s default) — reduces suite runtime by half
- LIMIT cost minimum: check broker's minimum order cost, not just `volume_min` (Kraken: ~$5)

The `live_adapter` mark and runner exclusion apply automatically via `tests/conftest.py` —
no changes needed there when adding new adapter test files.

## Related

- `python/framework/trading_env/adapters/kraken_adapter.py` — adapter under test
- `configs/brokers/kraken/kraken_spot_broker_config.json` — symbol specs
- `configs/market_config.json` — connection settings for `kraken_spot` (override via `user_configs/market_config.json`)
- Issue #304 — when `dry_run` is retired, fixture key changes to `'paper_mode': True`
