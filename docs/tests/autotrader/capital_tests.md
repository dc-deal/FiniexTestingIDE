# Capital Tests (#489)

What the bot may actually spend — as opposed to what the account happens to hold.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/capital/](../../../tests/autotrader/capital/) |
| Harness | `MockOrderExecution` in spot mode — no network, no config files, no tick data |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entry | `🧩 Pytest: Capital Allocation (#489)` |
| Architecture doc | [autotrader_architecture.md](../../autotrader/autotrader_architecture.md) — *Capital — what the bot may spend* |
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
so the backtest stopped predicting the live run, which is the one thing this framework exists to
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
| `test_the_fill_site_reads_others_claims_and_excludes_the_filling_order` | The figure the fill-time check reads: the other orders' claims, never its own — asserted on the query with both orders in flight, because the mock fills a LIMIT regardless of price and cannot hold one resting while another fills. A fill-time *refusal* is not producible here at all — submission and fill compute the same figure, only an outside balance change could separate them |

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

## Where the price comes from, and why not earlier

The sufficiency check runs **after warmup** (`setup_pipeline` Phase 9), because that is the first
point in a live boot where a price exists: no tick has arrived during startup, and the adapter
contract carries no price read at all. The reference is the newest warmup bar close. A strategy
with no bar workers produces no bars — then the buy side cannot be judged and the session is told
so.

## Running it

```bash
python -m pytest tests/autotrader/capital/ -v
```
