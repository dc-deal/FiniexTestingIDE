# Own Exit Levels Tests

`tests/framework/test_own_exit_levels.py` — the helper a decision logic keeps its own stop and
target with (`python/framework/decision_logic/own_exit_levels.py`, #625), and `OwnExit`, the
description of a close made at one of them. Runs under the synthetic `framework/_root` suite. Pure
— a stand-in trading API, no executor, no data.

## Why it exists

A venue holds a level for a position only where its API takes one, and Kraken's takes none; the
framework does not rebuild what a venue lacks. So a logic checks its levels itself and closes
through its own `close_position`. The CORE logics and the probe's own-exit mode use the helper and
their suites run it end to end; this file pins what it promises, including the cases no logic
happens to reach.

## What Is Tested

| Class | What it pins |
|-------|--------------|
| `TestTheLevelsWaitUnderTheEntryOrder` | levels set under the entry's order id belong to the position it opens; without levels and distances nothing is checked |
| `TestAPositionWithoutLevelsIsSeeded` | a long's stop below and target above its entry, a short the mirror; a distance of none, zero or less gives no level (a zero-width band must not put the stop AT the entry); levels set explicitly win over the seed |
| `TestTheStopOnlyTightens` | toward the market moves, away does not — both directions; a position without a stop takes the first |
| `TestTheClosingSideOfTheBookDecides` | a long's stop and target read the bid, a short's the ask, through the shared price-trigger predicates; the stop first when one quote reaches both |
| `TestAPositionIsClosedOnce` | the close carries the `OwnExit`; never a second while the first is on its way; a refused close is sent again on the next tick; only the tick's symbol |
| `TestTheLevelsGoWithTheirPosition` | a position seen and gone takes its levels; an entry not filled yet keeps them; `forget` drops an entry that will not fill |
| `TestASnapshotComesBackOnlyForRestoredPositions` | the round trip; a position that did not come back is dropped; a break-even stop survives where a re-seed would have loosened it |
| `TestAnOwnExitNamesALevel` | `OwnExit` refuses a reason no level makes (`MANUAL`, `SCENARIO_END`) and a level that is no price |
| `TestAnOwnExitCountsAsATriggeredClose` | through the real `DecisionTradingApi`: a close sent with an `OwnExit` carries its reason and level to the executor and counts once in `sl_tp_triggered`; a refused close and a request that joins a close already on its way do not count; a close without an `OwnExit` is not counted there |

## Related

- The CORE logics end to end: [Trend Channel Reference](../simulation/trend_channel_reference_tests.md),
  and the probe's own-exit mode in the AutoTrader mock sessions
  ([Integration Tests](../autotrader/integration_tests.md), `test_autotrader_trade_scenarios.py`).
- How a bot uses it: [Quickstart — Own Exits](../../user_guides/quickstart_guide.md).
