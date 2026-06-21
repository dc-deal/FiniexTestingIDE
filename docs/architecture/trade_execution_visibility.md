# Trade Execution Visibility — Trigger / BrokerOrder / Fills

## Concept

Every order produces a hierarchy of execution events. This document defines the project's three-level model and how it surfaces in the data, the displays, and the CSV export.

```
Trigger          (what we sent)
   ↓
BrokerOrder      (the broker's acceptance + resting state, if any)
   ↓
Fills            (per-execution atomic trades, 1..N per order)
```

Industry parallels:

| Layer | FIX | Kraken | MT5 | IBKR | Binance |
|---|---|---|---|---|---|
| Trigger | `NewOrderSingle` | `AddOrder` → txid | `OrderSend` → ticket | `placeOrder` → orderId | `POST /order` |
| BrokerOrder | `ExecReport(OrdStatus=NEW)` | OpenOrders + txid | "Pending Order" tab | OpenOrder updates | "Open Order" |
| Fills | `ExecReport(PARTIALLY_FILLED/FILLED)` | `QueryTrades` → tradeid | "Deal" history | `Execution` events | "Trade" per order |

For **MARKET** orders the BrokerOrder layer typically collapses — acceptance and fill arrive in one event. For **LIMIT** the BrokerOrder layer is visible (often resting seconds to hours).

## Data Model

`BrokerTrade` is the atomic execution. See [Broker Trade Records](broker_trade_records.md) for the type itself.

`Position` and `TradeRecord` carry the per-execution detail through to the consumer:

```python
@dataclass
class Position:
    ...
    broker_ref: Optional[str]                      # Kraken txid / MT5 ticket
    entry_trades: List[BrokerTrade]                # Original entry executions

@dataclass
class TradeRecord:
    ...
    entry_trades: List[BrokerTrade]                # Shallow copy of Position.entry_trades
    exit_trades:  List[BrokerTrade]                # This close-event's executions
```

### Sharing Semantics on Partial Close

When a position is partially closed N times, **N TradeRecords are produced**. All of them carry the same shallow copy of `Position.entry_trades` (same `trade_id` values). Each carries its own distinct `exit_trades`.

```
Position pos_1 — opened LONG 0.03 in one fill (T-E001)
   ├── PARTIAL close 0.01 @ tick 2000  → TR#1
   │     entry_trades=[T-E001]                  ← shared
   │     exit_trades =[T-X001]
   ├── PARTIAL close 0.01 @ tick 4000  → TR#2
   │     entry_trades=[T-E001]                  ← shared
   │     exit_trades =[T-X002]
   └── FULL    close 0.01 @ tick 8000  → TR#3 (remainder)
         entry_trades=[T-E001]                  ← shared
         exit_trades =[T-X003]
```

Renderers detect sharing via `trade_id` frequency across `entry_trades` lists within a scenario and annotate accordingly.

### Lots Asymmetry — Important to Read

For a partial-close TradeRecord, the aggregate `lots` field and the underlying `entry_trades[0].volume` differ by design:

| Field | Value | Meaning |
|---|---|---|
| `TradeRecord.lots` | 0.01 | The portion closed by *this* TradeRecord event |
| `TradeRecord.entry_trades[0].volume` | 0.03 | The original BrokerTrade that opened the *whole* position (un-scaled) |
| `TradeRecord.exit_trades[0].volume` | 0.01 | The actual close BrokerTrade for *this* event |
| `TradeRecord.total_fees` | proportional | Scaled to close ratio (e.g. 1/3 of original fee) |

The sub-line `entry` shows the **broker-truth** of the original execution; the aggregate row shows the per-record share. Both numbers are correct; they describe different things.

## Trade-Event Side vs Position Direction (BUY/SELL vs LONG/SHORT)

Two distinct concepts the industry has standardised on:

- **`OrderSide` (BUY/SELL)** — what the execution did. Per-fill view. FIX `Side(54)`, IBKR Action (`BOT`/`SLD`), Binance/Kraken/MT5 Side column.
- **`OrderDirection` (LONG/SHORT)** — what the position looks like. Per-position view.

The mapping is deterministic:

| Position direction | Action | Execution side |
|---|---|---|
| LONG | OPEN  | BUY  |
| LONG | CLOSE | SELL |
| SHORT | OPEN  | SELL |
| SHORT | CLOSE | BUY  |

Single source of truth: helper `direction_to_side(direction, action) → OrderSide` in `order_types.py`. Used by every BrokerTrade construction site and by TradeRecord builders.

Where each concept lives in the data model:

| Field | Type | View | Populated for |
|---|---|---|---|
| `BrokerTrade.side` | `OrderSide` | per-execution | every BrokerTrade |
| `TradeRecord.entry_side` | `OrderSide` | open-event view | every TradeRecord |
| `TradeRecord.exit_side` | `OrderSide` | close-event view | every TradeRecord |
| `Position.direction` | `OrderDirection` | position view | every Position |
| `TradeRecord.direction` | `OrderDirection` | position view (kept for aggregate views) | every TradeRecord |
| `OrderResult.action` | `OrderAction` | lifecycle (OPEN/CLOSE) — not a side concept | every OrderResult |

**Where each is rendered:**

| Surface | Column | Value source |
|---|---|---|
| Live OPEN POSITIONS panel | `Dir` | `Position.direction.value` → `long`/`short` |
| Live TRADE HISTORY panel | `Side` | `TradeHistoryEntry.exit_side.value` → `buy`/`sell` (color stays by direction) |
| Sim trade-history log | `Side` | `TradeRecord.exit_side.value` → `BUY`/`SELL` (color by direction) |
| events.csv FILL rows | `side` column | `BrokerTrade.side.value` → `buy`/`sell`. `direction` column empty. |
| events.csv POSITION_OPEN/CLOSE rows | `direction` column | `TradeRecord.direction.value` → `long`/`short`. `side` column empty. |

The events.csv treats `side` and `direction` as **mutually exclusive per row** — FIX-style separation of OrdSide (per-trade operation) from PositionSide (position view). A FILL is an execution operation; a POSITION_OPEN/CLOSE is a position-view aggregate.

**Color-by-direction, text-by-side** convention in the Live panel keeps the visual link to "what kind of position is this trade affecting?" (green = closed-LONG, red = closed-SHORT) while the text accurately labels the algo operation (BUY/SELL).

## Cardinality Independence — Entry vs Exit

The number of `entry_trades` and `exit_trades` on a TradeRecord are unrelated to each other:

| Entry cardinality | Exit cardinality | Scenario |
|---|---|---|
| 1 | 1 | Single MARKET open, single MARKET close |
| N | 1 | Multi-fill entry (Kraken matched 3 counterparties), single close |
| 1 | N | Single entry, exit broker-split into N (e.g. partial-fill on close LIMIT) |
| N | M | Multi-fill on both sides |

In V1.3 sim and current Kraken live, both lists are 1-element. The data model is ready for #143 (order-book sim) and #342 (Kraken partial-fill detection) — both will populate N-element lists without further data-model changes.

## Propagation Points

| Path | Where | Detail |
|---|---|---|
| `pending.trades` ← synthesize | `AbstractTradeExecutor._synthesize_pending_trade` | Single BrokerTrade with `trade_id=f'SYNTH-{pending_order_id}-{seq:06d}'`. Monotonic per-executor counter prevents id collisions across open / partial closes. `BrokerTrade.side` derived via `direction_to_side(pending.direction, OrderAction.OPEN/CLOSE)` from the pending's lifecycle action. |
| `TradeRecord.entry_side` / `exit_side` | `PortfolioManager._create_trade_record` + `partial_close_position` | `direction_to_side(position.direction, OPEN)` and `direction_to_side(position.direction, CLOSE)` — same helper, single source of truth. |
| `Position.entry_trades` ← shallow copy | `AbstractTradeExecutor._fill_open_order` → `portfolio.open_position(entry_trades=...)` | `list(pending.trades)` |
| `TradeRecord.entry_trades` ← shallow copy | `PortfolioManager.partial_close_position` + `_create_trade_record` | `list(position.entry_trades)` (same list on every derived record) |
| `TradeRecord.exit_trades` ← shallow copy | `AbstractTradeExecutor._fill_close_order` → portfolio methods | `list(pending_order.trades)` (distinct per close event) |
| `Position.broker_ref` ← propagated | `_fill_open_order` → `portfolio.open_position(broker_ref=...)` | From `pending.broker_ref`. Future consumer: Reconciler (#151) |

## Renderings

### Sim trade-history log

`docs/../python/framework/reporting/console/trade_history_summary.py` emits the aggregate row plus one sub-line per `BrokerTrade` in `entry_trades` / `exit_trades`:

```
   # |  Dir  | ET |  Lots |  Entry Price |   Exit Price | ... | Net P&L | Close Reason
   ───────────────────────────────────────────────────────────────────────────────────
   1 | LONG  |  M |  0.01 |     100.0000 |     101.0000 | ... |  +9.99  |
     └─ in   T-E001  vol 0.03000  price 100.0000  fee 0.05  taker  shared(3x)  (this trade: 0.01 of 0.03)
     └─ out  T-X001  vol 0.01000  price 101.0000  fee 0.03  taker
   2 | LONG  |  M |  0.01 |     100.0000 |     102.0000 | ... | +19.99  |
     └─ in   T-E001  vol 0.03000  price 100.0000  fee 0.05  taker  shared(3x)  (this trade: 0.01 of 0.03)
     └─ out  T-X002  vol 0.01000  price 102.0000  fee 0.03  taker
```

- `shared(Nx)` — set when the same `trade_id` appears on N TradeRecords in this scenario
- `(this trade: X of Y)` — appears only on shared entries; clarifies the partial-close share vs the original execution volume

### Live Display panels

Compact — sub-rows fire only when non-trivial:

- **OPEN POSITIONS** — extra sub-row when `len(entry_trades) > 1` (multi-fill entry)
- **TRADE HISTORY** — per-side sub-row when entry or exit has > 1 fill
- **TRADE HISTORY Reason column** — when the underlying `close_reason` is MANUAL (empty string), the renderer surfaces the partial nature:
  - `PARTIAL` close_type → `partial`
  - `FULL` close_type where `entry_trade.volume > trade.lots` (= remainder of a partial chain) → `remain`
  - `FULL` standalone (`entry_trade.volume == trade.lots`) → no marker
  - Non-MANUAL reasons (`sl_triggered`, `tp_triggered`, `scenario_end`) → keep the real reason value; the partial nature is then visible via the lots column (`lots < original_lots`)
- **ORDERS** — three-level rendering for LIMIT (Trigger → BrokerOrder → Fills) is wired but stays dormant until #342 surfaces real `PARTIALLY_FILLED` state from the Kraken parser

In V1.3 multi-fill data does not yet flow from the broker side, so most sub-rows stay invisible. The display path is ready the moment the data does.

The TRADE HISTORY column header reads **`Side`** (not `Dir`) — the value is the close operation (`buy` or `sell`), color-coded by the underlying position direction (green for closed-LONG, red for closed-SHORT). See the "Trade-Event Side vs Position Direction" section for the full rationale.

## Event-Stream CSV (`events.csv`)

Replaces the previous two-file format (`autotrader_orders.csv` + `autotrader_trades.csv`). Long-format / FIX-`ExecutionReport`-style — one row per event, `event_type` as discriminator. One file per AutoTrader session, one per scenario in sim (`events_<scenario>.csv` inside an `events/` subfolder of the scenario-set log dir).

Canonical column order ([event_stream_csv_writer.py:EVENT_FIELDS](../../python/framework/reporting/event_stream_csv_writer.py)):

```
ts, event_type, order_id, position_id, trade_id,
broker_ref, direction, side, lots, price, fee, fee_currency,
status, close_type, close_reason, is_maker, notes
```

`direction` and `side` are mutually exclusive per row (see "Trade-Event Side vs Position Direction" above):
- POSITION_OPEN / POSITION_CLOSE rows carry `direction` (long/short), `side` is empty
- FILL rows carry `side` (buy/sell), `direction` is empty
- ORDER_SUBMIT / CLOSE_SUBMIT / ORDER_REJECT rows carry neither (algo lifecycle markers, no per-execution payload)

### Event Types

| Event | When | Source |
|---|---|---|
| `ORDER_SUBMIT` | Algo sent an OPEN trigger | `order_history` walk (`action=OPEN`) |
| `ORDER_REJECT` | Broker / guard rejected pre-submit | `order_history` walk |
| `CLOSE_SUBMIT` | Algo sent a CLOSE trigger (one per TradeRecord) | `trade_history` walk — 1:1 with TradeRecord |
| `FILL` | One `BrokerTrade` (atomic execution) | `entry_trades` + `exit_trades` on each TradeRecord |
| `POSITION_OPEN` | `_fill_open_order` finalized | First TradeRecord of a `position_id` in trade_history |
| `POSITION_CLOSE` | `_fill_close_order` finalized | Every TradeRecord |

### Why CLOSE_SUBMIT comes from trade_history, not order_history

OrderResults for opens and closes share the same `order_id` by design (= `position_id`). If CLOSE_SUBMIT were keyed on `(order_id, action='close')` and emitted from `order_history`, three partial closes of the same position would collapse to one CLOSE_SUBMIT. Building CLOSE_SUBMIT from `trade_history` gives a clean 1:1 mapping per close event regardless of position re-use.

### First-class fields vs the metadata bag on OrderResult

The genuine order dimensions are typed first-class fields (`action` since #330; `symbol`,
`direction`, `requested_lots`, `close_type` since #343) — consumers read `order.symbol`
instead of `order.metadata.get('symbol')`, and presence is consistent across all
construction sites. The writer routes ORDER_SUBMIT vs CLOSE_SUBMIT on `order.action`.

`metadata` retains only diagnostic / order-type-specific keys: `fee_cost`, `fee_type`,
`fill_type`, `submitted_at_tick`, `filled_at_tick`, `realized_pnl`, `awaiting_fill`,
`broker_ref`, `reason` (EXPIRED), `order_type` (EXPIRED), `limit_price`, `stop_price`.

Construction sites are inventoried at:

- `live_trade_executor.py` — 3 sites (MARKET open, LIMIT open, close)
- `trade_simulator.py` — 5 sites (MARKET, LIMIT, STOP, STOP_LIMIT opens + close)
- `abstract_trade_executor.py` — 4 sites (EXECUTED open, EXECUTED close, EXPIRED limit, EXPIRED stop)

## File Locations

| File | Role |
|---|---|
| `python/framework/types/portfolio_types/portfolio_types.py` | `Position.broker_ref`, `Position.entry_trades` |
| `python/framework/types/portfolio_types/portfolio_trade_record_types.py` | `TradeRecord.entry_trades`, `TradeRecord.exit_trades` |
| `python/framework/trading_env/abstract_trade_executor.py` | Propagation at `_fill_open_order` + `_fill_close_order`; `_synthesize_pending_trade` |
| `python/framework/trading_env/portfolio_manager.py` | Signature extensions on `open_position`, `close_position_portfolio`, `partial_close_position`, `_create_trade_record` |
| `python/framework/reporting/event_stream_csv_writer.py` | `EventStreamWriter`, `EventType` enum, `TradeEvent`, `EVENT_FIELDS` |
| `python/framework/reporting/console/trade_history_summary.py` | Sim sub-line renderer + `shared(Nx)` + `(this trade: X of Y)` |
| `python/system/ui/autotrader_live_display.py` | Live panel sub-rows + `partial` / `remain` reason markers |
| `python/framework/autotrader/autotrader_main.py` | AutoTrader-side `EventStreamWriter.from_autotrader_result(...).flush('events.csv')` |
| `python/framework/batch/batch_report_coordinator.py` | Sim-side per-scenario loop emitting `events_<scenario>.csv` into `events/` subfolder |

## Latent for Future Work

- `ActiveOrderSnapshot.cumulative_filled_lots` / `requested_lots` — fields defined, ORDERS panel BrokerOrder sub-row conditional. Activates when #342 (Kraken `PARTIALLY_FILLED` parser) surfaces real partial state
- `Position.broker_ref` consumption by the Reconciliation Layer — owned by #151
- MT5 multi-deal entry executions — feed into `entry_trades` naturally once #209 lands
