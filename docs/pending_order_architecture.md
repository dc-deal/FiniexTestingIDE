# Pending Order Architecture: Three Worlds

## Overview

Every order passes through up to three distinct stages ("worlds") before becoming a position. Each world has its own storage, trigger logic, and modification rules.

> **Execution layer foundation:** see [architecture_execution_layer.md](architecture_execution_layer.md)
> **Live execution specifics:** see [live_execution_architecture.md](live_execution_architecture.md)

```
                          send_order()
                              │
                              ▼
              ┌───────────────────────────────┐
              │     WORLD 1: Latency Queue     │
              │  AbstractPendingOrderManager   │
              │  ._pending_orders              │
              │                                │
              │  Sim: tick-based delay          │
              │  Live: broker polling           │
              └───────────────┬───────────────┘
                              │ delay elapsed
                              ▼
              ┌────────────── DISPATCH ──────────────┐
              │                                      │
         MARKET/CLOSE                          LIMIT / STOP / STOP_LIMIT
              │                                      │
              ▼                                      ▼
        fill immediately              ┌──────────────────────────────┐
                                      │  check immediate trigger     │
                                      │  (price moved during latency)│
                                      └──────────┬───────────────────┘
                                           YES   │   NO
                                            │    │    │
                                            ▼    │    ▼
                                      fill now   │   queue to World 2 or 3
                                                 │
                          ┌──────────────────────┴──────────────────────┐
                          │                                             │
              ┌───────────▼──────────────┐              ┌──────────────▼──────────────┐
              │  WORLD 2: Active Limits   │              │  WORLD 3: Active Stops       │
              │  TradeSimulator           │              │  TradeSimulator               │
              │  ._active_limit_orders    │              │  ._active_stop_orders         │
              │                           │              │                               │
              │  LIMIT orders             │◄─── convert  │  STOP orders                  │
              │  + converted STOP_LIMIT   │              │  STOP_LIMIT orders            │
              │                           │              │                               │
              │  Trigger:                 │              │  Trigger:                     │
              │  LONG: ask <= limit_price │              │  LONG: ask >= stop_price      │
              │  SHORT: bid >= limit_price│              │  SHORT: bid <= stop_price     │
              │                           │              │                               │
              │  Fill: at limit_price     │              │  STOP → fill at market        │
              │  Fee: maker               │              │  STOP_LIMIT → convert to      │
              │                           │              │    limit (→ World 2)           │
              │  Modify: modify_limit_    │              │  Fee: taker (STOP) /          │
              │    order()                │              │    maker (STOP_LIMIT)          │
              │  Cancel: cancel_limit_    │              │                               │
              │    order()                │              │  Modify: modify_stop_order()   │
              └───────────┬──────────────┘              │  Cancel: cancel_stop_order()   │
                          │                              └──────────────┬───────────────┘
                          │ price trigger                               │ stop trigger
                          ▼                                             ▼
              ┌────────────────────────────────────────────────────────────────┐
              │                     _fill_open_order()                         │
              │              (AbstractTradeExecutor — shared)                  │
              │                                                                │
              │  Margin check → Position open → Fee calculation → Statistics   │
              └────────────────────────────────────────────────────────────────┘
```

---

## World 1: Latency Queue

**Storage:** `AbstractPendingOrderManager._pending_orders` (dict, keyed by `pending_order_id`)

**Purpose:** Simulates broker acceptance delay. Every order — regardless of type — passes through this queue first.

**Simulation:** `OrderLatencySimulator` extends `AbstractPendingOrderManager`. Uses `SeededDelayGenerator` to assign a deterministic `fill_at_tick` to each order. On each tick, `process_tick()` returns orders whose delay has elapsed.

**Live:** `LiveOrderTracker` extends `AbstractPendingOrderManager`. Tracks orders by `broker_ref` (O(1) lookup). Fill/rejection arrives via broker polling, not tick counting.

**What exits the queue:**
- `PendingOrder` objects with `order_type`, `order_action`, `direction`, `entry_price`, `order_kwargs`
- The `pending_order_id` is the same ID that `send_order()` returned in `OrderResult.order_id`

**After latency, dispatch by type:**

| order_type | order_action | Behavior |
|-----------|-------------|----------|
| MARKET | OPEN | Fill immediately at current tick bid/ask |
| LIMIT | OPEN | Check immediate trigger → fill or queue to World 2 |
| STOP | OPEN | Check immediate trigger → fill or queue to World 3 |
| STOP_LIMIT | OPEN | Check immediate trigger → convert or queue to World 3 |
| any | CLOSE | Fill immediately (close existing position) |

---

## World 2: Active Limit Orders

**Storage:** `TradeSimulator._active_limit_orders` (list of `PendingOrder`)

**Contains:**
- Pure LIMIT orders that weren't immediately fillable after latency
- Converted STOP_LIMIT orders (flagged with `order_kwargs["_from_stop_limit"] = True`)

**Trigger logic (checked every tick in Phase 2 of `_process_pending_orders`):**
- LONG: `ask <= limit_price` — price dropped to limit, favorable entry
- SHORT: `bid >= limit_price` — price rose to limit, favorable entry

**Fill behavior:**
- Fill at `limit_price` (not current tick — guaranteed fill at requested price)
- `EntryType.LIMIT` for pure limits, `EntryType.STOP_LIMIT` for converted ones
- Maker fee (providing liquidity)

**Immediate fill case:** If price already passed the limit during latency delay, order fills immediately with `FillType.LIMIT_IMMEDIATE` — never enters `_active_limit_orders`.

**Modification:** `modify_limit_order(order_id, new_price, new_stop_loss, new_take_profit)`
**Cancellation:** `cancel_limit_order(order_id)` — removes from list, returns `True`

**Live mode:** Broker handles limit matching server-side. No `_active_limit_orders` needed — fill detection through standard broker polling.

---

## World 3: Active Stop Orders

**Storage:** `TradeSimulator._active_stop_orders` (list of `PendingOrder`)

**Contains:**
- STOP orders waiting for breakout trigger
- STOP_LIMIT orders waiting for stop trigger (then convert to limit)

**Trigger logic (checked every tick in Phase 3 of `_process_pending_orders`):**
- LONG: `ask >= stop_price` — breakout upward, enter long
- SHORT: `bid <= stop_price` — breakout downward, enter short

**Fill behavior by order type:**

| Type | On trigger | Fill price | Fee |
|------|-----------|-----------|-----|
| STOP | Fill at current market (bid/ask) | Current tick | Taker |
| STOP_LIMIT | Convert to LIMIT → move to World 2 | Limit price (later) | Maker |

**STOP_LIMIT conversion** (`_convert_stop_limit_to_limit`):
1. Mutates `PendingOrder`: `order_type` → LIMIT, `entry_price` → `limit_price`
2. Sets `_from_stop_limit = True` in `order_kwargs` (for correct EntryType/FillType in Phase 2)
3. Checks if limit price already reached:
   - YES → fills immediately as `FillType.STOP_LIMIT`
   - NO → appends to `_active_limit_orders` (enters World 2)

**Modification:** `modify_stop_order(order_id, new_stop_price, new_limit_price, new_stop_loss, new_take_profit)`
**Cancellation:** `cancel_stop_order(order_id)` — removes from list, returns `True`

---

## Order ID Chain

The same ID flows through the entire lifecycle. No separate ID systems.

```
send_order(symbol, order_type, ...)
    │
    ▼
OrderResult.order_id = "EURUSD_1"     ← Decision Logic receives this
    │
    ▼
PendingOrder.pending_order_id = "EURUSD_1"    ← Internal tracking
    │
    ▼
Decision Logic stores ID (e.g. self._pending_limit_id = "EURUSD_1")
    │
    ▼
modify_limit_order(order_id="EURUSD_1")    ← Searches _active_limit_orders
modify_stop_order(order_id="EURUSD_1")     ← Searches _active_stop_orders
cancel_limit_order(order_id="EURUSD_1")    ← Same pattern
cancel_stop_order(order_id="EURUSD_1")     ← Same pattern
    │
    ▼
get_pending_stats()                         ← ActiveOrderSnapshot.order_id = "EURUSD_1"
```

---

## Why Separate Modify Commands

Three different contexts require three different validation approaches:

### `modify_position()` — Position already open
- SL/TP validated against **current tick** (bid/ask)
- Position is filled → current market price is the only sensible reference
- Example: position opened at 1.1000, current ask 1.1050 → SL at 1.0950 validated against 1.1050

### `modify_limit_order()` — Waiting for limit fill
- SL/TP validated against **limit price** (the expected fill price)
- Position doesn't exist yet → current tick is irrelevant
- Example: limit buy at 1.0800, SL at 1.0750 → validated against 1.0800

### `modify_stop_order()` — Waiting for stop trigger
- `stop_price` validated against **current tick direction** (must still be reachable)
- SL/TP validated against **stop_price** (STOP) or **limit_price** (STOP_LIMIT)
- STOP: position will open at market when triggered → stop_price is best approximation
- STOP_LIMIT: position will open at limit_price → validated against limit_price

A single `modify()` method would need complex branching to select the correct validation reference. Separate commands keep each validation path clean and explicit.

---

## PendingOrderStats and ActiveOrderSnapshot

Statistics are collected via `get_pending_stats()` and include all three worlds.

### ActiveOrderSnapshot

Snapshot of a single active order (limit or stop) for stats/reporting:

```python
@dataclass
class ActiveOrderSnapshot:
    order_id: str                      # Same ID from send_order()
    order_type: OrderType              # LIMIT, STOP, or STOP_LIMIT
    symbol: str
    direction: OrderDirection           # LONG or SHORT
    lots: float
    entry_price: float                  # Limit price or stop trigger price
    limit_price: Optional[float]        # Only for STOP_LIMIT (fill price after trigger)
```

### PendingOrderStats fields for active orders

```python
active_limit_orders: List[ActiveOrderSnapshot]    # World 2 snapshot
active_stop_orders: List[ActiveOrderSnapshot]      # World 3 snapshot
latency_queue_count: int                           # World 1 count (no details — orders are in transit)
```

### get_active_order_counts()

Quick query for Decision Logic to check order distribution across worlds:

```python
def get_active_order_counts(self) -> Dict[str, int]:
    return {
        "latency_queue": self.latency_simulator.get_pending_count(),
        "active_limits": len(self._active_limit_orders),
        "active_stops": len(self._active_stop_orders),
    }
```

Exposed via `DecisionTradingAPI.get_active_order_counts()`.

---

## Margin Check Timing

Margin is checked at **fill time**, not at submission. This has important implications:

```
send_order(LIMIT, lots=1.0)   → accepted (no margin check)
    ↓ latency
    ↓ enters World 2
    ↓ ... 500 ticks later ...
    ↓ price triggers
_fill_open_order()             → margin check HERE
    ↓
    ├── sufficient → position opens
    └── insufficient → REJECTED (OrderResult in _order_history)
```

**Consequence:** Multiple pending orders can coexist even if combined margin would exceed available balance. First order to trigger fills successfully; subsequent orders may be margin-rejected if balance is depleted.

This matches real broker behavior — order acceptance is separate from order execution.

---

## Scenario End Cleanup

At scenario end, `close_all_remaining_orders()` handles all three worlds:

1. **Open positions:** Closed via synthetic `PendingOrder` objects that bypass the latency pipeline entirely (no pending stats impact). This is internal cleanup, not an algo action.

2. **Active limit orders** (`_active_limit_orders`): **Preserved** (not cleared). A warning is logged. `get_pending_stats()` is called after cleanup and snapshots them into `PendingOrderStats.active_limit_orders` — capturing the bot's unfilled plan at scenario end.

3. **Active stop orders** (`_active_stop_orders`): **Preserved** (not cleared). A warning is logged. Snapshotted into `PendingOrderStats.active_stop_orders` — capturing untriggered breakout orders.

4. **Latency queue** (`clear_pending()`): Any genuine stuck-in-pipeline orders are recorded as `FORCE_CLOSED` with a `reason` field (e.g. `"scenario_end"`). Only these real anomalies produce individual `PendingOrderRecord` entries in `anomaly_orders`.

**Note:** `check_clean_shutdown()` validates only the latency queue (via `_has_pipeline_orders()` override in `TradeSimulator`) — intentionally preserved active limit/stop orders do not trigger cleanup warnings.

---

## Three-Phase Processing Order

`_process_pending_orders()` runs on every tick, in strict order:

```
Phase 1: Latency drain      → process_tick() returns elapsed orders
                              → dispatch by order_type (fill / queue)

Phase 2: Limit monitoring   → iterate _active_limit_orders
                              → check _is_limit_price_reached()
                              → fill triggered orders, keep others

Phase 3: Stop monitoring    → iterate _active_stop_orders
                              → check _is_stop_price_reached()
                              → STOP: fill at market
                              → STOP_LIMIT: _convert_stop_limit_to_limit()
```

**Order matters:** A STOP_LIMIT order can exit World 3 (Phase 3) and enter World 2 in the same tick. It will be checked by Phase 2 on the *next* tick (or immediately during conversion if limit price already reached).

---

## Entry Types and Fee Mapping

| EntryType | Trigger | Fill Price | Fee Model |
|-----------|---------|-----------|-----------|
| MARKET | Immediate | Current bid/ask | Taker |
| LIMIT | Price reaches limit | Limit price | Maker |
| STOP | Price reaches stop trigger | Current bid/ask (market) | Taker |
| STOP_LIMIT | Stop triggers, then limit fills | Limit price | Maker |

Fee logic in `AbstractTradeExecutor._fill_open_order()`:
```python
is_maker = entry_type in (EntryType.LIMIT, EntryType.STOP_LIMIT)
```

Maker/taker distinction only affects brokers with maker/taker fee models (e.g. Kraken). Spread-based brokers (MT5) are unaffected.
