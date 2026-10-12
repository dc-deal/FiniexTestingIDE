# Broker Trade Record Model — Order ↔ Executions Pairing

> See [Trade Execution Visibility](trade_execution_visibility.md) for how `BrokerTrade` records
> propagate through `Position.entry_trades` / `TradeRecord.entry_trades` / `exit_trades`, how the
> renderers surface them, and the long-format event-stream CSV.

## Concept

Every order placed at a broker eventually produces one or more **executions** (also called trades,
fills, or deals). The order is the instruction; executions are the realizations. The relationship is
always 1:N — one order, many executions — and is universal across institutional and retail brokers
(FIX ExecutionReports, IBKR `execDetails`, Binance `myTrades`, Kraken `QueryTrades`, MT5 deals).

This project models that pairing via the `BrokerTrade` domain type. The data model is broker-agnostic; transport details live in each adapter's Tier-3 `build_trades_query_payload` / `do_request_trades_query` / `parse_trades_query_response` triple.

```
ORDER (the instruction)
  │
  ├─── EXECUTION #1   vol=0.04 @ 100.10  fee=0.001
  ├─── EXECUTION #2   vol=0.03 @ 100.15  fee=0.0008
  └─── EXECUTION #3   vol=0.03 @ 100.20  fee=0.0008

cumulative_filled_lots = 0.10
cumulative_avg_price   = 100.146   (weighted)
cumulative_fee         = 0.0026
```

## Domain Type

[BrokerTrade](../../python/framework/types/trading_env_types/broker_trade_types.py):

| Field | Meaning |
|---|---|
| `trade_id` | The venue's execution ID on an execution the venue reports (Kraken tradeid, MT5 deal ticket); ours, `SYNTH-<order_id>-<n>`, on the execution recorded on an order when its fill is booked |
| `parent_broker_ref` | Parent order's broker_ref (Kraken txid, MT5 order ticket) |
| `order_id` | OUR internal order_id — primary routing key |
| `volume` | Lots filled in this execution |
| `price` | Price of this execution |
| `fee` | Fee for this execution — the venue's own on a venue execution; on a synthesized one, the fee the fill was booked with |
| `fee_currency` | Fee currency (e.g. 'USD', 'EUR') |
| `timestamp` | UTC, timezone-aware execution time |
| `side` | BUY / SELL — the execution's trading side, not the position's direction |
| `is_maker` | True for LIMIT/maker fills, False for MARKET/taker |

`PendingOrder` (see [latency_simulator_types.py](../../python/framework/types/trading_env_types/latency_simulator_types.py)) carries the executions:

```python
trades: List[BrokerTrade]
cumulative_filled_lots: float
cumulative_fee: float
cumulative_avg_price: float
```

The helper `pending.append_trade(trade)` mutates `trades` and recomputes the cumulative aggregates in lock-step. Consumers read the cumulative fields directly without recomputing on every access.

## Adapter Tier-3 Contract

Three methods on `AbstractAdapter` — every live-capable adapter implements them:

```python
def build_trades_query_payload(broker_ref: str) -> Dict[str, Any]
def do_request_trades_query(payload: Dict[str, Any]) -> Dict[str, Any]
def parse_trades_query_response(raw, broker_ref, order_id) -> List[BrokerTrade]
```

Pure / transport / pure layering, identical to the submit/query/cancel/modify triples established by #319.

The `OrderCapabilities.trade_level_reporting` flag (default True) declares whether the venue
answers a trades query with per-execution detail. The drift auditor reads it: it asks for the
executions after every fill only where the venue can answer. Kraken declares True; MT5 declares
False until its deal layer exists (#209), because the query would reach a method that raises. The
books never depend on it — every fill carries one record synthesized where it is booked.

### Kraken Implementation

Kraken's REST API requires a two-call pattern:

1. `POST /0/private/QueryOrders` with `trades=true` → returns the order detail plus a `trades: [tradeid, ...]` list
2. `POST /0/private/QueryTrades` with comma-separated `txid=` → returns full per-trade detail

The Kraken adapter encapsulates both calls inside `do_request_trades_query`. Dry-run orders (DRYRUN-* refs) bypass the broker and return an empty list — a documented limitation, since dry-run orders never produce real executions.

### Mock Implementation

The `MockBrokerAdapter` records synthetic trade records at fill time (in `do_request_submit`
INSTANT_FILL and `do_request_query` DELAYED_FILL paths) via the `_record_mock_trades` helper. The
constructor parameter `trades_per_fill: int = 1` controls how many records are produced per fill:

- `1` (default): single full-volume trade — typical real-world fill
- `N > 1`: split into N records with even volume and small price offsets — exercises partial-fill code paths in regression tests

Each synthesized trade gets a `MOCK-TRADE-XXXXXX` ID and a quote-currency fee — at the rates the
broker configuration declares, or at the mock venue's own (`set_venue_fee_rates`), which is how a
test plays an account whose real tier is not the declared one. A spread broker's executions carry
no fee: it charges nothing per fill. The answer that reports the fill carries the fill's total fee
as `fill_fee`, like Kraken's; `set_fee_in_status_answer(False)` sends it without, for the fallback.

## Drain-Layer Distribution (Post-Drain Anchor)

Reference: #326, #508.

```
fill booked (entry: outcome listeners · close: close-fill listeners)
        │
        ▼
DriftAuditor → submit_trades_query_async(order_id, broker_ref)
        │
worker thread: HTTP / RPC trades_query → parse → push TradesQueryResponse
                                                          │
                                                          ▼
drain_inbox      ←──── routes to _trades_response_hook
                                                          │
                                                          ▼
LiveTradeExecutor._handle_trades_response(response):
  └─ fan-out to every registered consumer           (books NOTHING)
        └─ DriftAuditor._on_trades_response         (compares with what was booked)
```

The executions arrive after the fill has been booked, and they are evidence about that booking,
never a second booking route. The drain used to append them to a still-active order and book the
order from their total — a path only tests reached, and one that would have booked a protective
order's partial fill twice once anything asked about the order while it still worked.

## Sim vs. Live — Same Algo-Facing Contract

| | Sim (TradeSimulator) | Live (LiveTradeExecutor) |
|---|---|---|
| Emission trigger | `_fill_open_order` / `_fill_close_order` (shared) | `_fill_open_order` / `_fill_close_order` (shared) |
| Data source | Synthesized from local price + fee model | Synthesized from the answer that reported the fill — with the venue's own fee where it reports one (#508) |
| The venue's real executions | n/a — sim is its own truth | Read by the drift auditor after every fill, entry and exit; never written onto the order |

Both pipelines route through `AbstractTradeExecutor._synthesize_pending_trade` when `pending.trades` is empty at fill time. Consumers (`portfolio`, `order_history`, listeners) see the same shape in both pipelines.

## Where a live fill's fee comes from

A live fill is booked once, from the answer that reports it — and with the fee that answer carries,
where the venue reports one there (#508). No second figure follows, so nothing is corrected
afterwards. The fee model still decides WHETHER a fill is charged and under which category; the
venue decides how much. Every executed result and order event says which it was: `fee_source` is
`venue` or `estimate`.

Where a venue reports the fee is declared on the adapter, `OrderCapabilities.fee_report`:

| `fee_report` | Where the fee is | Booked from |
|---|---|---|
| `status_answer` | in the answer that reports the order filled — Kraken's QueryOrders `fee` | the venue's figure |
| `executions` | only in the order's executions, after the fill — a broker that sends its commission in a report of its own (#210), or keeps it on the deal in its history (MT5's `DEAL_COMMISSION`; #209 measures where it appears before declaring) | not built: it needs the order held open until that report, or the estimate corrected afterwards |
| `not_reported` | nowhere per fill — a spread broker, whose cost is inside the fill price | the estimate |

A dry run declares what its venue does. Its answers come from our own simulator and carry no fee,
so its fills are booked from the estimate — without the warning a real answer missing its fee gets.

Measured on Kraken, 2026-10-11 (`python/experiments/venue_probes/probe_kraken_fee_at_fill.py`, real
orders): the order's total `fee` is in the first QueryOrders answer that says closed and does not
change afterwards; it equals the sum of the order's trade fees; the ledger debits it in the quote
currency on both sides, rounded to that asset's four decimals — under 0.0001 USD per fill. Every
AddOrder therefore sends `oflags=fciq`: the spot books charge the fee in the quote, and Kraken's
documentation names the base currency as the default when selling.

What stays derived is the CATEGORY. The venue states maker or taker per execution only, never on
the order, so a fill's fee is filed under the category its fill type implies — a market order or a
close is a taker, a resting limit a maker. A limit that crossed the book on arrival is charged the
taker rate and filed as a maker; the amount is still the venue's. Over the fifteen orders measured
on 2026-10-11 the venue's own flag agreed every time.

**The fallback is visible.** A fill on a venue that declares `status_answer` whose answer arrives
without a fee is booked from the estimate, its event says `estimate`, and the session's log warns —
"we estimated" and "the venue said" are different facts about money. A venue reporting a fee where
the configured model charges none per fill is warned about and NOT booked: the configuration does
not describe that venue.

**A protective order the venue holds** is reported as totals for everything it executed — the
volume, its average price, its fee — while it is booked in steps. Each step books what each total
grew by since the last one: its own volume, at its own price, with the fee the total grew by. The
counters (`venue_close_applied_lots`, `_fee`, `_value`) follow the booking, a step booked from
the estimate included, so the venue's next total settles it rather than paying it twice. The
position carries a copy into the cold-start carry-over, so a restart books only what is new.
Where an estimate exceeded the venue's later total, that step books no fee and reverses nothing:
the excess stays booked, a later total above the books absorbs it, and only an order whose last
total stays below them ends above the venue's figure. The session log warns, and the step's event says so in its `message` — as does the
event of a step that settles an estimate. The part booked from the estimate is counted in-session
only (`venue_close_estimated_fee`), so after a restart a settling step books the same figure
without its sentence.

**The drift auditor checks both legs.** After every fill — an entry, and since #508 a close as
well — it asks for the order's executions and compares them with what was booked. With the fee
now the venue's, its FEE channel compares two of the venue's own figures, the way the PRICE
channel always has, and that is what keeps measuring on every real fill that the fee really is
complete in the first answer.

## Out of Scope

- **Push-based trade streams** (Kraken WS `ownTrades`, MT5 EA `OnTradeTransaction`) — the data model supports it (push events arrive via inbox, drain routes the same way), but the wiring is a future enhancement.
- **Position-level trade aggregation across multiple orders** — `pending.trades` is per-order. Cross-order position-level trade summaries are a V1.4+ concern.
- **Historical backfill** — fetching old trades after session restart is reconciliation territory (#151).

## Tests

| File | Scope |
|---|---|
| [tests/autotrader/live_executor/test_broker_trade_records.py](../../tests/autotrader/live_executor/test_broker_trade_records.py) | append_trade aggregation, async path roundtrip via processor, multi-trade mock |
| [tests/simulation/trade_emission/test_trade_emission.py](../../tests/simulation/trade_emission/test_trade_emission.py) | Sim `_fill_open_order` / `_fill_close_order` synthesis on the shared abstract path |
| [tests/parity/test_trade_records_parity.py](../../tests/parity/test_trade_records_parity.py) | Sim/live agreement on post-fill state (position, history, synthesis shape) |

## Related Issues

- **#319 LiveRequestProcessor Core Refactor** — provides the Tier-3 layer pattern, async worker dispatch, multi-listener fan-out
- **#318 Async Modify/Cancel** — same async pattern adapted to modify/cancel/position-modify
- **#327 Local-vs-Broker Drift Audit** — direct consumer of `pending.trades` and `cumulative_fee`
- **#320 Polling Cadence Management** — partial-fill bookkeeping delegated to this issue's data model
- **#151 Reconciliation Layer** — uses trade-level granularity for state reconciliation
- **#209 MT5 Live Adapter** — implements the same Tier-3 trades_query triple against `HISTORY_DEALS_GET`
