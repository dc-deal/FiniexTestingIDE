# Round-Trip Fee Tests

`tests/framework/test_round_trip_fees.py` — what one completed trade costs, and what the run
counts about the trades it closed. Runs under the synthetic `framework/_root` suite.

## Why this file exists at all

A maker/taker venue charges every fill, so a round trip costs two fees. Both close paths used to
pass `exit_fee=None` — correct for a spread broker, which charges no per-side commission, and wrong
for every other one: every completed trade at Kraken was under-booked by exactly one fee. The
switch is the FEE MODEL, and the asymmetry between the two brokers is what these tests pin.

The file grew around the same question — what a record says about its money — and now also holds
the cost columns of a partial close, the two fee totals a run reports, the rule for a trade that
realised nothing, and the excursion a spot trade records between its entry and its close.

Driven directly against the `PortfolioManager` and the fee factories — no ticks, no latency, no
scenario. The arithmetic is the subject.

Not here: how fees are priced from the broker configuration (the fee-tier suite), and how the
reports sum them (the reporting suite).

## What Is Tested

| Class | What it pins |
|---|---|
| `TestTheFeeModelDecidesWhetherThereIsAnExitFee` | a maker/taker venue charges the exit, a spread broker charges nothing per side; an absent or unpriced fee model is refused when the adapter is built |
| `TestARoundTripBooksBothLegs` | two fees on a maker/taker round trip, one on a spread one |
| `TestTheExitFeeLeavesTheMoney` | `net_pnl == gross_pnl − total_fees` in BOTH account models, and a margin balance moves by the net figure |
| `TestAPartialCloseChargesOnlyTheClosedLots` | the closed portion carries the whole exit fee and its share of the entry fee |
| `TestTheCostColumnsAddUpToTheFees` | `commission_cost + swap_cost == total_fees` on every record — full and partial close, margin and spot. A partial close left its exit fee out of `commission_cost`, so the columns fell short by exactly that fee at a maker/taker venue |
| `TestTheSessionCostIsReadableThroughTheApi` | a decision logic reads what the session spent through `get_cost_breakdown()`, both legs included, as a copy it cannot disturb |
| `TestTheDeclaredRateIsTheRateCharged` | the rates the seed declares are the rates charged, taker and maker |
| `TestTheRunCountsWhatItClosed` | a trade that realised exactly nothing is neither a winner nor a loser; the stats' `total_fees` is the fees of the CLOSED records and leaves an open position's fee out — that one is in `fees_charged` |
| `TestTheExcursionIsTrackedBetweenEntryAndClose` | a winner that dipped first reports the dip as its MAE, in both account models. At spot the account value never marked the position, so the excursion was measured at entry and close alone; mutation-checked, the spot case goes red without the mark |

## Running

```bash
pytest tests/framework/test_round_trip_fees.py -v --tb=short
```
