# Account Value Tests

`tests/framework/test_account_value.py` — the one account-value definition per account model
(`PortfolioManager.get_account_value()`, #356 / #492). Runs under the synthetic
`framework/_root` suite.

## Why this file exists at all

The live circuit breaker used to read a different quantity per account model: spot got its
spot-aware equity, margin got `get_balance()`. A margin balance moves only on REALISED P&L by
construction, so an open drawdown was invisible to it — which made the account model that can lose
MORE than it holds the one whose breaker could not see the loss coming.

The correct number already existed three methods away, feeding the drawdown series. It simply was
not the number the breaker asked for. Making it public is what removed the second answer, and this
file is what keeps it removed.

Driven directly against the `PortfolioManager` — no ticks, no latency, no scenario. The quantity
is the subject.

## What Is Tested

### `TestMarginSeesAnOpenLoss`

The defect, stated as the difference between two numbers. A margin position moving against us
costs real money the moment it moves; settled cash does not know that yet.

| Test | Description |
|------|-------------|
| `test_the_account_value_falls_while_the_balance_does_not` | the balance is unchanged (correct — nothing was realised) while the account value has fallen by the open loss |
| `test_and_it_rises_on_an_open_gain` | the mirror, so the fix is not a one-directional fudge |
| `test_a_flat_margin_account_reads_its_balance` | without a position the two answers must agree, or every baseline shifts |

### `TestSpotAnswersNoneRatherThanGuessing`

An unvalued holding is not a drawdown.

| Test | Description |
|------|-------------|
| `test_no_price_means_no_number` | `None` before a price exists — a substituted value would be a drawdown reading invented out of nothing, the same discipline as a missing clock raising rather than falling back to wall time (§9) |
| `test_with_a_price_it_is_the_spot_equity` | with a price it is exactly `get_spot_equity`, not a second definition |

### `TestTheDrawdownSeriesAndTheBreakerShareIt`

One definition, two consumers — the whole reason the method is public. While they were separate,
the series and the breaker could disagree about whether the account had lost anything, and the
report would show one of the two.

### `TestTheMinFloorMeansTheSameThingInBothModels`

The consequence, made explicit so it cannot drift back. `min_balance` and `min_equity` now
denominate the SAME quantity and the account model only decides which config key a profile writes.
Before #356 the margin floor was settled cash, so an open loss could not reach it at all, however
deep it got.

## Why It Matters

It is the input every limit in the circuit breaker sits on. A wrong input under a correct
threshold is a limit that cannot fire, and the account it could not protect is the leveraged one.

## Running the Tests

```bash
pytest tests/framework/test_account_value.py -v --tb=short
```
