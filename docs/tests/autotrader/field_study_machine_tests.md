# Field Study Phase Machine Tests

Offline unit tests for the state machine that drives the Live Field Study's phase sequence
(#332). **No broker, no network, no orders** — the machine is pure: it consumes one
`PhaseContext` observation per tick and emits one typed `PhaseAction`, performing no I/O and
tracking no broker order ids.

That purity is what makes these tests worth having. The Field Study itself is a release gate
that runs on a funded account and costs real money, so every phase outcome it can reach must
be reachable here first — including the failure outcomes, which a real run should never
produce and therefore never exercises.

| File | Covers |
|---|---|
| `test_state_machine.py` | one case per phase type, plus the outcomes each one can end in |
| `test_cancel_orchestration.py` | the cancel bookkeeping across a re-arm |
| `test_close_and_end.py` | the pass that sends a close and asks for the session's end (#630): the close goes out first, the event flags start afresh with it, and every field study profile has the phase after the force close and before the final summary |
| `test_field_study_setup.py` | the session wiring (#566): a non-spot account opens no capture, a resting order stops the run before it trades, a preflight that raises still leaves a terminated capture, the header records where the session's adapter declares the venue reports a fill's fee and what the executor expects of it (#508), and the end writes the reconciliation totals and the REST telemetry before the end marker |
| `test_order_hooks.py` | what the study's order hooks tell the machine — they record nothing, the capture's order lines are the session's own stream (#566): a close ending with no direction — its position is gone — is observed rather than raised on, and an unaccounted order raises its own flag, never the rejection flag |

## What the outcomes mean

The machine never says "something went wrong"; it says which KIND of wrong, and the
distinction decides whether the certificate fails:

| Outcome | Meaning |
|---|---|
| `PASS` | the phase did what it set out to do |
| `EXPECTED_REJECTION` | the venue refused it, and refusing was the point (lot below minimum, order above balance) |
| `INCONCLUSIVE` | market-dependent, not a defect — a resting limit the market never reached |
| `FAIL` | mechanical: the order never rested, a cancel was not confirmed, a strict rejection filled instead — or an order went unaccounted, which fails any phase, one expecting a rejection included, because the venue may still hold it |
| `SKIPPED` | the adapter lacks the capability, so the phase was disabled before the run |

`INCONCLUSIVE` versus `FAIL` is the line worth understanding: a limit order that does not fill
because the price never came is the market's answer, while a limit order that never appears at
the venue at all is ours.

## The stop phase (#500)

`stop_cancel` is the newest phase type and the tests around it pin two properties that are
easy to get backwards.

**A buy stop rests ABOVE the market** — the mirror of a resting buy limit, because a stop is a
breakout entry. Getting the side wrong does not produce a rejection anywhere in this system:
Kraken executes a mis-sided trigger immediately as a market order, and the simulation fills it
on the same pass. So the wrong side silently turns a resting-order phase into an unintended
real trade.

**A fill FAILS the phase.** For every other resting phase a fill is a pass; here it is the
evidence that the trigger was mis-sided. `test_stop_cancel_fails_when_it_fills_before_the_cancel`
is what keeps that inversion in place.

The phase also reads `PhaseContext.active_stop_count`, never `active_limit_count`. A stop lives
in its own resting world with its own cancel and modify paths, and
`test_stop_cancel_rests_then_cancels_pass` asserts that a limit count of 1 moves nothing — that
order belongs to another phase.

## The close on its way at the stop (#630)

`close_and_end` is the one phase a pass cannot finish. It opens a position, then sends its close and
asks for the session's end in one pass, so the close is still on its way when the loop ends: the stop
has to wait for it. The machine concludes the phase at the session's end
(`conclude_at_session_end`), from the last observation. A close's fill reaches the study as a closed
position, which it does not subscribe to, so the book is the proof: flat only where the stop booked
the fill before the session-end event. The final summary that end overtook is concluded as skipped.

The tests hold each way the phase can end: a pass on a flat book; a fail naming what the stop left
(a position still in the book, a close given up unaccounted, a close rejected, an order still
pending), with the severity named where the end was not a normal one; an open that is rejected,
unaccounted or never fills, after which the final summary ends the session as before; a position at
entry, which sends nothing; and a pass that still comes after the close. An end in another phase, or
before the close, concludes nothing, so a run that aborted still reads its unreached phases as
missing.

## Run

```bash
pytest tests/autotrader/field_study_machine/ -v
```

Or launch.json: `🧩 Pytest: Field Study Machine (#332)`.

The real-money counterpart — what each phase proves against a real venue, and what it costs — is
[field_study_guide.md](../live_field_study/field_study_guide.md).
