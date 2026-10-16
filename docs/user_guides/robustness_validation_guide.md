# Robustness Validation — In-Sample / Out-of-Sample & Multi-Window

A parameter-centric testing IDE needs a built-in guard against **overfitting** — the most common
way a great-looking backtest fails with real money. Robustness mode runs one constant strategy across many
time windows and reports whether the performance generalizes, instead of trusting a single number.

This is a **simulation** feature (the backtest pipeline). It adds no simulation cost — it is a
post-run aggregation over the per-window results the batch already produces.

## The concepts (established theory)

- **In-Sample (IS):** the data you tune on. **Out-of-Sample (OOS):** data the strategy never saw
  during tuning — the honest judge. Good IS *and* OOS → real structure; good IS, poor OOS →
  overfitting (the parameters memorized the noise of the IS window).
- **Degradation:** the drop from IS to OOS. Measured here as **Walk-Forward Efficiency (WFE)** =
  `OOS metric / IS metric`. Near 1.0 → robust; far below → overfit.
- **Holdout / lockbox:** every time you look at an OOS window and re-tune, its information leaks
  into the strategy. Keep a final window looked at **once**, at the very end. The tool cannot
  enforce this — it is your discipline.
- **Distribution over a peak:** the goal is a *region* of windows that all perform decently, not
  one tall window that collapses on the smallest market shift.

## Two ways to build a robustness set

### 1. Generator (recommended)

The generator produces the windows and assigns IS/OOS roles **time-ordered** (the first windows
are In-Sample, the trailing `--oos-split` fraction is Out-of-Sample — never train on the future):

```bash
python python/cli/generator_cli.py generate-blocks kraken_spot ETHUSD \
  --block-size 6 --count 10 --oos-split 0.3
```

The emitted scenario set carries a top-level `robustness` block and a `role` on each window.
The strategy is defined **once** in `global` — the generator never writes per-scenario strategy
overrides, so the parameters are constant by construction (the fair-test prerequisite).

A **Profile Run** (volatility-split) produces the same roles plus the per-window volatility
**regime** and **session**, which add a regime breakdown to the report (see below).

### 2. Manual scenario set

Define the strategy once in `global`, then one window per scenario with a `role`:

```json
{
  "scenario_set_name": "eurusd_robustness",
  "robustness": { "enabled": true, "metric": "expectancy", "oos_split": 0.3 },
  "global": {
    "strategy_config": { "decision_logic_type": "user_algos/my_algo/my_strategy.py", "...": "the ONE strategy" }
  },
  "scenarios": [
    { "scenario_name": "w_jan", "symbol": "EURUSD", "data_broker_type": "mt5",
      "start_date": "2024-01-01T00:00:00+00:00", "end_date": "2024-02-01T00:00:00+00:00",
      "role": "in_sample" },
    { "scenario_name": "w_feb", "symbol": "EURUSD", "data_broker_type": "mt5",
      "start_date": "2024-02-01T00:00:00+00:00", "end_date": "2024-03-01T00:00:00+00:00",
      "role": "out_of_sample" }
  ]
}
```

Cascade-capable keys (`strategy_config`, `execution_config`, `trade_simulator_config`) must stay
in `global` for a robustness set — a per-scenario strategy override breaks the fair comparison and
is flagged (see the constancy guard).

## The `robustness` block

The block is set-wide (a sibling of `scenario_set_name`, never inside `global`):

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Turn robustness mode on |
| `metric` | `expectancy` | The per-window metric: `expectancy` (mean R, currency-neutral), `net_pnl`, or `equity_return_pct` (final equity against the start, open positions marked) |
| `oos_split` | `0.3` | Trailing fraction assigned to Out-of-Sample by the generator |
| `min_windows` | `3` | Below this the distribution is statistically weak (advisory) |
| `overfit_wfe_threshold` | `0.5` | WFE below → OVERFIT verdict |
| `robust_wfe_threshold` | `0.8` | WFE at/above → ROBUST |
| `disposition_trust_pct` | `25.0` | Block-splitting distortion above which the verdict is suppressed |

**`expectancy` vs `net_pnl`:** expectancy (mean R-multiple) is comparable across instruments and
currencies and is the recommended default. It requires trades with a stop loss (no stop → no
R-multiple → expectancy 0). `net_pnl` is intuitive but not comparable across currencies.

**`equity_return_pct` — for a strategy that is invested all the time.** Both metrics above are
read from the trades a window CLOSED. A strategy that holds an allocation for weeks closes little
inside a window, so its result sits in what it still holds at the end, and they never see it. A
window that bought 0.35 ETH at 2,000 USD and ends with ETH at 2,300 closed nothing: `net_pnl`
reads 0, while the account went from 1,000 to 1,105 USD. `equity_return_pct` reads +10.5: the
final equity against the window's starting value, in percent, with everything still open marked
at the last price — comparable across currencies like `expectancy`, and needing no stop loss.

## Reading the report

The run summary shows a **ROBUSTNESS VALIDATION** section:

```
🎯 ROBUSTNESS VALIDATION
  Distribution (10 windows · metric: expectancy)
    profitable: 70%  |  mean +0.18  median +0.21  std 0.34
    best +0.72 (w04)  |  worst −0.41 (w08)  |  CoV 1.89
  By regime:
    high     (3 windows): mean +0.05  profitable 33%
    low      (2 windows): mean +0.31  profitable 100%
  In-Sample → Out-of-Sample:
    IS  (7): mean +0.24  profitable 71%
    OOS (3): mean +0.06  profitable 33%
    WFE 0.25 (OOS/IS) → ⚠ OVERFIT
```

- **Distribution** — the spread across all windows (`CoV` = std/|mean|, lower is more consistent).
- **By regime** — Profile Runs only: where the strategy works (which volatility regime).
- **IS → OOS + WFE** — the degradation verdict display class.

The ROBUST/OVERFIT **verdict** also fires as an advisory in the **Warnings & Errors** section
(machine-visible), plus the persisted `robustness.json`.

## The trust gate — block-splitting distortion

When a window is cut from continuous history, positions still open at the cut stay open: the
window ends holding them, and a closed-trade metric never sees what they are worth. If a large
share of each window's P&L rides on them (high **block-splitting disposition**), the per-window
numbers are artifacts and the robustness verdict would be built on sand. When the disposition
exceeds `disposition_trust_pct`, the verdict is **suppressed** with a caveat — fix the distortion
first (continuous mode / larger blocks; see the
[Generator & Block Splitting Architecture](../generator/generator_block_splitting_architecture.md)).

The gate applies to the closed-trade metrics only. `equity_return_pct` values what a window still
holds, so nothing open at the edge is missing from its number; the disposition is still shown,
marked as no trust input.

## What a cut costs — measured

Splitting history into blocks raises a fair question: does a strategy that starts flat in every
block earn what the same strategy earns running through? Each block pays an entry the continuous
account does not, and starts at its exact target where the continuous account carries a holding
from before. Measured 2026-10-15 for a slow strategy — a daily trend-following allocator on
ETHUSD, one decision a day, its holding between none and the whole account — over two stretches of
Kraken history. Every 30-day block's `equity_return_pct` was set against the continuous account's
own return over the same dates:

| Stretch | Blocks | Mean holding | Cut cost, mean | Median | Mean absolute | Of it, the entry fee |
|---|---|---|---|---|---|---|
| bear, 2022-01 → 2024-01 | 24 | 16 % | −0.06 pts | 0.00 pts | 0.47 pts | 0.12 pts |
| bull, 2020-01 → 2021-07 | 18 | 43 % | −0.43 pts | +0.20 pts | 2.10 pts | 0.35 pts |

The cut biases nothing in either regime: mean and median sit at zero, and a block reads as an
unbiased sample of the strategy. What grows with the holding is the noise of ONE block — two
points either way when the strategy holds a lot — so a judgement rests on the distribution of
many blocks, never on one.

The measurement holds for what it covered: fixed 30-day blocks, a strategy deciding once a day,
and a metric that values the holding. A fast strategy that carries positions through the cut and
is judged on closed trades is the case the trust gate above exists for.

## Per-bucket sufficiency

The Walk-Forward Efficiency rests on BOTH the in-sample and the out-of-sample mean. The overall
`min_windows` check can pass while a single bucket — usually OOS — is decimated (excluded or
crashed scenarios). When the IS *or* the OOS bucket holds fewer than `min_windows`, the verdict is
**suppressed** ("inconclusive — IS=n/OOS=m") rather than declaring OVERFIT/ROBUST on a handful of
windows. Recover the missing windows (fix excluded scenarios, lower parallelism if they were
killed) or widen the set, then re-run.

## The constancy guard

A fair comparison requires identical parameters across the windows. The guard compares the
cascade-resolved `strategy_config` of every window; a drift flags the comparison as "not fair"
(the generator path avoids drift by construction). Note the existing discovery flags do not catch
this — they compare only the logic-type string / worker count, not the parameters.

## What this is not

Walk-Forward *optimization* (rolling re-tuned IS/OOS windows, the #390 sweep × robustness) is a
later evolution. This issue ships the honest judge — the distribution + IS/OOS view — that keeps a
parameter sweep from being self-deception.
