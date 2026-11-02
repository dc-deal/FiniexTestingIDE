# Session-End Tests (#492)

What an AutoTrader session does with what it still holds when it ends — and, more importantly, what
it no longer *claims* to have done.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/session_end/](../../../tests/autotrader/session_end/) |
| Profile | [configs/autotrader_profiles/mock/session_end_test.json](../../../configs/autotrader_profiles/mock/session_end_test.json) · the stop's wait: [session_stop_awaited_order.json](../../../configs/autotrader_profiles/mock/session_stop_awaited_order.json) (spot) · [session_stop_awaited_order_margin.json](../../../configs/autotrader_profiles/mock/session_stop_awaited_order_margin.json) |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entries | `🧩 Pytest: Session End (#492)` · `🤖 AutoTrader: BTCUSD Mock - Session End (#492)` · `🧪 AutoTrader: Session End, Awaited Order (spot)` · `🧪 AutoTrader: Session End, Awaited Order (margin)` |
| Architecture doc | [session_end_policy.md](../../architecture/session_end_policy.md) — the full treatment, with the order-type map |
| Accounting rule | [reporting_pipeline.md](../../architecture/reporting_pipeline.md) — *Realised vs valued* |

---

## What was wrong, and why the tests read the way they do

The cleanup used to close every open position **in our book only**: it built a synthetic close
order and filled it from the last tick. Nothing was submitted. So a session ending with an open
position reported a **realised exit nobody executed** — and at spot it also moved `base → quote`
in the balance ledger, so the summary printed `BTC 0.0` while the coin sat at the broker.

That has one consequence for the suite that is easy to miss: several existing tests used the
fabricated exit as their **observation channel**. `test_close_reason_is_scenario_end` asserted
the fabricated record existed; the SL/TP tests read the configured levels off the closing trade
record. Those were rewritten to read the position, where the levels always lived — which needs
no exit at all.

## Suite Coverage

### `test_session_end_policy.py` — what a profile may declare

| Test | What it verifies |
|---|---|
| `test_the_default_policy_resolves_unchanged` | The shipped default is `cancel` / `leave` — the market norm |
| `test_the_two_axes_are_independent` | Leaving orders says nothing about the position |
| `test_it_refuses_and_names_the_issue_that_unblocks_it` | `positions: 'close'` refuses, naming #487 |
| `test_it_does_not_fall_back_to_leave` | It refuses rather than quietly doing something else |
| `test_a_profile_may_tighten_against_a_leave_broker` | Tightening is always allowed |
| `test_a_profile_may_not_loosen_against_a_cancel_broker` | Loosening needs the broker's posture; both files named in the message |
| `test_the_attempt_is_refused_rather_than_ignored` | The `dry_run` near-miss (#304) in the other axis |
| `test_leave_plus_operator_confirm_unattended_is_refused` | The incoherent pair, with all three ways out in the message |
| `test_it_is_allowed_when_an_operator_is_declared_present` | `--attended` resolves it |
| `test_it_is_allowed_when_the_algo_accounts_for_the_orders` | An `on_cold_start` (#493) lifts exactly that refusal |
| `test_it_is_allowed_with_automatic_adoption` | `adoption_mode: 'auto'` resolves it |
| `test_leave_with_cold_start_disabled_is_refused` | Nothing would ever adopt them back |
| `test_cancel_never_triggers_the_pair_check` | The pair only exists because the orders survive |

### `test_session_end_cleanup.py` — what the cleanup does

| Test | What it verifies |
|---|---|
| `test_the_position_is_still_open_afterwards` | The cleanup finishes ORDERS, not positions |
| `test_no_trade_record_is_produced` | **The old contract, inverted** — that record was a fabricated exit |
| `test_no_close_carries_the_reserved_reason` | Nothing produces `SCENARIO_END` any more |
| `test_the_spot_balance_still_shows_the_coin` | The sharper half: the balance line no longer contradicts the account |
| `test_it_survives_a_session_that_never_saw_a_tick` | The #355 abort case — no price, no crash |
| `test_a_policy_left_position_is_not_an_error` | Without this, every clean session end grades `FINISHED_WITH_ERRORS` and exits 3 |
| `test_an_unexpected_survivor_is_still_an_error` | Where flatness was expected, "orphaned" is still the right word |
| `test_a_flat_session_is_clean_either_way` | — |
| `test_cancel_books_the_order_cancelled_by_the_framework` | A resting order the venue confirms cancelled ends `cancelled`, by the framework, at the session end — it used to be recorded `expired`, a claim the venue never made |
| `test_leave_does_not_end_it` | Left standing means left in BOTH places — an order that can still fill is not recorded as ended |
| `test_cancel_reaches_the_venue_and_ends_it_locally` | The same for a resting STOP, and read from the mock's own cancellation record rather than from our book — a cleanup that only forgot the order locally looks identical from our side |
| `test_leave_keeps_it_in_both_places` | A stop left standing by policy is neither cancelled at the venue nor ended here |
| `TestAnUnconfirmedCancelIsNotAnExpiry` | A cancel the venue refuses, with the read after the refusal unanswered as well, leaves an `unaccounted` record and an error saying what the operator should expect to find; a confirmed cancel is booked `cancelled` |
| `TestACancelAlreadyOnItsWay` | A cancel of the strategy's that is already on its way is not sent a second time: the order's status is read, and it ends as the strategy's cancel. An order that filled before its session-end cancel is booked filled from the read after the refusal. Money cases in both account models |
| `test_the_cleanup_does_not_take_a_shutdown_mode` | The #356 scope boundary, pinned structurally |
| `test_the_shutdown_check_only_asks_about_flatness` | — |

**The resting-order fixture uses `DELAYED_FILL`, not `INSTANT_FILL`.** The instant mode fills a
LIMIT on submission, so the order would never rest and both orders-axis tests would pass with
nothing to act on — the fixture asserts it actually placed one.

**The stop fixture holds a stop and NO limit, and that is the whole point of it (#500).** Phase 1
of the live cleanup was written for `_active_limit_orders` alone, and its guard read that list's
truthiness in *both* branches — so a session holding only stops ran neither: nothing cancelled at
the venue, nothing expired locally, not even a log line. It was documented as harmless because the
live submit gate refused STOP, which was true of the SUBMIT path and false of the situation: boot
adoption files a venue-reported stop into that world by design. The lesson generalises past this
branch — a guard written on one of two collections reports nothing when only the other is
populated. Kraken states the consequence themselves: a `stop-loss-limit` is not linked to a
position and must be cancelled by hand once the position is gone.

### `test_session_end_accounting.py` — realised and valued, kept apart

| Test | What it verifies |
|---|---|
| `test_buying_does_not_produce_a_drawdown` | **The phantom-drawdown guard** (see below) |
| `test_the_margin_formula_would_have_reported_a_phantom` | Pins WHY the spot branch exists, so removing it fails loudly |
| `test_no_sample_without_a_price` | A SPOT position nobody could price contributes no drawdown — the earlier version built neither the position nor the spot mode its docstring named |
| `test_the_curve_keeps_one_scale_across_a_close` | The close path and the run-end sample measure the SAME quantity (see below) |
| `test_the_open_position_is_reported` | It reaches the model with its mark |
| `test_it_is_not_a_completed_trade` | Trade count, win rate and `net_profit` are untouched by it |
| `test_its_value_is_in_the_wealth_view` | `final_equity` carries it, and only it |
| `test_the_policy_travels_with_the_row` | — |
| `test_an_unvalued_position_says_so` | No tick, no mark, no invented price |
| `test_the_wealth_view_says_whether_it_is_a_valuation` | `final_equity_valued` — the figure says whether it IS a mark-to-market |
| `test_no_adopted_flag_is_offered` | The row deliberately has no `adopted` field; its only derivation was a structural constant |

### `test_session_end_reporting.py` — the surfaces an operator reads

| Test | What it verifies |
|---|---|
| `test_the_balances_are_rendered` | **The console guard** (see below) |
| `test_the_open_position_is_rendered` | Id, entry and mark all appear |
| `test_the_wealth_view_is_labelled_and_separate` | `net_profit` says "(realised)"; the mark says "unrealised" |
| `test_the_policy_is_stated` | A position left by decision is distinguishable from a missing one |
| `test_a_truly_empty_unit_still_says_nothing_happened` | The early exit is right where there is genuinely nothing — only there |
| `test_an_unvalued_position_is_marked_as_such` | — |
| `test_the_equity_is_not_called_marked_to_market_when_nothing_was_marked` | Two lines that used to contradict each other |
| `test_it_is_called_marked_to_market_when_it_is_one` | The other direction, so the label is not simply removed |

### `test_broker_truth_at_session_end.py` — the venue's account at the end (#362)

Drives `AutotraderMain._shutdown` with its collaborators replaced, so the ORDER of the steps is what
is checked.

| Test | What it verifies |
|---|---|
| `TestTheEndRead` | The venue is asked after the session's orders are handled and before the order-event stream closes; a read that fails — a refused credential here — is logged and never keeps the stream open |
| `TestTheFieldStudysEnd` | The field study's capture closes after the session-end read it copies in, and closes even when its REST telemetry cannot be written |

### `test_signal_session_end.py` — what a Ctrl+C or a SIGTERM tells the session (#348)

A signal used to stop the loop and nothing more, so the decision logic's session-end event and the
closing message read "tick source exhausted" — the answer for a replay that ran out of data.

| Test | What it verifies |
|---|---|
| `test_a_sigterm_asks_for_a_normal_end_with_its_reason` | A supervisor's stop names itself, at NORMAL severity |
| `test_a_ctrl_c_asks_for_a_normal_end_and_keeps_its_shutdown_mode` | The operator's stop names itself; the shutdown mode is unchanged |
| `test_an_earlier_request_is_kept` | A hard stop's EMERGENCY request is not overwritten — it decides the shutdown mode |
| `test_a_signal_before_the_executor_reaches_it_once_it_exists` | A Ctrl+C during the start is remembered and handed over |
| `test_an_end_asked_for_before_the_first_pass_lets_no_decision_run` | The loop makes no pass at all, so no order follows a stop |

### `test_stop_waits_for_orders.py` — a normal stop waits for the orders on their way (#630)

Everything is provoked from outside, on a mock venue: the answer to a submit is held back after the
venue acted, so the order is executed while its answer is on the wire. The answer comes in the shape
of a venue like Kraken (a reference, and the fill on a later read) and of the stock mock (the fill in
the answer). Where money moves, both account models run too. The whole-session classes use a probe logic
that sends an order and asks for the end on one tick.

| Class | What it verifies |
|---|---|
| `TestAnAwaitedOpenIsBookedByTheWait` | the fill reaches the book, the balances and the logic before the session-end event, whether the answer arrives during the wait or already lay unread |
| `TestAnAwaitedMarketClose` | the close's fill ends the position and books the trade; a close with no answer leaves the position open, unaccounted and named |
| `TestARestingOrderWithNoReferenceYet` | the wait lasts until the reference came, and the cleanup then cancels with it, in both account models |
| `TestNothingAwaitedCostsNothing` | no pass and no line, also with a resting order the venue confirmed; a pass stamps the liveness and touches no decision, no worker, no reconcile, no state frame and no signal inbox |
| `TestTheBoundRunsOut` | unaccounted with `session_end`, one ERROR per order, no fill invented, the logic hears it before the session-end event, one alert naming the order, the worker stopped before the first booking (by the order of the calls), a late answer changes nothing booked. The fill timeout's road still applies: a last read books a fill, a confirmed cancel books a cancel, an executed part is booked, and a venue that stopped answering leaves the outcome unknown and never a refusal |
| `TestAFillTimeoutInsideTheWait` | an order whose fill timeout falls inside the wait goes the wait's road, in both account models: unaccounted with `session_end`, an ERROR, the alert; a submit still queued never goes out and is withdrawn as cancelled |
| `TestTheEndsThatDoNotWait` | a hard stop's EMERGENCY and an input that ran out go straight on; a hook that turns the end into an emergency ends the wait; what these ends leave is an ERROR, no longer a warning and exit 0 |
| `TestABoundaryCrossedDuringTheWait` | the wait's clock crosses midnight from the last event, and the fill is booked into the day it opened; the answer is released once that clock passed midnight, a condition rather than a timer |
| `TestAHookThatSendsAnOrderDuringTheWait` | the same bound covers the new order |
| `TestTheRowsTheStopReads` | what each row says about an open, a close, an acknowledged order and a resting one |
| `TestAWholeSessionWithAnAwaitedOrder` | through a session's records: the order booked, its position valued though no tick follows the fill, one booking period and no phantom ones up to today, the fill stamped near the last event, the hooks in order, exit 0 |
| `TestAWholeSessionWhoseBoundRunsOut` | through a session's records: exit 3 on the ERROR, the order unaccounted, no position, the hooks in order |
| `TestTheCleanup` | the worker stops before the first booking (by the order of the calls) and a queued submit never goes out and ends cancelled, an answer that came is read before anything is booked, a read that fails still cancels the resting orders |
| `TestTheNoteAfterTheCleanup` | a fill the cleanup found reaches the note at spot. At margin the note carries no book, which this pins as well (#209) |
| `TestTheBudget` | in both account models: a declared kill leaves the reserve and every join and the alert flush their time, read off the bound the wait begins with, or leaves no pass at all; with no time left no read and no cancel goes out, and each such order takes the unconfirmed route with a sentence saying why; a stop counts from the first signal, or from its first step where nobody signalled. A worker still inside a call doubles what the next call may cost, and the executor's ladder counts the adapter's socket timeout where broker_transport sets it apart |
| `TestTheEndReadUnderTheBudget` | with no time left each part stays unread and is named, with time for one attempt each part gets one, with time for the ladder a healthy venue answers, and the read at the start is untouched |
| `TestAWholeSessionWithAVenueThatStoppedAnswering` | the parts stay unread and named, exit 3, and the ledger row is written |
| `TestAWholeSessionWithNothingAwaitedUnderAShortKill` | with nothing on its way the stop still opens its budget: the venue read keeps to it, each part stays unread and named, exit 3, and nothing was waited for; in both account models |
| `TestAnOrderSentAtTheSessionEnd` | a close sent from `on_session_end` is waited for, its fill booked and the trade in the books before the cleanup, in both account models and answer shapes; the logic hears nothing after the session-end event; both waits keep to one bound; after a wait that ran out nothing more goes out, and the event's order ends cancelled |
| `TestAPartAtTheRunOut` | a part the venue's last answer shows executed is booked as the order's fill and the rest is unaccounted, with one ERROR naming both, the alert's line and the closing outcome counting it filled in part; in both account models |
| `TestAnOrderThatNeverLeft` | a submit queued behind an unanswered order and a hook's order sent after the worker stopped never reach the venue: cancelled by the framework, a WARNING, not in the alert, heard before the session-end event; in both account models |
| `TestTheLeavePolicy` | under `orders: "leave"` a resting order with no reference is booked unaccounted with an ERROR, not counted open at the venue; in both account models |
| `TestAnOrderAtTheResolutionCeiling` | an order nothing asks about any more is not waited for, and the cleanup books it unaccounted with an ERROR; in both account models |
| `TestARestingOrderWithACancelOnItsWay` | its age is the age of its cancel, not of its life |
| `TestAHookThatClosesOnTheFill` | an open and its close share one id and count as two orders, each with its line in the journal; in both account models |
| `TestASlowPass` | `settled after` counts the pass that held a read of the venue |
| `TestWhatTheStatusAsksTheSession` | how many orders the cleanup will cancel, by the session-end policy, and what asked for a stop no signal asked for; a signal keeps its word |
| `TestWhatTheWaitCameTo` | what the closing alert's line reads off the order records of a real executor: an open and a close that filled, a bound that ran out and a resting order the venue took, in both account models, and no status at all with nothing in flight |

## The two guards worth knowing about

**The phantom drawdown.** The run-end equity sample must use the SPOT portfolio value, not the
margin-style `balance + unrealized_pnl`: in spot mode `balance` is the QUOTE balance alone, so a
held coin contributes only its unrealised gain and never its value. Measured on a 1000 USD
account buying 0.01 BTC:

```
a purchase, valued two ways
  spot-aware        →    1.57 USD   (fee + spread, real)
  margin formula    →  603.15 USD   (the purchase itself, a phantom)   384x

a fall of ~150, measured across a CLOSE with a pre-existing holding
  one scale         →  151.58 USD   (what the portfolio actually lost)
  two scales        → 1251.58 USD   (a quote-scale trough under a portfolio-scale peak)
```

`test_the_margin_formula_would_have_reported_a_phantom` pins the *disagreement* rather than a
number, so someone simplifying `sample_equity()` back to `_calculate_equity()` gets a red suite
instead of a 60 % drawdown in every spot report.

`test_the_curve_keeps_one_scale_across_a_close` closes the window on **both** sides, and that
took three attempts worth recording. A buy-then-close needs a base holding the account had
BEFORE it traded, or the two formulas coincide once the position is sold. Even then they agree,
because the run-end sample lands last and a point that only RAISES the maximum produces no
drawdown. And a one-sided `> 100` passes against the mixed version too — it reports 1251. Only
the two-sided window separates them.

**The console early exit.** `total_trades == 0` printed "No trades executed" and RETURNED,
skipping balances, costs and the position the unit was holding. A buy-and-hold run reported
nothing at all — and the case only became reachable once the run end stopped force-closing.

## Related coverage outside this suite

| Where | What |
|---|---|
| [tests/framework/reporting/test_block_splitting_report.py](../../../tests/framework/reporting/test_block_splitting_report.py) | `TestTheDispositionStillDistinguishes` — the block-edge disposition must keep ANSWERING, not just run: a flat block, a holding block, two symbols cutting differently, the unrealised P&L at the edge and an unvalued position all have to come out different, so a silently constant "GOOD" is caught. The block-edge cases moved here when `process_block_boundary` was retired |
| [tests/autotrader/integration/](../../../tests/autotrader/integration/) | The rewritten session-level contracts — SL/TP read from the position, and `test_no_exit_is_fabricated_at_session_end` |
| [tests/framework/config/](../../../tests/framework/config/) | The loader field-coverage guard added alongside this work |
| [tests/framework/reporting/test_autotrader_session_summary.py](../../../tests/framework/reporting/test_autotrader_session_summary.py) | The closing block names an open position in the HEADLINE, and says nothing about one when the session ended flat |

## When to Touch This Suite

- **A new `session_end` value** — extend the policy tests; a value that is declared but not
  built must refuse, never fall back
- **`positions: 'close'` gets built (#487)** — `test_it_refuses_...` inverts, and the close needs
  its own coverage for the synchronous drain and the timeout
- **The equity sample changes** — the two phantom guards are the ones to read first
- **The console layout changes** — the assertions are substring-based on purpose (the real
  `ConsoleRenderer` is used, colour codes and all), so a reworded line fails and a restyled one
  does not

---

**Tests and documentation for this area are required: the architecture doc carries the *why*,
this file carries the *what*, and the accounting rule lives in the reporting-pipeline doc.**
