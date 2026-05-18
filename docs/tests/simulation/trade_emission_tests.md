# Sim Trade Emission Tests Documentation

## Overview

The trade_emission test suite validates the BrokerTrade emission introduced by #326 on the **simulation side**. Sim and live share the emission helper `_synthesize_pending_trade` in `AbstractTradeExecutor`, called from `_fill_open_order` and `_fill_close_order` when `pending.trades` is empty. The suite verifies that sim fills produce the expected BrokerTrade shape and that close fills emit their own trade on the close PendingOrder.

**Test Configuration:** Direct `TradeSimulator` instantiation via fixture
- Adapter: `MockBrokerAdapter(mode=INSTANT_FILL)` with zero inbound latency
- Account: 10,000 USD initial balance
- Symbol: BTCUSD (Mock built-in spec)

**Total Tests:** 4

**Location:** `tests/simulation/trade_emission/`

---

## Test Structure

### Direct Executor Instantiation (no scenario execution)

Mirrors the pattern from `modify_lifecycle` — direct `TradeSimulator` instantiation, controlled msc ticks, sharp focus on a single behavior per test.

```
tests/simulation/trade_emission/
├── __init__.py
├── conftest.py                          ← sim_executor fixture + feed_sim_tick helper
└── test_trade_emission.py
```

Helper functions in `conftest.py`:

| Helper | Purpose |
|---|---|
| `sim_executor` (fixture) | TradeSimulator with zero-latency INSTANT_FILL Mock |
| `feed_sim_tick(executor, msc, bid, ask, symbol)` | Direct tick feed with controlled msc |

Each test:
1. Submits a MARKET order via `executor.open_order(...)`
2. Feeds a tick to fill the order
3. Inspects synthesis calls / order history / position state

---

## Test Cases

### TestSimMarketFillEmitsTrade

| Test | Description |
|---|---|
| `test_open_position_has_no_pending_trade_lookup` | After MARKET fill, position is created. Trade synthesis happened inside `_fill_open_order` on the pending order (consumed). |
| `test_history_shows_executed_after_fill` | Order history contains exactly one EXECUTED entry after fill |

### TestSimSyntheticTradeShape

| Test | Description |
|---|---|
| `test_synthesized_trade_matches_fill` | Intercepts `_synthesize_pending_trade` and verifies the BrokerTrade has correct volume (0.001), side (LONG), is_maker=False (MARKET = taker), fee_currency=USD |

### TestSimCloseEmitsTrade

| Test | Description |
|---|---|
| `test_close_synthesizes_trade_on_close_pending` | Closing a filled position emits a second `_synthesize_pending_trade` call with `order_action=close` |

---

## Running the Tests

```bash
pytest tests/simulation/trade_emission/ -v
```

Launch.json entry: `🧩 Pytest: Broker Trade Records (#326)` (runs this suite together with the live executor and parity counterparts).

---

## Architecture Notes

### Shared Synthesis Path

Sim and live converge on `AbstractTradeExecutor._synthesize_pending_trade(...)` — invoked from `_fill_open_order` and `_fill_close_order` when `pending.trades` is empty. The synthesis builds a single BrokerTrade with the locally-computed fee (`entry_fee.cost` for opens, `0.0` for closes in V1) and appends it via `pending.append_trade(...)`.

The conditional check `if not pending_order.trades` preserves any per-execution data that an earlier consumer already populated (e.g. real broker QueryTrades response in a future async-polling path). Sim always finds the list empty at fill time; the synthesis fires unconditionally.

### Sim/Live Parity

The parity counterpart `tests/parity/test_trade_records_parity.py` asserts that sim and live produce equivalent synthesis behavior — same call count per order, same volume, same side, identical cumulative aggregates. Together these two suites lock down the contract that #327 Drift Audit and #151 Reconciliation depend on.

---

## Related

- [Broker Trade Records architecture](../../architecture/broker_trade_records.md) — full data model and Tier-3 contract
- [Live Executor Tests](../autotrader/live_executor_tests.md) — covers the live-side BrokerTrade emission + async trades_query roundtrip
- [Trade Records Parity Tests](../parity/) — sim/live agreement on post-fill state and synthesis shape
