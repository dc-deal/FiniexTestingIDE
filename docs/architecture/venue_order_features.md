# What a Venue Holds — Order Features and Own Exits

A stop or a target that nobody holds looks like protection and is none. So a run has to know, before
it starts, whether the venue's API will hold a level for a position, and a strategy has to know what
happens where it will not. This document says how that is answered, what a strategy does instead, and
what the two answers cost.

Not here: which order types a venue accepts ([Live Execution](live_execution_architecture.md)) and
how to write the exit itself ([Quickstart — Own Exits](../user_guides/quickstart_guide.md)).

## The rule

**An adapter offers what its venue's API offers, and the framework does not rebuild what is missing.**
A logic declares the order features it uses, an adapter states the ones its API takes, and a run
refuses a declared one the venue lacks.

| Where | What happens to a declared feature the venue lacks |
|---|---|
| A backtest | the scenario is refused in the batch's main process, before any data is loaded; the others run |
| A session | the start is refused, exit code 2 |
| At order time | a level its logic did not declare is refused with `order_feature_not_declared`, on an order (a denied row) and on every modify (a warning, once per feature); removing a level always passes, and no cooldown follows |
| The live executor | refuses a level itself too: nothing it accepted would be enforced by anybody |

A backtest may declare an exception, `features_beyond_venue` at the top of its scenario set. It is held
narrow (a name no scenario needs, or that the venue holds anyway, is refused; a sweep is not judged per
combination, because one arm of a grid may need what another does not), recorded in the run's
header and ledger row, warned about in its report and refused by the benchmark certificate. A session
cannot declare one.

## Why the rebuilt stop went

Until #625 the framework rebuilt at Kraken what Kraken's API does not offer: a STOP placed after the
entry filled, the target kept locally, the stop cancelled before any other close, resized after a
partial close, adopted after a restart. One level lived in four places, and every race between them
was a class of defect. Kraken's API team confirmed on 2026-10-27 that neither a bracket nor a
one-cancels-other can be placed through its spot API, on REST, WebSocket or FIX. So the thirty-day run
trusts no rebuilt contingency, and a backtest and a session price the same exit the same way.

## What a strategy does instead

It keeps its levels itself, checks them on every tick and closes through `close_position`
(`OwnExitLevels`). The close names its reason and its level, so the trade keeps `sl_triggered` or
`tp_triggered`, the close counts in `sl_tp_triggered`, and its slippage is measured against the level.

**The price of that, stated plainly:**

- **A position is unprotected while the process is not running.** Nothing rests at the venue. A
  supervisor restarts the process and an alert tells the operator it went silent, but a move during
  the outage is not stopped by anything.
- **The same holds while the feed is silent.** An own exit needs a price, so an outage of the feed
  stops it too. Only a logic's own heartbeat ladder can close then, by elapsed time.
- **An own exit fills at the next price after the level,** in a backtest as in a session. A level a
  position carries in a backtest fills AT the level. The first is what a venue without held levels can
  deliver, so it is what a backtest of such a strategy should show.
- **A stop order a logic rests itself is an entry, not a protection.** At spot, a SELL stop that fills
  books as a new SHORT position beside the LONG it was meant to protect, with nothing linking the two.
- **A moved stop exists only in the logic's memory,** and it survives a restart through the algo state
  persistence, as of its last save. Without a saved state it is derived again from the entry price.

## The simulation's own level check

A level a position carries is enforced by the simulation alone (`TradeSimulator`), against every tick:
a synthetic close AT the level, in the tick and deterministic. It serves a backtest on a venue that
holds levels, and one that declares the exception. A session has no such check, because it holds no
level.

## Coming back: a stored book with a level

A session that finds a stop or a target in its stored position book refuses to start, naming the
position and what to do: such a level would be enforced by nobody, and a stop still resting at the
venue would read as an order of unknown origin or fill beside the bot's own close. The check reads the
raw document, because the fields of the rebuilt stop are gone from the model.

## What venues and frameworks do, as of 2026-10-25

Dated, because venues change. The table of what each venue's API offers, and the sources, stand in #625.

| | Stop and target | Where the venue lacks something |
|---|---|---|
| QuantConnect LEAN | native orders | refused per brokerage model; its Kraken model refuses every contingent order |
| nautilus_trader | native where the adapter supports it | the adapter denies it; local emulation only when a strategy asks for it per order |
| CCXT | standalone orders | a feature map per exchange, no emulation |
| freqtrade | the stop optionally at the exchange | rebuilds the stop itself, as this project did until #625 |
| This project | what the API holds; otherwise the logic's own | refused at the start, in a backtest too unless an exception is declared |

## The venue's dead man's timer, not armed

Kraken offers a mirror of our own liveness ping: `CancelAllOrdersAfter` counts down, the client
refreshes it, and when the client stops, the venue cancels the account's orders. This project does not
arm it (decided 2026-10-13, #235, and kept on 2026-10-30). The first reason given was that it would
cancel a stop the venue holds, and since #625 there is no such stop. What it would cancel today is a
strategy's resting entries, and a strategy trading with market orders has none. If a strategy with
resting entries comes to need it, it arrives as a capability an adapter declares, with a reader.

## What is measured, and what is not

- **Measured:** Kraken's answer on what its API places (2026-10-27), and what its open-orders read shows
  of a group placed in its own interface (2026-10-25).
- **Not measured against a real venue:** an own exit. Its path is the ordinary close, which the field
  study does measure.
- **Not walked: a live path on a venue that holds levels.** MT5 states both, and no live executor passes
  a level on. #209 builds that.

## Related

- [Quickstart — Own Exits](../user_guides/quickstart_guide.md) — how a logic does it
- [Algo State Persistence](../user_guides/algo_state_persistence_guide.md) — what survives a restart
- [Session-End Policy](session_end_policy.md) — what happens to resting orders
- [Order Features Tests](../tests/framework/order_features_tests.md) · [Own Exit Levels Tests](../tests/framework/own_exit_levels_tests.md)
