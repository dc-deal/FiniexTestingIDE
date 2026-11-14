# Order Endings Tests

`tests/framework/test_order_endings.py` — one status per way an order ends, in both pipelines,
and the counts taken from them. Runs under the synthetic `framework/_root` suite.

An order used to end in one of three words, and `rejected` covered everything that went wrong:
a lot size the executor refused before anything was sent, a refusal by the venue, an amend the
venue refused while the order kept working, an order the framework gave up waiting for. A
count, a cooldown and a report could not tell them apart, and the strategy's rejection hook
fired for orders the venue may have executed. This file pins the distinctions the executors
draw now. The statuses themselves are described in the order history's consumer document; how
an executor books a row is in [Execution Layer](../../architecture/architecture_execution_layer.md).

Not here: the live give-up and resolution paths (`cancelled` at the fill timeout,
`unaccounted`, `undelivered`) and the session-end cancels, which the live executor and session-end
suites cover against the venue mocks they already build.

## What Is Tested

### `TestTheStatusMapIsComplete`

`EXECUTION_STATS_FIELD_BY_STATUS` is what every count is read through, so it is held to its
subject in both directions: every status is counted or declared uncounted, every count names
the status it counts, and every model that passes the counts on — per unit, summed, per
currency, per run, per ledger row — carries all of them.

| Test | Description |
|------|-------------|
| `test_every_status_is_counted_or_declared_uncounted` | the map's keys are the whole enum |
| `test_every_status_count_names_its_status` | `orders_<status>` — the name IS the mapping |
| `test_every_order_count_is_a_status_a_submission_or_an_adoption` | no `orders_*` field counts something undeclared — beside the statuses only the submissions and the orders taken over at boot |
| `test_every_model_passing_the_counts_on_carries_every_count` | parametrized over the report models |
| `test_the_ledger_has_a_summed_column_for_every_count` | a count with no column is dropped at the ledger, one with the wrong reduction is folded wrong |

### `TestARefusalSaysWhoRefused`

| Test | Description |
|------|-------------|
| `test_a_lot_size_refusal_is_denied_and_never_submitted` | both pipelines, both account models: `denied`, counted as denied, not as submitted |
| `test_a_close_of_a_missing_position_is_denied_with_its_own_reason` | `position_not_found`, booked — it used to be `broker_error` and left no row |
| `test_the_stress_test_is_a_venue_refusal_and_is_heard` | the stress test plays the venue, so its refusal reaches the outcome listeners — the strategy's hook and the cooldown — as a real one does |

### `TestEveryEndingHasARowInTheSimulation`

| Test | Description |
|------|-------------|
| `test_a_strategy_cancel_ends_cancelled_by_the_strategy` | the cancel gets its closing row, and the cancellation event carries it |
| `test_the_data_end_expires_resting_and_travelling_orders_alike` | an order still in the latency queue when the data ends used to end without any row |

### `TestAnOrderSentAtTheSessionEndInTheSimulation`

An order the strategy sends from `on_session_end` ends as it ends live (#642). The simulation's end is
run in the order its tick loop runs it; `test_trend_channel_reference.py` runs it through the loop
itself.

| Test | Description |
|------|-------------|
| `test_a_close_sent_from_the_event_ends_flat_and_is_heard` | in both account models the close fills at the last bid and the logic hears `executed` after the session-end event |
| `test_a_market_order_sent_from_the_event_fills_at_the_last_quote` | an open fills at the last ask, in both account models |
| `test_a_resting_order_sent_from_the_event_expires_and_is_heard` | a limit sent then expires and is reported as `expired` |
| `test_a_cancel_sent_from_the_event_is_applied` | a cancel the strategy asks for then is applied — `cancelled` by the strategy — where it used to wait for a tick that never came and the order expired |
| `test_an_order_sent_from_the_outcome_hook_is_withdrawn` | the session is over: cancelled by the framework at the session end, never filled and not reported |
| `test_an_order_already_on_its_way_still_expires_with_the_data` | only what the strategy sent from the event is settled; an order in flight before it expires with the data, unreported |
| `test_after_an_emergency_end_nothing_is_settled` | as a live stop does not wait through an `EMERGENCY` end, the close is withdrawn and the position stays open |

### `TestAProtectiveExitInTheSimulation`

The simulation fills a breached stop-loss or take-profit with a synthetic close in the breaching tick:
a stop at that tick's market, a target at its level.
That close is an order in the backtest as live's is in a session, and it waits as live's does.

| Test | Description |
|------|-------------|
| `test_it_counts_as_a_submitted_order` | counted as submitted where live counts it — uncounted, a backtest whose exits were stops reported more orders executed than submitted |
| `test_it_stands_aside_while_the_strategys_close_is_on_its_way` | a breach while the strategy's close is in the latency queue fills nothing; the close arrives and fills at the market, as live's would. Filling the level beneath it made the position vanish under the close, which then arrived to a `rejected` row live can never produce |

### `TestEveryEndingHasARowLive`

| Test | Description |
|------|-------------|
| `test_a_strategy_cancel_ends_cancelled_by_the_strategy` | the same row as in the simulation, from the venue's confirmation |
| `test_an_order_the_venue_ended_unasked_is_the_venues` | a status read that finds a resting order cancelled or expired, with no cancel of ours behind it, books the venue as the initiator — and the venue's own word decides between the two statuses |
| `test_an_order_ended_after_a_partial_fill_ends_as_that_fill` | what the order executed is its fill and the rest never happened — one `executed` row and no `order_cancelled`, where a second, `cancelled` row used to repeat the executed size |
| `test_an_order_still_travelling_at_the_session_end_is_unaccounted` | the venue may hold it — it used to end without any row |
