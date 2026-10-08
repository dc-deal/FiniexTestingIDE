# Protective Level Tests (#500)

Who actually enforces a `stop_loss` or `take_profit` — as opposed to who was assumed to.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/protective_levels/](../../../tests/autotrader/protective_levels/) |
| Harness | `MockOrderExecution` and a `TradeSimulator` over the mock adapter — no network, no config files, no tick data |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entry | `🧩 Pytest: Protective Levels (#500)` |
| Architecture doc | [autotrader_capital_and_safety.md](../../autotrader/autotrader_capital_and_safety.md) — *Protective levels — who enforces a stop* |
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
| `test_a_breach_closes_the_position` | The behaviour that did not exist: a stop acts in an AutoTrader session |
| `test_the_exit_carries_the_reason_across_the_round_trip` | The reason is known at the TRIGGER and the fill lands a round trip later, so it rides on the `PendingOrder`. Without it a stop-out records as a plain manual close |
| `test_a_target_breach_closes_it_too` | The same, the other direction |
| `test_a_position_without_levels_is_left_alone` | The regression guard: nothing closes what declared nothing |
| `test_a_second_breach_while_the_close_is_in_flight_triggers_nothing` | A live close takes a round trip and the next tick is usually worse. Without the in-flight guard the position would be closed twice — the risk the old early return was wrongly protecting against |
| `test_the_simulation_still_answers_local` | The sim's answer is unchanged |
| `test_a_simulated_stop_fills_at_the_level_itself` | And so is its mechanism: AT the level, in-tick. This is the difference from an AutoTrader session, pinned rather than smoothed over |
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
constant stays and the two tests above are what fail when somebody wires it to the wrong field.

**Since #503 the constant is the RUN'S DEFAULT, not the answer for every position.** The thing this
section predicted has happened: one boolean per executor cannot describe a mixed run, so the
enforcement site now sits on the `Position` — derived from `protective_broker_ref`, which is
stamped from the venue's own CONFIRMATION and never from our declaration. Between submitting a
protective order and hearing back, nobody at the venue holds anything, and the local check stays
awake for exactly that window.

### `test_venue_held_protection_declaration.py` (#503, stage A)

The declaration side: the intent can be expressed, resolved and refused. Nothing is placed yet.

| Test | What it verifies |
|---|---|
| `test_the_two_declarations_combine` | The resolution matrix, parametrised: profile default ⊕ per-order override. Asserted THROUGH the refusal rather than through a getter — the resolution exists to gate the placement, and a test that reads it directly would still pass if the gate were wired to something else |
| `test_the_venue_is_named_when_it_cannot_hold_it` | The refusal names the short side (the profile switch and the broker), because a refusal an operator cannot act on is only half a refusal |
| `test_a_venue_that_can_hold_it_lets_the_order_through` | The other direction, over a mock that declares the capability |
| `test_an_order_without_a_level_is_never_refused_for_this` | A profile-wide opt-in must not turn every unprotected entry into a rejection |
| `test_without_a_reference_the_position_follows_the_run` | The stamp is derived, and absent it falls back to the run's answer |
| `test_a_reference_makes_it_venue_held_whatever_the_run_says` | A confirmed protective order overrides the run default for that one position |
| `test_the_stamped_position_is_left_to_the_venue_and_the_other_is_not` | The mixed run, both directions at once: acting locally on a venue-held level sells the position twice, not acting on a local one leaves it unprotected |
| `test_the_flag_does_not_reject_a_backtest_order` | The simulation ACCEPTS the flag and changes nothing. Refusing there would make an opted-in strategy un-backtestable, and sim/live parity is the point of the project |
| `test_the_backtest_still_answers_local` | And its own answer is unmoved |
| `test_each_adapter_states_whether_its_venue_can_hold_one` | Parametrised over the REAL broker JSON: Kraken `True` (measured), MT5 `False` (it holds the level ON the position — a different mechanism, #209) |
| `test_the_mock_declares_no_venue` | The mock says so explicitly rather than defaulting quietly |
| `test_it_is_not_the_same_question_as_a_position_level_modify` | `venue_held_protective_orders` and `native_position_sl_tp` point in opposite directions on Kraken. Reading one for the other reinstates #500's original defect one level up |

### `test_venue_initiated_close.py` (#503, stage B)

The routing side: a resting order that fills is no longer necessarily an ENTRY, and a close
order can name a position other than itself.

| Test | What it verifies |
|---|---|
| `test_a_filled_close_order_closes_and_opens_nothing` | The defect this stage prevents: three of the four live fill sites called `_fill_open_order` unconditionally, so a firing stop would have opened a SECOND position, booked an entry fee and told the algo an order had filled |
| `test_the_close_is_booked_against_the_named_position` | The close settles the position named in `closes_position_id`, carrying its reason |
| `test_a_locally_requested_close_still_resolves_by_its_own_id` | The unchanged case, and the reason the resolution reads `closes_position_id OR pending_order_id`: every close the strategy or the engine requests carries the position's id AS the order id |
| `test_a_full_close_emits_it_once` | `POSITION_CLOSED` fires on a full close — until now a full close emitted nothing and the algo learned of it by noticing the position missing |
| `test_a_venue_initiated_close_says_nobody_here_asked` | `requested_locally=False` where the venue fired its own order |
| `test_a_partial_close_emits_the_partial_event_and_not_this_one` | The two events do not overlap |
| `test_the_simulation_emits_it_too` | Parity, and it is the point rather than a bonus: an event only an AutoTrader session can produce would make a backtest stop predicting the session |

### `test_venue_close_resolver.py` (#503, stage C)

The booking side: one fill reaches us through several routes, so it must be bookable from
all of them without closing twice.

| Test | What it verifies |
|---|---|
| `test_a_repeated_report_books_nothing_further` | The property the resolver exists for — a second arrival of the same volume is a no-op |
| `test_the_counter_records_what_was_booked` | The delta is taken against `venue_close_applied_lots` |
| `test_the_counter_is_not_the_fills_aggregate` | **Decision 13.a asserted rather than assumed.** On a venue with trade-level reporting the trades drain fills `cumulative_filled_lots` BEFORE anything is booked, so a delta taken against it would be zero exactly in the case this exists for — and the failure would be silent: no error, just a position that stays open while the venue has already sold it |
| `test_it_books_a_partial_close_then_the_close` | Kraken has no PARTIALLY_FILLED — a half-filled order stays `open` and reports what executed beside it. Both halves are real lots |
| `test_the_second_booking_does_not_reuse_the_first_executions` | `_fill_close_order` hands its trade list to the portfolio as the closing record's executions; handed the same list twice, the second close would report the first partial's executions and fee again |
| `test_a_close_for_a_position_we_no_longer_hold_is_reported` | Not attributable is never silent, and the venue's reference is always named — it is the only handle left for a manual check |
| `test_more_lots_than_the_position_holds_is_reported` | The excess is reported; what COULD be attributed is still booked |
| `test_an_unattributable_report_is_not_replayed_forever` | The counter advances even where nothing could be booked, or every poll rediscovers the same orphan volume and buries the session channel |

The three live feeds that reach it — a `FILLED` query, a terminal answer still carrying
`vol_exec`, and a partial one on a still-open order — are wired in `_handle_query_response`.
They are fed DIRECTLY in this file on purpose: the mock cannot price-trigger a resting stop
order, so driving one through the poll loop would prove the mock rather than the resolver.

### What the 2026-09-10 adversarial review added

Nine independent lenses over the uncommitted diff, each finding verified by three skeptics told
to refute it. Four defects survived and are now pinned here — three of them in code the offline
suite was green over:

| Test | The defect it prevents |
|---|---|
| `test_the_target_is_still_enforced_here` · `test_the_stop_is_still_left_to_the_venue` | **The stand-down was per POSITION and had to be per LEVEL.** Kraken has no OCO, so exactly one of a declared pair rests at the venue — the STOP. Standing down for the whole position left the TAKE PROFIT recorded, printed, carried into the report and enforced by nobody: #500's defect verbatim, one level up |
| `test_a_remainder_below_volume_min_advances_the_counter_to_the_whole_position` · `test_and_the_venues_next_report_is_then_a_clean_no_op` | The idempotency counter recorded the REQUEST. `_fill_close_order` converts a partial into a full close when the remainder falls below `volume_min`, so the counter under-recorded and the venue's next report of the same volume raised a false "unattributable" alarm on a healthy close |
| `test_it_falls_back_to_the_positions_own_size` · `test_an_unsizeable_answer_is_reported_rather_than_swallowed` | A `FILLED` answer carrying no volume booked NOTHING, silently. A stop-out that leaves no trace is indistinguishable from a stop that never fired |
| `test_the_second_close_still_carries_an_execution` | An empty unbooked slice is not the same message as "there are none". Handed down as-is it either suppressed the synthesis or re-reported the first partial's executions and its fee |
| `test_a_filled_answer_closes_the_position_instead_of_opening_one` | The query-`FILLED` path — the one a firing venue stop actually takes — driven through the REAL handler rather than the router |

Not pinned, and deliberately: the other three fill sites still go through `_route_resting_fill`
directly, because placing a real protective order is stage D's. They carry ENTRIES; the one that
carries a protective order is the query path above.

### `test_protective_order_placement.py` (#503, stage D1)

The first stage that leaves the process. Three moments are kept apart, and the safety of the
whole feature is in that separation:

    submit        protective_order_id set · the stamp is NOT · the local check WATCHES
    confirmation  the stamp falls · the local check stands down for this stop
    death         the stamp is cleared · the local check takes it back next tick

| Test | What it verifies |
|---|---|
| `test_a_protected_entry_produces_a_protective_order` | The placement happens at all, hung off the FILL — there is nothing to protect before a position exists |
| `test_it_reverses_the_direction_and_carries_the_trigger` | What protects a LONG is a SELL; the same direction would double the position |
| `test_it_carries_its_own_id_not_the_positions` | The wire key derives from the order id, so overloading the position's would collide with a restart's counter |
| `test_no_opt_in_places_nothing` · `test_an_entry_without_a_stop_places_nothing` | The gate in both directions |
| `test_the_local_check_still_watches_before_confirmation` | Between sending and hearing back nobody at the venue holds anything |
| `test_a_confirmation_hands_the_stop_over` · `test_and_then_the_local_check_leaves_that_stop_alone` | And only then does it stand down |
| `TestItClaimsNoFunds` | The resting stop reserves nothing of the account, and a second buy beside it is not refused — in both account models. The venue holds nothing against a resting exit, and a backtest's stop-loss is no order at all; read as a close in its own trading direction, a sell stop held its position's value in quote |

### `test_protective_order_lifecycle.py` (#503, stages D2-D6)

Placing one is the easy half. Every failure below is SILENT — the console shows a level, the
venue holds a different one or none — which is why each ends in the session channel.

| Test | What it verifies |
|---|---|
| `test_moving_the_stop_amends_the_order_rather_than_replacing_it` | **D2.** An amend keeps the order; a cancel-replace opens a window with no protection at all, on every move of a trailing stop |
| `test_withdrawing_the_stop_cancels_the_order` | A level set to None cannot be amended into — the order itself has to go, or the venue keeps enforcing what the strategy withdrew |
| `test_an_unchanged_level_touches_nothing` · `test_a_refused_amend_says_which_level_the_venue_is_really_holding` | The no-op, and the divergence named out loud when the order cannot follow |
| `test_a_refused_cancel_withholds_the_close_and_says_so` | **D3.** No `reduce_only` at spot: a close beside a resting stop can fill twice. Without a confirmed cancel the close does NOT go out and the position stays open AND protected |
| `test_a_partial_close_re_places_at_the_remaining_size` · `test_a_full_close_leaves_nothing_to_protect` | **D4.** The remainder is protected again at its NEW size; a full close invents nothing |
| `test_the_released_stop_ends_cancelled_by_the_framework` | The protective order a close waited for ends with its own row: `cancelled`, by the framework, `protection_released` — it used to end with no record of how |
| `test_its_cancel_event_names_the_position_it_protected` | Its `order_cancelled` event states the POSITION's direction, as its row does — the stop protecting a LONG is a sell, and the event used to say SHORT beside a row saying LONG |
| `test_it_is_exempt_from_the_cancel_policy` | **D5.** The only pair a session can start with today would otherwise cancel the protection exactly when the bot stops looking |
| `test_the_repeat_request_sends_nothing` · `test_and_the_waiting_close_still_goes_out_when_the_cancel_confirms` | **D3's other half.** A deferred close registers nothing with the request processor, so `is_pending_close` and `has_pending_orders` both stay False and the local level check calls in again on every tick — a repeat request JOINS the waiting close instead of overtaking it, and the waiting one still goes out when the cancel confirms |
| `test_the_position_hears_about_it` | A broker reference arrives in THREE places, and the reconcile attribution is the third. A protective order reclaimed there without stamping its position leaves the position reading LOCAL for a stop the venue holds — two enforcers, and a carry-over with no reference for the next boot |
| `test_a_protective_amend_refusal_notifies_no_outcome` · `test_an_ordinary_order_does_not_arm_it_either` | **D6.** Measured: 378 amends over 62 positions, worst burst 20 in 39 ticks. Two rejections arm a 60 s block on every new order in that direction — the REPLACEMENT protective order included. A refused amend is no new-order rejection, because no new order was attempted — for a protective order and for an ordinary entry alike: the order keeps working, so it books no row either |

**The suite's own venue** lives in `conftest.py`. A protective order is the first order in this
project meant to REST at the venue and outlive the process, and the stock `MockBrokerAdapter`
cannot represent that: in INSTANT_FILL it fills the stop on arrival — closing the position it was
placed to protect — and with no book of its own it answers REJECTED to the first poll of an order
it did not mint, tearing it down one tick after confirmation. Both are venue behaviours the mock
never needed before.

## The difference between the two pipelines is real

The simulation fills a synthetic close at exactly the level, deterministically, in the same
tick. An AutoTrader session has no such price on offer: it goes through the normal asynchronous close, so the
exit lands at whatever the venue gives it a round trip later. **A backtest therefore reports
protected exits slightly better than an AutoTrader session can deliver them**, and the suite pins both sides so
that nobody "fixes" the divergence by making the backtest non-deterministic.

## The two windows in which a level is not acted upon

Both are narrow, both are real, and neither is a defect to be fixed by loosening the guard:

- **A close is already in flight.** The guard matches any close on the position, so a
  strategy's partial close holds the stop off until it resolves. Letting both fly would ask
  the venue for more lots than the position holds. The simulation stands aside the same way
  since #362: filling the level beneath a close still in its latency queue made the position
  vanish under that close, which then arrived to find nothing — an exit and a refusal an
  AutoTrader session can never produce.
- **A protective close was refused.** The pending is gone, so the next tick triggers again.
  That is the right answer for a transient refusal and a tight loop for a permanent one; the
  refusal is logged as an ERROR into the session pot each time, so it cannot pass unnoticed.

## What this suite does NOT cover

**The boot half lives next door:** `tests/autotrader/cold_start/test_protective_order_boot.py`
covers what the next session makes of a carried protective order — fired, still resting, a
reference the venue no longer recognises, or an order id carried with NO reference at all
(submitted, and the session ended before the venue answered: it names an order object that died
with that session, so it can be neither asked about nor cancelled, and every close would be
withheld behind a cancel that can never be scheduled) — because that is cold-start behaviour and
the fixtures for it are there.

A level enforced by this process does not survive this process. Putting it at the venue means the
level becomes an ORDER — and since #500 the live path can place one: `STOP` and `STOP_LIMIT` route
to Kraken, so a STRATEGY may rest its own protective order there. Stages A through F of #503
shipped 2026-09-10/11 — declaration, routing, resolver, placement, the order's whole life at the
venue, and the boot that asks what became of a carried one — and the tables above plus the boot
suite next door cover them.

**One piece is genuinely still open: the `ClosedOrders` read.** A reference lookup cannot describe
an order the venue has already retired, so after a downtime longer than Kraken's txid retention a
fired stop comes back UNKNOWN and nothing can say whether it filled. The SAFE half is built —
UNKNOWN is never read as "still resting" — so the bot never believes in a protection it does not
have; what is missing is the wider, time-ranged read that would settle it.

**One premise this section used to state is false, and it was corrected by measurement
(2026-09-07, minimum size, cleaned up):** Kraken does NOT reserve the holding against a resting
exit, and a stop and a limit rest simultaneously over the same holding. So the pair is not the
constraint. What follows instead is the opposite problem: the venue links nothing, so OCO and
orphan cleanup are OUR work — a close on another route leaves the protective order ARMED over a
holding that is gone. The release-gate test that used to be a strict `xfail` here was RETIRED
rather than flipped: it checked the entry payload for a conditional close, and #503 deliberately
never puts one there. Its replacement asserts the real contract — the entry payload stays EMPTY —
and the acceptance it was standing in for was taken at the venue instead, by the field study's
`protective_level_test` phase on a real account.

**The `ClosedOrders` read named above is no longer missing (#487).** The adapter now has a
time-ranged closed-order route that can also be narrowed to one wire key, and it was measured
against Kraken before it was written. That closes the gap this section described for the boot
resolver, and it is the read on which "the venue never took this order" rests.

## A close waiting behind a protective order always ends

`test_waiting_close_settles.py`. A close requested while the position's protective order is at
the venue is parked until the venue confirms that order's cancel — a close racing a resting stop
can fill twice. It used to end in exactly three ways: the cancel confirmed, the close released, the
close abandoned. Every other way the protective order could stop resting left the close parked for
the rest of the session, and while it waited every new close joined it and the local stop check
stood aside: the position could be neither closed nor protected.

| Test class | Pins |
|---|---|
| `TestTheProtectiveOrderEndsBeforeItRests` | refused at submission → the waiting close goes out; filled in its own submit answer → the waiting close is dropped, nothing stays pending; never held by the venue while its cancel was still parked (the resolution, by our own key, after the settle window) → booked `undelivered`, and the close goes out |
| `TestTheVenueEndsTheProtectiveOrderAfterItRested` | a status read finds it rejected → the close goes out; rejected after closing part → the part is booked and the close goes out for the rest |

The window these tests need — the protective order sent, its answer not yet read — exists in the
live executor only between two drains, and the mock's worker thread answers as fast as it is
asked. The file therefore holds the venue's answer to the STOP submit until the test releases it;
without that the window opened by luck of scheduling — in the first run, two of the five tests
found the order already confirmed.

What still abandons the close is where the stop may rest after all — a refused cancel, the
resolution's ceiling, an absence read by reference — and that stays pinned below.

## When the answer to a protective write never arrives (#487)

`test_unresolved_protective_writes.py`. This is the expensive corner of #487 and it lives here
rather than in a transport suite, because the write that hurts when it is guessed at is the
CANCEL of a stop the venue is holding.

`_handle_cancel_response` branched on `is_rejected` alone, so an UNRESOLVED answer — the venue
never spoke — ran the entire success path: the order dropped from the resting list, the protective
stamp cleared, `order_cancelled` emitted, and the deferred close RELEASED. A market close then
goes out beside a stop that may still be resting, which is the double-fill the cancel-before-close
ordering exists to prevent, reached through a transport fault instead of a race.

The rule is that an unresolved write books nothing. The one thing booking nothing must not mean
is waiting forever: without a release the deferred close sits in `_deferred_closes` for the rest
of the session, and `close_position` is a no-op while it does — the position could never be closed
again, while the framework's own stop check re-requests the close on every tick.

| Test class | Pins |
|---|---|
| `TestAnUnresolvedCancelBooksNothing` | the order stays in its resting list, the stamp stays, the deferred close is NOT released and no close reaches the venue, the operation stays in flight so nothing races it, and the resolution is asking |
| `TestTheCeilingEndsTheWait` | at the ceiling the close is ABANDONED rather than left hanging, the operation is released so the order is not stuck for the session, the order itself is still not dropped, and the abandoned close ends with a `denied` · `close_withheld` row — the one it gets when it is withheld at the request (#362) |
| `TestACancelAnswerNamingNothingBooksNothing` | a cancel answered UNKNOWN — the venue answered and named nothing it cancelled — is treated as the open question it is: the stop and its stamp stay, the close keeps waiting, the operation stays in flight and the resolution asks. Kraken raises instead of answering this way (measured 2026-09-13), so it is the second adapter's case (#209) |
| `TestAnUnresolvedAmendWritesNoProvisionalValue` | the shadow price does not move, the provisional values stay parked, and the one-outstanding-amend guard stays closed |

Abandoning rather than releasing is the safe direction: the position stays open and, because the
cancel did not go through, still protected — and the operator is told the close they asked for did
not happen.

The mock's fault injector is what makes this reachable at all. `set_transport_fault(operation,
message, terminal=False)` raises a CLASSIFIED transient fault; a plain `ConnectionError` is
classified TERMINAL and produces REJECTED instead, so a test written against the old injector
would have asserted the wrong state while passing.

## When the venue carried the cancel out and only the answer was lost

`test_lost_cancel_answer.py`, the sibling of the section above. There the cancel never reached the
venue; here the venue cancelled the stop and the answer went missing. The next status read finds
the stop cancelled — and that read used to be booked as a broker REJECTION: a rejection row, the
cooldown, `on_order_rejected`, never `order_cancelled`, and the close parked behind the cancel was
never sent. Reproduced before the fix: five minutes on, stop-loss and take-profit both breached,
the position was open and no close had been submitted. The same branch booked a cancel or expiry
the venue made on its own as a rejection too.

The mock gains two venue behaviours for it: `lose_next_cancel_answer` carries the cancel out and
then raises a transient fault, and `end_at_venue()` ends a stop nobody asked to end, with or
without executed volume. Every class runs in both account models.

| Test class | Pins |
|---|---|
| `TestTheReadThatFindsTheStopCancelled` | no rejection is booked; the strategy hears `order_cancelled`; the parked close is sent exactly once and the position closes; the resolution stops asking; and when it is the resolution that names the stop cancelled, the ordinary poll books it at once |
| `TestTheCancelNeverArrived` | a resolution that names the stop still resting ends the operation and abandons the close, keeping the stop and its stamp; an ordinary poll answering "resting" changes nothing, because a query queued before the cancel may legitimately say so |
| `TestTheVenueEndedTheStop` | a cancel or expiry nobody asked for is booked as cancelled, the level goes back to the local check, and what the stop executed before it ended is booked |
| `TestAParkedCloseIsACloseOnItsWay` | `is_pending_close` answers true while the close waits, so a breached level is not counted as a new trigger on every tick |
| `TestACancelStampsItsOwnWrite` | `last_write_at` is the cancel's moment, so the settle window of a lost cancel answer runs from the cancel, not from the stop's submission |

## Running it

```bash
python -m pytest tests/autotrader/protective_levels/ -v
```
