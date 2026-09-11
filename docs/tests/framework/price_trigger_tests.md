# Price Trigger Tests

`tests/framework/test_price_trigger.py` — the shared order-vs-quote predicate
(`python/framework/utils/trading_math/price_trigger.py`, §45). Runs under the synthetic
`framework/_root` suite.

## Why this file exists at all

The module has two callers and both exercise it: the simulation executor asks it whether a
resting order fills, and the dry-run simulator asks it the same question from inside an
adapter's parse layer. Between them all four direction × type combinations are covered — which
tells you the CALLERS work, not what the module promises. This file pins the promise in one
place, including a boundary no caller happens to hit.

## What Is Tested

### `TestALimitWaitsForABetterPrice`

A limit never fills worse than its own price. A buy sits below the ask and fills when the ask
falls to it; a sell sits above the bid and fills when the bid rises.

| Test | Description |
|------|-------------|
| `test_a_buy_limit_reads_the_ask` | above / exactly at / below the ask — parametrized, including the AT case |
| `test_a_sell_limit_reads_the_bid` | the mirror, against the bid |
| `test_a_limit_INSIDE_the_spread_is_reached_by_neither_side` | the fact that reads like a contradiction: a buy there is below the ask and a sell there is above the bid, so it rests |
| `test_outside_the_spread_the_two_directions_diverge` | a direction handled backwards is the defect this module prevents being written twice |

### `TestAStopWaitsForAWorsePrice`

The inverse, because a stop exists to get out or to follow a breakout.

| Test | Description |
|------|-------------|
| `test_a_buy_stop_reads_the_ask` | triggers when the ask rises to it |
| `test_a_sell_stop_reads_the_bid` | triggers when the bid falls to it — the protective case |
| `test_a_stop_and_a_limit_of_one_direction_are_opposites` | for one direction they answer inversely, except exactly at the price where both hold |

### `TestTheSideOfTheBookADirectionTradesAt`

`taken_price` — a buy pays the ask, a sell receives the bid.

| Test | Description |
|------|-------------|
| `test_a_buy_pays_the_ask` / `test_a_sell_receives_the_bid` | the two directions |
| `test_it_costs_the_spread_to_turn_around` | buying and selling at once loses exactly the spread — the property an inverted hand-written pick gets wrong |

### `TestAZeroSpreadQuoteIsAnswerable`

`bid == ask` is not hypothetical: Kraken's trade channel delivers exactly that, which is what
#244 exists for. Both predicates must still decide, and a round trip must then cost nothing.

## Why It Matters

The comparison used to live privately in the simulation executor while the dry-run simulator had
none at all — so a rehearsal filled a resting order because time had passed and a backtest filled
it because the market had arrived. Two answers to one question breaks the framework's central
claim. Verified by mutation: inverting the book side turns three of these tests red, and
inverting the limit comparison turns six tests of `simulation/sltp_limit_validation` red.

## Running

```bash
pytest tests/framework/test_price_trigger.py -v --tb=short
```
