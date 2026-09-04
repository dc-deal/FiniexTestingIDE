# Session-End Tests (#492)

What a live session does with what it still holds when it ends — and, more importantly, what
it no longer *claims* to have done.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/session_end/](../../../tests/autotrader/session_end/) |
| Profile | [configs/autotrader_profiles/backtesting/session_end_test.json](../../../configs/autotrader_profiles/backtesting/session_end_test.json) |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entries | `🧩 Pytest: Session End (#492)` · `🤖 AutoTrader: BTCUSD Mock - Session End (#492)` |
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
| `test_cancel_expires_the_order_locally` | A cancelled resting order leaves an EXPIRED record |
| `test_leave_does_not_expire_it` | Left standing means left in BOTH places — an order that can still fill is not expired |
| `test_the_cleanup_does_not_take_a_shutdown_mode` | The #356 scope boundary, pinned structurally |
| `test_the_shutdown_check_only_asks_about_flatness` | — |

**The resting-order fixture uses `DELAYED_FILL`, not `INSTANT_FILL`.** The instant mode fills a
LIMIT on submission, so the order would never rest and both orders-axis tests would pass with
nothing to act on — the fixture asserts it actually placed one.

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
| `test_an_open_position_becomes_the_edge_impact` | The block-edge disposition reads the open positions (#214) |
| `test_a_flat_block_reports_no_impact` | — |
| `test_an_unvalued_position_contributes_zero_rather_than_a_guess` | — |

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
| [tests/framework/reporting/test_block_splitting_report.py](../../../tests/framework/reporting/test_block_splitting_report.py) | `TestTheDispositionStillDistinguishes` — the block-edge disposition must keep ANSWERING, not just run: three cases that have to come out different, so a silently constant "GOOD" is caught |
| [tests/autotrader/integration/](../../../tests/autotrader/integration/) | The rewritten session-level contracts — SL/TP read from the position, and `test_no_exit_is_fabricated_at_session_end` |
| [tests/framework/config/](../../../tests/framework/config/) | The loader field-coverage guard added alongside this work |
| [tests/framework/reporting/test_live_session_summary.py](../../../tests/framework/reporting/test_live_session_summary.py) | The closing block names an open position in the HEADLINE, and says nothing about one when the session ended flat |

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
