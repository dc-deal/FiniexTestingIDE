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
algo ◄─masks─── AlgoPosition  ◄── made once per position, computes on every read
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

Positions are the first mask. The other things the decision API and a worker receive — active
orders, order results and the `result` in the decision events, order capabilities, the market data
status, and a worker's ticks and bars — follow in #654, each with its own table on this page.
