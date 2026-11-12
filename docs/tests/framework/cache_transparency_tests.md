# Cache Transparency Tests

`tests/framework/cache_transparency/` — nothing an algo can see depends on the portfolio's lazy
marking, and what an algo receives is a read-only algo mask or a frozen value (#653, #654).

Run: `python -m pytest tests/framework/cache_transparency/ -v`
Launch entry: `🧩 Pytest: Cache Transparency (All)`

---

## What this suite exists to catch

The portfolio marks its open positions lazily to keep the tick loop fast, and that is only safe while
no algo can tell. Until the algo masks it could: at spot a decision read a position's price and P&L
as they stood at the last extreme, and a position a fill had just opened read a price of `0.0` in its
own fill hook. No test saw it, because no strategy of ours read those fields. This suite looks from
the algo's side instead of the strategy's: an observer probe records everything the decision trading
API hands out, on every pass, and the record must not depend on the cache.

| Failure | What it costs |
|---|---|
| A value an algo reads differs with the cache on and off | A strategy decides on a stale or unmarked value, and its backtest differs from its live session by construction |
| A position read in a session is not marked at its tick | The live side reads what the backtest does not |
| A public method of the decision API is neither declared nor observed | A new read reaches algos unchecked |
| A read says it hands out a mask, a frozen value or a plain value and its return type is something else | An engine object reaches the algo through a read nobody looked at |
| A mask can be written to, or hands out an engine object | An algo changes engine state past the order guard and, live, the venue |
| A worker can change the tick or a bar another worker reads | One worker's write changes what the next one computes on, in an order nobody chose |
| A mask's documented fields differ from the mask | The page an algo author reads describes another contract |

Not here: the cost of the lazy marking and of the masks — those are the cost probes
`python/experiments/cost_probes/probe_portfolio_cache_cost.py` and
`python/experiments/cost_probes/probe_algo_tick_bar_cost.py`.

## The tests

### `test_cache_transparency.py`

The first scenario of a backtesting fixture, traded by the observer probe — an open on a tick, an
open on a heartbeat, a partial close, two closes — runs with `verification.portfolio_cache` on and
off, in this process (DEBUG_MODE). The two records must be equal field by field; a failure names the
first pass and field.

| Test | Description |
|------|-------------|
| `TestTheFixturesAreWorthComparing` | at spot and at margin the plan opened two positions, one at a heartbeat, closed one in part and both in full; it rested a limit far from the market, kept its mask and cancelled it; every event carries a readable result |
| `TestNothingADecisionSeesDependsOnTheCache` | at spot and at margin the two records are equal |

### `test_cache_transparency_session.py`

A session is not reproducible pass by pass — its fills run on the wall clock — so the live side holds
a property: on every tick pass every open position the probe reads, through the API or kept from an
earlier pass, shows the price it would close at on that tick. Mock sessions at spot, state frames on.

| Test | Description |
|------|-------------|
| `TestTheSessionsAreWorthChecking` | positions were held on more than a hundred tick passes |
| `TestEveryReadIsMarkedAtItsTick` | with the cache off and with it on, no read lags its tick |

### `test_algo_position.py`

The mask against a real portfolio, at spot and at margin.

| Test | Description |
|------|-------------|
| `TestTheMaskIsReadOnly` | every assignment raises; no instance dictionary; only values come out |
| `TestTheMaskIsCurrentWhenRead` | after every quote the lazy portfolio's mask equals what a portfolio marking at every event stores; a mask kept across quotes follows them |
| `TestOneMaskPerPosition` | the same mask on every read; none for a position not open; the symbol filter |
| `TestAClosedPositionShowsWhatItStored` | a mask held past the close shows the values the position closed with |

### `test_decision_api_declaration.py`

| Test | Description |
|------|-------------|
| `TestTheDeclarationIsComplete` | every public method of the decision trading API is declared as reading or acting, nothing is declared the class lacks, and both kinds occur |
| `TestTheProbeObservesEveryRead` | the observer probe calls exactly the declared reading methods |
| `TestEveryReadSaysWhatItReturns` | every reading method declares what it hands out — a mask, a frozen value, a copy made for the call or a plain value — and its return type agrees |

### `test_algo_order_result.py`

| Test | Description |
|---|---|
| `TestTheResultMask` | the mask shows the outcome and a refusal; a close that joined another says so; the engine's bookkeeping — metadata, submission, order-event sequence — is not shown; an assignment raises and changes nothing; a decision event carries the mask |
| `TestFrozenValues` | the order capabilities and the market data status refuse every assignment |

### `test_algo_market_data.py`

What a worker and the decision receive of the market (#654): the tick and the forming bars as masks,
the history as tuples of frozen bars. The orchestrator is driven by a real bar rendering controller.

| Test | Description |
|---|---|
| `TestTheTickMask` | the mask shows the market fields and not the engine's own (receipt time, clipping); an assignment raises and changes nothing; a logic makes one mask per tick |
| `TestTheBars` | a closed bar refuses every assignment; a forming bar has a closed bar's fields; the history holds a frozen copy of each closed bar, which the renderer's old bar no longer reaches; the history cannot be appended to; warmup bars enter complete while the handed-in ones stay as they were; the forming bar's mask reads the bar as it moves and refuses assignments |
| `TestCopies` | a tick mask is its own copy; a forming bar mask copies as the bar as it stands, a frozen bar; any other mask refuses a copy or a pickle with a reason |
| `TestWhatTheWorkersReceive` | the workers and the decision receive the same tick mask; the forming bar arrives as one mask until its period closes; no worker can change the forming bars another receives, and the history is a tuple of frozen bars |

### `test_algo_mask_docs.py`

| Test | Description |
|------|-------------|
| `TestEveryMaskIsListed` | every mask class in the masks' directory is in the documented list |
| `TestEveryMaskIsDocumented` | each mask's table in `docs/user_guides/algo_masks.md` lists exactly its fields, each once |
