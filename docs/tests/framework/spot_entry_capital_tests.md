# Spot Entry Capital Tests

`tests/framework/test_spot_entry_capital.py` — how much capital a bot may commit to a new entry,
asked once and answered per account model (`AbstractTradeExecutor.get_free_entry_capital()`,
#502). Runs under the synthetic `framework/_root` suite.

## Why this file exists at all

Every CORE decision logic gated its entries on `AccountInfo.free_margin`. That figure answers the
question only in the MARGIN world. Measured against the Kraken spot config on 2026-09-23, its
value there is:

```
free_margin = free quote balance + unrealized P&L on the holdings - lots
```

The middle term is the defect. An unrealized gain on a coin is not cash and cannot be spent on the
next entry — and because the term can be negative as well, the error points BOTH ways, so no
choice of floor absorbs it. On a 1000 USD account holding 0.1 ETH bought at 3000:

| ETH price | free quote | `free_margin` | what the gate does |
|---|---|---|---|
| 3000 | 700.00 | 699.90 | roughly right |
| 6000 | 700.00 | 999.90 | offers 43 % more capital than exists |
| 2000 | 700.00 | 599.90 | withholds capital the account has |

The trailing `- lots` has its own cause: Kraken declares its margin in the quote currency at
leverage 1, so the margin formula takes the branch that multiplies no price and charges 0.1 ETH
as 0.1 USD.

The correct answer already existed — the executor's own funds check has computed it per account
model since #489. What was missing was a way for a DECISION to ask it.

Driven directly against the `PortfolioManager` and the `TradeSimulator` — one mark, no latency,
no scenario. The quantity is the subject, the same shape as `account_value_tests.md`.

## What Is Tested

### `TestTheQuantityTheBotsGateOnIsNotSpendableCash`

The defect, stated as the difference between two numbers rather than as a judgement. These cases
stay true after the fix — the `free_margin` formula is deliberately out of scope, and #497 owns
whether it should answer `None` at spot. They are the evidence that switching the gate was not a
no-op.

| Test | Description |
|------|-------------|
| `test_an_unrealized_gain_is_offered_as_capital_that_cannot_be_spent` | the coin doubles, the figure grows, and no cash entered the account |
| `test_and_a_decline_withholds_capital_the_account_really_has` | the same defect pointing the other way, which is what makes it uncorrectable by a different floor |
| `test_the_margin_used_it_subtracts_is_the_lot_size_not_a_value` | pins the second cause at its source: 0.1 ETH worth 300 USD is charged as 0.1 |

### `TestFreeEntryCapitalAnswersPerWorld`

One method, two account models — which is what lets a single configured floor serve both.

| Test | Description |
|------|-------------|
| `test_spot_answers_the_free_quote_balance` | a fresh spot account may commit what it holds in quote |
| `test_and_it_falls_by_exactly_what_was_spent` | the property the old quantity does not have |
| `test_a_spot_sell_reads_the_holding_valued_at_the_mark` | selling spends the coin; answering the free quote would refuse a sell for lack of money it does not need. Valued rather than counted, so one floor means one thing in both directions — and valued at the MID, because this is the valuation plane |
| `test_margin_answers_exactly_free_margin` | bit-identical, or every checked-in mt5 scenario moves for no reason |

### `TestTheBotsOwnRestingOrdersAreSubtracted`

A venue reserves on PLACEMENT while our balances move on FILL (#489). A gate that ignored this
would send an order the executor's own funds check then refuses — a rejection the bot could have
avoided asking for.

| Test | Description |
|------|-------------|
| `test_an_unfilled_buy_reduces_the_capital_a_second_one_may_use` | the claim lands the moment the order rests |
| `test_the_order_being_filled_can_be_excluded` | counting the order under execution would reserve it twice |

## What Is Not Tested Here

The `free_margin` formula itself, and whether `AccountInfo.free_margin` should be `None` at spot
rather than a misleading number — that belongs with #497's work on one honest set of account
figures. The safety layer's own use of the account model is #356 / #314.

## Related

- [`account_value_tests.md`](account_value_tests.md) — the sibling question, one account VALUE per model
- [`../autotrader/capital_tests.md`](../autotrader/capital_tests.md) — the #489 reservation this
  subtraction reuses, and the boot-time sufficiency check
