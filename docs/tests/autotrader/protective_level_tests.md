# Protective Level Tests (#500)

Who actually enforces a `stop_loss` or `take_profit` — as opposed to who was assumed to.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/protective_levels/](../../../tests/autotrader/protective_levels/) |
| Harness | `MockOrderExecution` and a `TradeSimulator` over the mock adapter — no network, no config files, no tick data |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entry | `🧩 Pytest: Protective Levels (#500)` |
| Architecture doc | [autotrader_architecture.md](../../autotrader/autotrader_architecture.md) — *Protective levels — who enforces a stop* |
| Session-level coverage | `TestStopLossConfiguration` / `TestTakeProfitConfiguration` in `tests/autotrader/integration/test_autotrader_trade_scenarios.py` — the same property through a whole AutoTrader session |

---

## What was wrong, and why the tests read the way they do

The engine's SL/TP check returned immediately unless the executor was a simulation. Its own
docstring gave the reason: *"in LIVE mode, the broker handles SL/TP execution server-side"*.
Nothing ever made that true — the submit payload had no field for a level, so the venue was
never told. The level was recorded on the position, shown to the strategy, printed on the
console and carried into the run report, and no code anywhere acted on it.

The venue said so itself. A LIMIT submitted with a stop loss under `validate=true` came back
described by Kraken as `buy 0.10000 ETHUSD @ limit 100.00`, with no conditional close.

So the tests assert an ACT, not a record. "The level is stored on the position" was exactly
what the old tests asserted, and it passed throughout the whole period nothing was enforcing
it. A test that cannot tell a working stop from a decorative one is worse than no test.

## Suite Coverage

### `test_protective_level_enforcement.py`

| Test | What it verifies |
|---|---|
| `test_both_pipelines_name_an_enforcer` | The crossing property: neither executor may answer "nobody". This is the defect as one assertion |
| `test_a_live_level_is_watched_by_this_process` | Today's live answer, which the report has to be able to state out loud |
| `test_a_breach_closes_the_position` | The behaviour that did not exist: a live stop acts |
| `test_the_exit_carries_the_reason_across_the_round_trip` | The reason is known at the TRIGGER and the fill lands a round trip later, so it rides on the `PendingOrder`. Without it a stop-out records as a plain manual close |
| `test_a_target_breach_closes_it_too` | The same, the other direction |
| `test_a_position_without_levels_is_left_alone` | The regression guard: nothing closes what declared nothing |
| `test_a_second_breach_while_the_close_is_in_flight_triggers_nothing` | A live close takes a round trip and the next tick is usually worse. Without the in-flight guard the position would be closed twice — the risk the old early return was wrongly protecting against |
| `test_the_simulation_still_answers_local` | The sim's answer is unchanged |
| `test_a_simulated_stop_fills_at_the_level_itself` | And so is its mechanism: AT the level, in-tick. This is the difference from live, pinned rather than smoothed over |
| `test_a_strategy_partial_close_holds_the_stop_off_while_it_flies` | The guard matches ANY close in flight, so a partial close suppresses the level for one round trip. Conservative on purpose: a partial takes some lots and a stop takes all of them |
| `test_a_real_adapter_still_answers_local` | Parametrised over the REAL Kraken and MT5 adapters built from the checked-in broker JSON. The two LOCAL assertions above run over the mock — the one adapter that can never trip — while 110 of 120 checked-in backtest scenarios declare `mt5`, so this is the case that would actually break |
| `test_no_adapter_declares_a_venue_held_level_yet` | The precondition under the constant, asserted instead of assumed: no adapter declares `native_position_sl_tp`. It goes red the day one does, and then the RESOLVER is the thing to look at |

### Why the resolver is still a constant

`get_protective_level_enforcement()` returns `LOCAL` unconditionally, and #500 item 3 asks for it
to ask the adapter instead. The only existing flag cannot answer the question. `native_position_sl_tp`
means *who performs a MODIFY on an open position*, and on the two venues that exist the two answers
point in opposite directions: #209 declares it `True` for MT5 while MT5's submit still attaches no
level (so `VENUE` would be claimed with nobody holding it — this issue's original defect, one level
up), and Kraken keeps it `False` by decision while a standalone stop order at the venue genuinely
does hold one.

What the resolver needs is a SUBMIT-side declaration, which nothing can make truthfully yet. So the
constant stays and the two tests above are what fail when somebody wires it to the wrong field. One
further thing is already known about the eventual answer: **one boolean per executor cannot describe
a mixed run.** Kraken's conditional close carries only one of a declared pair, and a trailing stop
migrates one position's level mid-life — so the enforcement site eventually belongs on the
`Position`, stamped from the venue's own confirmation rather than from our declaration.

## The difference between the two pipelines is real

The simulation fills a synthetic close at exactly the level, deterministically, in the same
tick. Live has no such price on offer: it goes through the normal asynchronous close, so the
exit lands at whatever the venue gives it a round trip later. **A backtest therefore reports
protected exits slightly better than live can deliver them**, and the suite pins both sides so
that nobody "fixes" the divergence by making the backtest non-deterministic.

## The two windows in which a level is not acted upon

Both are narrow, both are real, and neither is a defect to be fixed by loosening the guard:

- **A close is already in flight.** The guard matches any close on the position, so a
  strategy's partial close holds the stop off until it resolves. Letting both fly would ask
  the venue for more lots than the position holds.
- **A protective close was refused.** The pending is gone, so the next tick triggers again.
  That is the right answer for a transient refusal and a tight loop for a permanent one; the
  refusal is logged as an ERROR into the session pot each time, so it cannot pass unnoticed.

## What this suite does NOT cover

A level enforced by this process does not survive this process. Putting it at the venue means the
level becomes an ORDER — and since #500 the live path can place one: `STOP` and `STOP_LIMIT` route
to Kraken, so a STRATEGY may rest its own protective order there. What is not built is the
framework doing that for a declared `stop_loss`, and it needs one decision first: the pair cannot
both rest at Kraken (no OCO, no brackets, and a cash account reserves the whole holding for each
resting exit), so only one of the two can be venue-held. The release-gate test
`test_a_declared_stop_loss_reaches_the_venue` remains a strict `xfail` until that lands, and it
asserts the standalone route rather than the abandoned conditional close.

## Running it

```bash
python -m pytest tests/autotrader/protective_levels/ -v
```
