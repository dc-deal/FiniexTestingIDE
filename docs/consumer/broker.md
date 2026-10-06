# The broker a run executed against

A run's figures were computed under rules that none of those figures contain: how many units of
an asset one lot is, the smallest order the venue accepts, the price step an instrument quotes
in, whether the account carried leverage and at what level it would have been closed out. Without
them a P&L cannot be checked, and a second run that would reproduce it cannot be set up. This
section is the broker configuration the run executed against, as the run itself recorded it — one
row per broker, each with the symbols it traded.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/broker
```

**What is not here:** what anything cost. This is the specification, not the money — a trade's
fees are on [trade history](/api/v1/docs/trade-history), a period's totals on
[booking periods](/api/v1/docs/booking-periods). It is also not the archive: the
`/api/v1/brokers/…` routes answer which ticks and bars this server holds for a broker, which is a
different question — see [market data](/api/v1/docs/market-data).

## One row is one broker

```json
{ "key": ["broker_type"], "units": [ ... ] }
```

A backtest produces one row per broker its scenarios read, each with that broker's scenario list
and the symbols those scenarios traded. An AutoTrader session produces one row: its own broker and
the single symbol it traded. See [row keys](/api/v1/docs/row-keys).

`scenarios` is therefore **empty on a live session** — an empty list, not a null, because a
session has no scenario grid rather than an unrecorded one. [Nulls](/api/v1/docs/nulls) has the
difference.

On a backtest, `scenarios` names the scenarios that ran and produced results. It is not the list
the configuration declared: a scenario that was disabled, or refused before it started, is not
here. The declared list is in [config](/api/v1/docs/config), and every scenario with its outcome,
failures included, is on [scenario details](/api/v1/docs/scenario-details).

`broker_type` is the identifier the market-data routes take as `{broker}`, so both sides of this
API name a broker with the same word. A broker the archive holds nothing for answers
`broker_not_found` there — a statement about the archive, not about this run.

## The account the run traded under

| Field | Meaning |
|---|---|
| `company` · `server` | the venue, and which of its servers |
| `market_type` | the market its instruments belong to, such as `forex` or `crypto` |
| `trade_mode` | the **account's** mode, as the venue names it — `demo`, `live`, `real` |
| `leverage` | the account's leverage; `1` means none |
| `margin_mode` | how margin is calculated — `retail_netting`, `retail_hedging`, `exchange`, `none` |
| `margin_call_level` · `stopout_level` | the two thresholds, in percent |
| `hedging_allowed` | whether opposite positions on one symbol are allowed |

**`trade_mode` is the account's mode, never the run's.** A backtest reads the specifications of a
real account and trades on none of them, so `live` here says nothing about whether orders were
placed. The field that answers that is `orders_to` — see [run kinds](/api/v1/docs/run-kinds).

**Where `leverage` is 1 there is no margin to calculate.** `margin_mode` then reads `none` and
both thresholds read `0.0`. That is the configuration being correct, not a figure going missing.

## What one symbol row says

| Field | Meaning |
|---|---|
| `volume_min` · `volume_max` · `volume_step` | the smallest and largest order size, and the increment between them |
| `contract_size` | how many units of the base asset one lot is — 100,000 on a forex lot, 1 where one lot is one unit of the asset |
| `tick_size` | the smallest price movement the instrument quotes |
| `base_currency` · `quote_currency` | what is bought, and the currency it is priced in |
| `swap_long` · `swap_short` | the overnight rate a position pays or earns past rollover |

`base_currency` and `quote_currency` are what the broker declares for that symbol, read from its
specification rather than derived from the symbol's name. Use the fields; do not split the string.

**The two swap rates carry no unit here.** Their unit is the broker's swap mode — points at one
venue, a percentage at another — and the mode is not on this row, so they must not be rendered as
an amount of money. Both read `0.0` where the venue charges no swap at all, which is the case on
a spot account. What a held position actually paid is `swap_cost`, on
[booking periods](/api/v1/docs/booking-periods) and on each trade.

## Two identities, and what each is for

`config_hash` is eight characters over the broker configuration's content — its symbols **and**
its fee structure. The fee structure is in it deliberately: a fee rate moves realised P&L on every
trade, so two runs priced differently are different runs and the record has to be able to say so.
It once covered the instruments alone, under which reading those two runs hashed identically. It
is empty where the broker configuration carries no identity of its own.

`broker_config_id` is where an AutoTrader session **froze** the configuration it traded with: the
symbol specifications as the venue's cache held them, the seed's fee structure, and the fee tier
the venue reported. That is the content the hash only digests in eight characters, so a later
backtest of the same window can read the frozen copy instead of whatever the cache holds by then.
It is empty on a simulation row, which reads the archive's broker files as it runs, and on a
session recorded before contract 19 — see [the contract log](/api/v1/docs/contract-log).

**Neither is a handle.** No route on this API takes a configuration id as a parameter. They are
for comparing: two rows carrying the same value executed under the same configuration.

## When this section is absent

A session that failed at startup before its broker configuration was resolved writes no broker
section, while its other sections are still there. That is `artifact_not_produced`, not a missing
run. The four 404s one run can answer with, and what each asks of you, are in
[errors](/api/v1/docs/errors).
