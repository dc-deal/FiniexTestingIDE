# Capital Tests (#489)

What the bot may actually spend — as opposed to what the account happens to hold.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/capital/](../../../tests/autotrader/capital/) |
| Harness | `MockOrderExecution` in spot mode — no network, no config files, no tick data |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entry | `🧩 Pytest: Capital Allocation (#489)` |
| Architecture doc | [autotrader_capital_and_safety.md](../../autotrader/autotrader_capital_and_safety.md) — *Capital — what the bot may spend* |
| Record coverage | `TestTheCommittedFundsRecord` in `tests/framework/reporting/test_portfolio_report.py` — see [Reporting Pipeline Tests](../framework/reporting_tests.md); the report half lives with the builder it tests |

---

## What was wrong, and why the tests read the way they do

`PortfolioManager` moves a balance when an order **fills**. A venue holds the asset the moment
the order is **placed**. So the funds check read a balance that two unfilled orders could each
spend in full:

```
account            1000.00 USD
resting LIMIT BUY   0.012 BTC @ 49000  →  claims 588.94 USD
market BUY          0.010 BTC @ 50001  →  needs  501.31 USD
```

Both passed. The venue then refused the second one, and the bot could not explain the rejection
to itself — its own books still showed the money. In the simulation nothing refused it at all,
so the backtest stopped predicting the session, which is the one thing this framework exists to
prevent.

Two consequences shape the suite. The claim must be derived from the **executor's** order book
rather than the portfolio, so the tests exercise it through a real executor rather than a stand-in.
And a submission-time refusal is **returned**, not announced — `_notify_outcome` fires only from
the asynchronous resolution paths — so one test registers an outcome listener and asserts it is
never called.

## Suite Coverage

### `test_committed_funds.py` — what an unfilled order claims

| Test | What it verifies |
|---|---|
| `test_an_empty_book_claims_nothing` | No unfilled order → the balance is the balance |
| `test_a_resting_buy_claims_its_quote_including_the_fee` | The fee is part of what the venue holds, so it is part of the reserve — asserted as a property, because a second fee formula in a test is a second place for it to be wrong |
| `test_a_resting_sell_claims_its_base` | A sell spends base, and the amount is the lots themselves: no price, no fee |
| `test_the_excluded_order_does_not_reserve_itself` | `exclude_order_id` — the order being filled is still in its own collection while the fill runs |
| `test_margin_mode_reserves_nothing` | Margin's quantity is free margin, and its claim is #209's (the rule differs: a CLOSE releases margin) |
| `test_the_second_order_is_refused_while_the_first_still_rests` | The case the feature exists for, with `balance` and `committed` named separately in the message |
| `test_the_same_order_passes_when_nothing_is_committed` | Regression guard: unchanged behaviour without unfilled orders |
| `test_a_sell_is_refused_against_committed_base` | The base side of the same rule |
| `test_the_refusal_is_returned_and_announces_nothing` | No outcome event fires — the algo learns from the `OrderResult` it already holds |
| `test_the_refusal_is_recorded_in_the_order_history` | It is a real rejection, so the run's record carries it |

#### `TestBothPipelinesAgree` — parity measured, not assumed

| Test | What it verifies |
|---|---|
| `test_the_simulation_refuses_the_second_order_too` | The SIMULATION executor, same account and orders, refuses identically — the shared code path is a reading here, not a claim |
| `test_the_simulation_passes_it_without_a_resting_order` | The sim-side regression guard |
| `test_the_fill_site_reads_others_claims_and_excludes_the_filling_order` | The figure the fill-time check reads: the other orders' claims, never its own — asserted on the query with both orders in flight, because the mock fills a LIMIT regardless of price and cannot hold one resting while another fills |

#### `TestAFillTheVenueMadeIsBooked` — the fill-time check decides only where the executor IS the venue

Submission and fill compute the same figure only at the same price. 0.0195 BTC at the ask of 50001
costs 977.56 USD with the taker fee, which a 1000 USD account can pay; the order fills 3 % higher,
at 1006.88 USD.

| Test | What it verifies |
|---|---|
| `test_live_books_it_despite_our_shortfall` | A live session books the fill the venue made and reports the disagreement as an error, in both account models — refusing to book it left the venue holding coins the book did not. The margin case needs the market to move with the fill, because the margin check prices the current tick and the spot check the fill itself |
| `test_the_simulation_still_refuses_it` | The simulation plays the venue, and a venue refuses an order it cannot fund |

#### `TestASpotCloseIsCheckedLikeASell` — a close is funded like any order (#639)

A LONG of 0.01 BTC whose coins a SELL has since sold: closing it used to go through and leave the
base balance at −0.01, while a SELL of the same coins was denied.

| Test | What it verifies |
|---|---|
| `test_the_simulation_denies_the_close` | DENIED with `insufficient_funds` at submission; the base stays at zero and the LONG stays in the book |
| `test_the_live_executor_denies_it_too` | The same denial in a live session over the mock venue |
| `test_a_close_the_account_can_fund_goes_through` | The regression guard: a LONG whose coins are held closes as before |
| `test_closing_a_short_needs_the_quote_it_buys_back_with` | Closing a spot SHORT buys back with the quote, and a resting BUY's claim on it is counted |
| `test_coins_gone_before_the_fill_refuse_the_simulated_fill` | Coins leaving between submission and fill: the simulation refuses the fill (`REJECTED`) and the position stays |
| `test_live_books_a_close_the_venue_executed` | The same in a live session: the venue sold the coins, so the book follows it |
| `test_a_stop_the_account_cannot_fund_fires_once` | A breached stop whose close the simulated venue refuses is booked rejected once and not tried again; it counts no protective close |
| `test_margin_is_not_checked` | A margin close moves no coins and is not checked |

#### `TestWhatTheEndReleased` — an order the end booked gives its funds back

The end of a session or a scenario books its resting orders cancelled or expired and leaves them in
the active lists for the snapshot. Counting them still made the run's last report show funds as
committed that the venue had given back.

| Test | What it verifies |
|---|---|
| `test_a_resting_buy_the_session_end_cancelled_claims_nothing` | A live session's cleanup cancels a resting buy: the row is `cancelled`, the order stays in the active list, and it claims nothing |
| `test_a_resting_buy_the_scenario_end_expired_claims_nothing` | The same in the simulation, where the end of the data expires the order |

### `test_account_sufficiency.py` — a boot that can fund nothing

The criterion is the **AND** of both sides, and that is the part worth testing: a spot bot may
legitimately start holding only the base asset and open by selling — the Field Study funds both
sides on purpose — so an empty quote balance alone must not refuse a boot.

| Test | What it verifies |
|---|---|
| `test_an_account_that_can_neither_buy_nor_sell_is_refused` | Both sides' numbers appear, and never in scientific notation |
| `test_an_account_that_can_buy_passes` | One side is enough |
| `test_an_account_holding_only_base_passes` | The Field Study shape |
| `test_exactly_the_minimum_is_enough_on_either_side` | The boundary is `>=` — an account that can place exactly one order may |
| `test_a_missing_currency_reads_as_zero` | A balances dict that never mentions an asset holds none of it |
| `test_without_a_price_a_short_account_is_not_refused` | No price → the buy side is unknown, and an unknown side cannot complete the AND |
| `test_a_nonsense_price_counts_as_no_price` | `0.0` and a negative are treated as absent, not as arithmetic |
| `test_the_sell_side_still_decides_without_a_price` | `volume_min` is in base units, so that half needs no price |
| `test_the_absence_is_spoken_not_swallowed` | The caller says the check ran without a price — one that silently does not run reads exactly like one that passed |
| `TestTheValueMinimum` | A venue's minimum order value (`cost_min`, #608) raises the smallest order to `cost_min / price` on both sides where that is larger; without a price it cannot apply; below the volume's cost it changes nothing; a mock session reads the value its config declares, as the live adapter does |

## Where the price comes from, and why not earlier

The sufficiency check runs **after warmup** (`setup_pipeline` Phase 9), because that is the first
point in an AutoTrader boot where a price exists: no tick has arrived during startup, and the adapter
contract carries no price read at all. The reference is the newest warmup bar close. A strategy
with no bar workers produces no bars — then the buy side cannot be judged and the session is told
so.

## Running it

```bash
python -m pytest tests/autotrader/capital/ -v
```
