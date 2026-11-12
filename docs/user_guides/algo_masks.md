# Algo Masks: What an Algo Receives and Where It Comes From

A decision logic that reads a position's `unrealized_pnl` should get the position's P&L at this
moment, the same in a backtest and in a live session — and it should not be able to change the
engine's position by assigning to it. Neither held while the decision trading API handed out the
engine's own `Position` objects: at spot the engine marks a position only when its price passes an
extreme, so the stored price and P&L lagged the market, and a position a fill had just opened read a
price of `0.0` in the fill's own hook (#653). This page says what an algo receives instead, which
fields it shows, and where each value comes from.

Not here: what an algo can *call* — the methods of the decision trading API and the hooks are
described in the [Execution Layer](../architecture/architecture_execution_layer.md) and the
[Quickstart](quickstart_guide.md).

## Two levels: what an algo calls, and what it receives

An algo — a decision logic and its workers — meets the framework on two levels:

- **the functional level** — the methods it calls and the hooks it subscribes to;
- **the mask** — what those methods hand back. An *algo mask* shows a declared set of fields of one
  engine object, computes what moves with the market when it is read, and never writes. The algo
  never holds the engine's object.

The mask is a contract in both directions. A field added to an engine type reaches no algo until a
mask declares it, so the engine can grow without changing what a strategy sees. And nothing an algo
holds can change the engine's state: assigning to a mask raises `AttributeError`.

```
algo ──calls──► DecisionTradingApi ──► executor (backtest or live) ──► portfolio
algo ◄─masks─── AlgoPosition     ◄── made once per position, computes on every read
                AlgoOrder        ◄── made once per resting order, reads the order on every read
                AlgoOrderResult  ◄── one per order result handed out, and in every decision event
```

The same masks serve the backtest and a live session: the decision API, the executor's base and the
portfolio are shared by both pipelines.

## `AlgoPosition` — an open position

`get_open_positions()` and `get_position()` return one `AlgoPosition` per open position.

| Field | Where it comes from |
|---|---|
| `position_id` | stored when the position opened — the identifier `close_position` and `modify_position` take |
| `symbol` | stored at entry |
| `direction` | stored at entry — LONG or SHORT |
| `status` | stored — open, partially closed or closed |
| `lots` | stored — the lots still open, reduced by a partial close |
| `original_lots` | stored at entry |
| `entry_price` | stored at entry |
| `entry_time` | stored at entry, on the canonical clock |
| `entry_type` | stored at entry — a market order, a limit or a stop |
| `stop_loss` | stored — the level the order set or `modify_position` changed, `None` when unset |
| `take_profit` | stored — as `stop_loss` |
| `comment` | stored — the opening order's comment |
| `pip_size` | stored from the symbol's specification |
| `digits` | stored from the symbol's specification |
| `contract_size` | stored from the symbol's specification |
| `price_unit` | stored from the symbol's specification |
| `entry_bid` | stored — the bid at entry |
| `entry_ask` | stored — the ask at entry |
| `entry_tick_value` | stored — the value of one point in the account currency at entry |
| `current_price` | computed when read — the price it would close at now: the latest bid for a LONG, the latest ask for a SHORT; `None` until its symbol's first quote |
| `gross_pnl` | computed when read — the price difference in points × the tick value × the lots; `None` until its symbol's first quote |
| `unrealized_pnl` | computed when read — `gross_pnl` minus every fee charged so far; `None` until its symbol's first quote |
| `total_fees` | computed when read — every fee so far; at margin the swap is booked up to the canonical clock first |
| `mae_pnl` | computed when read — the worst `gross_pnl` of the position's life, the latest quote included |
| `mae_price` | computed when read — the close price at that worst point |
| `mfe_pnl` | computed when read — the best `gross_pnl` of the position's life, the latest quote included |
| `mfe_price` | computed when read — the close price at that best point |

The computed fields use the formula the engine marks with, so a mask and a report never disagree. A
mask is made on the first read of its position and handed out again on every later read; a mask an
algo keeps across passes stays current, because it computes on every read. A closed position's mask
shows the values the position closed with.

## `AlgoOrder` — a resting order

What `get_active_orders` hands out: one mask per limit or stop order resting in the engine. The
engine keeps changing a resting order — a modify moves its price, a partial fill at a venue lowers
what is left, an operation marks it while in flight — so every field is read from the order when
the algo reads it. A mask is made on the first read of its order and handed out again while the
order rests; one the algo keeps after the order filled, was cancelled or expired shows the values
the order ended with.

| Field | Where it comes from |
|---|---|
| `pending_order_id` | stored when the order was placed — the identifier `modify_limit_order`, `modify_stop_order` and the cancels take |
| `order_action` | stored — whether the order opens a position or closes one |
| `order_type` | stored — LIMIT, STOP or STOP_LIMIT |
| `symbol` | stored |
| `direction` | stored — the direction of the position it opens |
| `lots` | read — the lots still working; a partial fill at a venue lowers it |
| `filled_lots` | read — the lots already executed of it, `0` while nothing filled |
| `entry_price` | read — the price it rests at: a limit's limit price, a stop's trigger; a modify moves it |
| `limit_price` | read — a stop-limit's limit price, which applies once its trigger is reached; `None` for the others |
| `stop_loss` | read — the stop loss the position it opens will carry, `None` when none was asked for |
| `take_profit` | read — as `stop_loss` |
| `entry_time` | stored — when the order was placed, on the canonical clock |
| `in_flight_operation` | read — a submit, modify or cancel still on its way, `NONE` in steady state; while a modify is in flight the fields above still show the values it would replace |

## `AlgoOrderResult` — an order's outcome

What `send_order` and `close_position` return, and the `result` of every decision event. The
engine keeps each booked result in its order history and hands the same result to every listener,
so the algo receives this mask in its place. A result does not change once it is booked; the
engine's own bookkeeping on it — the submission's details, its free metadata and its order-event
sequence number — is not shown.

| Field | Where it comes from |
|---|---|
| `order_id` | stored — the order's identifier |
| `status` | stored — pending, executed, denied, rejected, cancelled or expired |
| `is_success` | derived — whether the order executed |
| `is_refused` | derived — whether it was refused, here (denied) or by the venue (rejected) |
| `executed_price` | stored — the price it executed at, `None` while nothing executed |
| `executed_lots` | stored — the lots executed, `None` while nothing executed |
| `execution_time` | stored — when it executed, on the canonical clock |
| `commission` | stored — the fee it was charged, in the account currency |
| `fee_source` | stored — whether the fee is the venue's own figure or the model's estimate |
| `rejection_reason` | stored — why it was refused, `None` where it was not |
| `rejection_message` | stored — the refusal in words, `''` where it was not refused |
| `position_id` | stored — the position it opened or closed |
| `action` | stored — whether it opened, closed or partly closed a position |
| `symbol` | stored |
| `direction` | stored — LONG or SHORT; a close's is the closed position's |
| `requested_lots` | stored — the lots the order asked for |
| `close_type` | stored — for a close, whether it closed the position in full or in part |
| `order_type` | stored — the type the order was asked as |
| `initiator` | stored — who ended the order, where it ended other than by a fill |
| `end_reason` | stored — why it ended, where it ended other than by a fill |
| `joined_in_flight_close` | stored — for a close asked while another close of the same position was already on its way: the request joined that close and describes it, nothing new was sent |

## What a mask guarantees, and where it stops

- **Read-only.** Every assignment raises; a mask has no instance dictionary to add to.
- **Values only.** Numbers, text, enums, times — never the engine's object or one of its lists.
- **Current when read**, in a backtest and in a live session alike. Before the first quote of the
  position's symbol — a book restored at a cold start, read before the first tick — the price and
  the P&L are unknown and read `None`, never a placeholder `0.0`; the excursion fields show their
  recorded history. The cache-transparency suite
  proves it: the same backtest with `verification.portfolio_cache` on and off gives the observer
  probe identical records, field by field, and in a session every position read shows the price of
  the tick it is read on ([Cache Transparency Tests](../tests/framework/cache_transparency_tests.md)).
- **Paid only when read.** Measured on the development machine (2026-11-05, load about 7, the cost
  probe `python/experiments/cost_probes/probe_portfolio_cache_cost.py`): a mask is made once per
  position in about 0.4 µs; a stored field reads in about 45 ns; a computed field — price, P&L,
  excursion, fees — runs the full valuation, about 1.7 µs. An algo that reads only identifiers and
  directions pays nothing for the valuation.
- **Not a lock against intent.** Python has no true privacy: an algo that deliberately reaches through
  the mask's private reference still reaches the engine object, and with it every guard the read-only
  contract stands for. That is unsupported; the mask protects against accidents and against an engine
  field leaking unreviewed.

`verification.portfolio_cache` (`execution_config` in a scenario set, `execution` in an AutoTrader
profile) is the lazy marking at `true`, the default; `false` keeps every position current at every
event instead. A strategy's results must not depend on it — run one with it off to check your own algo.

## What is masked so far

Every read of the decision trading API hands the decision one of four kinds of thing, and the API
declares which beside each method (`DECISION_API_READS_RETURN`), held to the method's return type by
a test:

- **a mask** — positions, resting orders, order results;
- **a frozen value** — the symbol's specification, the order capabilities and the market data
  status: the engine replaces such a value rather than changing it, and assigning to one raises;
- **a copy made for the call** — the account info, the cost breakdown, the active order counts;
- **a plain value** — a number, a flag, a text, an enum or a time.

The decision events are frozen too, and their `result` is an `AlgoOrderResult`. A worker's ticks and
bars follow (#654), decided by a measurement first, because they are the hottest path.
