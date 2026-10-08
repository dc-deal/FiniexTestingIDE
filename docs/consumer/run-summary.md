# Run summary

Every other report section answers its own question well, and none of them answers "how did this
run do". Assembling that headline yourself means opening several sections and re-deriving the
figures — and two consumers that re-derive the same KPI disagree about it, usually over a rate
that cannot be averaged or a streak that spans two accounts. This section is the one object the
run composes once from its own section aggregates: money-denominated figures per account
currency, counts that have no currency run-wide.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/run-summary
```

**What is not here:** the per-unit rows these figures are folded from
([portfolio](/api/v1/docs/portfolio)), the trades behind them
([trade history](/api/v1/docs/trade-history)), the money booked per period
([booking periods](/api/v1/docs/booking-periods)), and the per-unit order counts
([execution stats](/api/v1/docs/execution-stats)).

## Composed, never re-derived

The figures here are taken from the sections that already computed them — the portfolio roll-up
and the trade analytics — rather than recomputed from the trades. So a KPI on this response and
the same KPI on the section it came from cannot disagree, and the one you read first is not the
one that happens to win.

## Two lists, two keys

```json
{ "keys": { "currencies": ["currency"], "units_absent": ["name"] } }
```

`currencies` is one row per account currency; nothing is ever summed across them. `units_absent`
is keyed like every other per-unit row, so it joins against the roster on
[scenario details](/api/v1/docs/scenario-details) and the rows on
[portfolio](/api/v1/docs/portfolio). See [row keys](/api/v1/docs/row-keys).

## What one currency row says

| Field | Meaning |
|---|---|
| `net_pnl` | the realised result in this currency |
| `total_trades` · `winning_trades` · `losing_trades` | what the currency's accounts **closed** |
| `win_rate` · `profit_factor` | the two rates, with the same undefined-versus-zero rules as on [portfolio](/api/v1/docs/portfolio) |
| `gross_profit` · `gross_loss` | the summed winners and the summed losers |
| `total_fees` · `fees_charged` | closed-trade fees, and everything charged including open positions |
| `unrealized_pnl` · `open_position_count` | what was still held when the run ended |
| `final_equity` · `total_final_equity` · `total_initial_balance` · `unit_count` | the account figures |
| `account_max_drawdown` · `max_equity` · `account_max_dd_pct` · `account_max_drawdown_unit` | the decline |
| `expectancy` · `avg_win_r` · `avg_loss_r` · `r_trade_count` · `r_win_count` · `r_loss_count` | the R figures |
| `avg_mae_winners` · `avg_mae_losers` · `avg_mfe_losers` · `largest_mae` · `largest_mfe` | the excursions |
| `avg_trade_duration_s` | the mean trade duration, in seconds |
| `max_consecutive_wins` · `max_consecutive_losses` | the longest streaks |

The fee split and the rule that a trade realising exactly nothing is neither a winner nor a loser
are stated on [portfolio](/api/v1/docs/portfolio), which is where those figures come from.

## A rate carries the halves it is a quotient of

`profit_factor` comes with `gross_profit` and `gross_loss`, and that is not redundancy. A rate
cannot be folded out of two rows, while its components can be summed on any level — so without
them a session's profit factor is not recoverable from its periods, and a deployment's is not
recoverable from its sessions. `win_rate` already had the property through `winning_trades` and
`total_trades`; these give it to the second rate.

They are the same two figures [portfolio](/api/v1/docs/portfolio) carries as `total_profit` and
`total_loss`, under the names this row folds them under.

Both read `0.0` on a record written before they were added, so a profit factor standing beside two
zeros is a run's age rather than a contradiction.

## The decline, and why the share is carried rather than computed

`account_max_drawdown` is the amount, `max_equity` the peak it fell from, `account_max_dd_pct` the
share it was — **all three from the same account**, named by `account_max_drawdown_unit`.

The share is carried rather than derived from the other two because it was measured against the
peak standing at the time. Dividing the stored amount by the stored peak is a different
calculation and answers a different question. Where no account declined, the trio names the first
account and its peak.

Over several accounts the trio is the deepest account's, never a sum.
[Portfolio](/api/v1/docs/portfolio) has the full rule; this row carries the same figures.

## Realised and valued, kept apart

`net_pnl` is realised. `unrealized_pnl` is what was still held, marked at the last tick, and it is
never folded in.

The separation matters most to anything that ranks runs. A ranking on `net_pnl` alone puts a
variant still **holding** a winner below one that closed it — the same distortion in the other
direction as closing every position at the run's end would cause.

## One account's figure, and the sum beside it

A backtest of many scenarios is many accounts; a live session is one. `unit_count` on the currency
row says how many are folded into it, and the figures follow from that:

- `final_equity` is the closing equity of the **one** account — and null where the currency spans
  several, because no account ever held their sum
- `total_final_equity` is that sum, beside the capital it started from, `total_initial_balance`

See [nulls](/api/v1/docs/nulls) for how to render the absence, and
[run kinds](/api/v1/docs/run-kinds) for why a unit is an account.

## The R figures, and when they are null

`expectancy` is the mean R — the figure a sweep ranks on. `avg_win_r` and `avg_loss_r` are the
mean winning and losing trade in R, and each is **null when there was no R-defined trade of that
kind**: a run with no R-defined loser has no average loss in R, and `0.0` would claim one.

`r_trade_count`, `r_win_count` and `r_loss_count` say how many trades the R figures rest on. A
small count beside a striking expectancy is the thing to show your reader.

## A streak belongs to one account

`max_consecutive_wins` and `max_consecutive_losses` are the longest run of one **account**, not of
the run's trades in time order. Over several scenarios the trades interleave, so A's win, B's win
and A's win read as a streak of three that no account ever had. The correction is dated in
[the contract log](/api/v1/docs/contract-log).

## Run-wide counts

The order counts — one per way an order starts or ends: `orders_submitted`, `orders_adopted`,
`orders_executed`, `orders_denied`, `orders_rejected`, `orders_cancelled`, `orders_expired`,
`orders_undelivered`, `orders_unaccounted` — and `sl_tp_triggered` are counts, so they carry no
currency and stand outside the per-currency rows. `unit_count` at this level is the run's unit
count — scenarios in a backtest, 1 in a live session.

What each count means, and the same counts per unit, are on
[execution stats](/api/v1/docs/execution-stats); executed over submitted and adopted is
`execution_rate_pct` on the [aggregated portfolio](/api/v1/docs/aggregated-portfolio).

## Which units are missing from the figures, and why

A run of ten scenarios with two refused reads as a run of eight unless the response says
otherwise. These fields say it:

| Field | Meaning |
|---|---|
| `units_declared` | every unit the configuration names, switched-off ones included |
| `units_disabled` | the ones switched off and never attempted |
| `units_absent` | the ones attempted that produced nothing, each with its reason |
| `unit_count` | the ones that produced figures |

Wherever they are all stated, `units_declared` equals `units_disabled` plus the length of
`units_absent` plus `unit_count`. Assert it: the left side comes from the configuration and the
right from the results, so the equation compares two sources rather than restating one.

`units_declared`, `units_disabled` and `units_absent` are **null on a run recorded before they
existed** — not stated, rather than zero. A zero there would be a figure the equation then
disproves. See [nulls](/api/v1/docs/nulls).

### What an absent unit says

| Field | Meaning |
|---|---|
| `name` | the unit, under the name every other section uses for it |
| `reason` | the sentence, written for a person |
| `reason_code` | the cause, for a program |
| `checks` | for a refusal, the stable ids of the checks that refused it |

`reason_code` shares its vocabulary with the per-unit error type on
[scenario details](/api/v1/docs/scenario-details): `ValidationError` for a refusal before the run
started, the exception's class name for a crash — a live session's crash included — and
`NoResults` where nothing failed and nothing was produced.

`checks` is what lets you ask which units a particular check cost you: ids such as
`warmup_quality` or `tick_stretch_gap`, stable enough to group on.

## Two timespans, and they answer different questions

| Field | Meaning |
|---|---|
| `tick_timespan_seconds` | the market time the run's units covered **together** — a stretch two scenarios share counts once |
| `tick_timespan_total_seconds` | their **sum** — the work, each scenario simulating its own stretch |

Both are null where no unit recorded one. They are far apart on a backtest whose scenarios overlap
in time, and reading the sum as calendar coverage claims the run touched a span it never did.

## Feed quality the figures were produced under

`signal_fresh_ratio` is the run's **weakest** signal channel — the lowest fresh ratio over all
usages, not an average. It is **null when no signal worker was involved**, deliberately rather
than `1.0`, which would claim a perfect feed where there was no feed. Per-source detail is on
[signal](/api/v1/docs/signal).

The disturbance totals stand beside it, so a headline can read them without scanning the episodes:
`disturbance_episode_count`, `disturbance_stale_seconds`, `disturbance_source_count` and
`disturbance_stress_injected`. **Zero in every one of them means the run saw no outage in either
staleness domain.** The episodes themselves are on
[feed stability](/api/v1/docs/feed-stability).

A ranking that carries `signal_fresh_ratio` alongside its objective carries the data quality its
rows were produced under; one that drops it ranks a clean run against a starved one as though they
were comparable.
