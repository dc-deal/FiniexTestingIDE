# Warmup History — the Bars a Run Starts From

A worker computes from bars, and before its first tick a run hands it bars it did not see form:
the warmup history. Which bars belong in it decides whether a backtest and a live session start
from the same state — and for a year the three paths that build one answered that question three
different ways, one of which read the future.

This document covers the rule, the paths that apply it, the seam at the first tick, the guard,
the outcome when a history falls short, what a session records, and thinly traded symbols. It does not cover how bars are rendered from ticks
([Batch Preparation System](../data_pipeline/batch_preparation_system.md)), how a worker states
how many bars it needs ([Worker Naming](../user_guides/worker_naming_doc.md)), or the signal
history a SIGNAL worker starts from.

## The rule

A bar is stamped with the instant its period **opens** — the archive (a resampled period is
labelled by its left edge), the renderer and the venues alike — but it is **known** only once its
period has **ended**: its high, low and close come from the whole period.

> The warmup history at the first tick T holds, per timeframe, the last N bars **complete at T**:
> bars whose period ended at or before T (`open + duration ≤ T`). N is what the most demanding
> worker reading that timeframe requires.

[`bar_period.py`](../../python/framework/utils/bar_period.py) is the one place that answers it
(`is_complete_at`, `complete_at_mask`, `period_start`, `period_end`), and every path asks it.
Every registry timeframe divides a UTC day, so the archive, the renderer and the venues share one
grid anchored at midnight UTC: the same opening instant names the same period everywhere.

What went wrong before: the backtest kept every bar whose opening stamp lay before the start. A
scenario starting at 13:00 warmed up on the day's D1 bar — opened at 00:00, closed at 24:00 — and
read its close eleven hours early; at the first tick the renderer then opened a bar with the same
opening instant a second time. Start 2026-08-05 13:02:30, three D1 bars:

| | Before | Now |
|---|---|---|
| warmup history | 08-03, 08-04, **08-05** (closes 08-06 00:00) | 08-02, 08-03, 08-04 |
| first bar the run renders | 08-05 — the same period again | 08-05, from 13:02:30 on |

## Who selects, and for which instant

| Path | Source | Selected for |
|---|---|---|
| backtest | the archive, by the batch (`SharedDataPreparator.prepare_bars`) | the scenario's first tick |
| mock session | the same package a backtest of its window gets | the first tick it replays |
| live-adapter session | the venue (`KrakenOhlcBarFetcher`), read at boot | the instant of the read — then the first tick, at the seam |

The archive paths select for the **first tick**, not the configured start. Nothing reads the
history before that tick, and no tick lies between the start and it, so every bar complete then
was built from ticks before the start. A scenario starting at 12:59:50 whose first trade arrives
at 13:00:22 therefore warms up on the 12:00 bar — exactly what a live session starting then sees.

[`WarmupHistory`](../../python/framework/bars/warmup_history.py) owns the history in both
pipelines, from its source to the first tick: built from the package or from the venue, judged,
injected, and closed at the seam.

## The seam at the first tick

A live session reads the venue during boot, seconds before its first trade arrives. Where a period
ends in between, the boot read could not take it: it was still forming. So when the first tick
arrives, before it is rendered, every timeframe whose newest bar ends before that tick's period
opens is **read again for the first tick** and replaced whole. After the seam, every path holds the
last N bars complete at the first tick.

```
boot read      23:59:59   D1 history ends 08-04 — 08-05 is still forming
first trade    00:00:02   08-05 ended in between → D1 read again → history ends 08-05
first bar rendered        08-06, from 00:00:02 on
```

A venue row is taken only when **both** sides call it finished: its period has ended by the
instant of the read, and the venue has committed it. Kraken names the newest committed period in
its answer's `last` field; the row after it is the period still forming. Measured on ETHUSD M1: a
period was committed about 0.8 s after it ended on 2026-10-12, between 1.5 and 2.2 s on 2026-10-13 —
within about two seconds, and the probe `probe_kraken_ohlc_commit.py` repeats the measurement. Two
of its answers are recorded verbatim as a test fixture, and the live adapter release gate checks
at every release that the venue still answers in their shape.
Either test alone fails near a
boundary — our clock may be past a period's end while the venue still adds trades to it.

The re-read is a single attempt inside the tick loop, never a retry with a wait. If it fails, or
the venue has not committed the period yet, the session **refuses** with
`WarmupHistoryRefusedError`: no decision has run yet, so stopping there is stopping before trading,
and a restart seconds later closes the seam.

The first bar a run renders holds only the ticks from its first tick on, in both pipelines — a live
trade feed delivers no earlier trades. For a strategy that reads closes this changes nothing: a
partial bar closes where the full one does.

## The guard

Warmup bars enter the renderer's history through one route,
`BarRenderingController.inject_warmup_bars`, in both pipelines. It refuses, as a defect
(`WarmupHistoryDefectError`), a bar not complete at the instant the history was selected for, an
opening instant that repeats or goes backwards, and a bar of another timeframe; a bar that passes is
stamped `is_complete` there and nowhere else. At the first bar the session renders itself, the
renderer checks that the history ends where that bar opens — a second, independent line that also
catches a history selected for the wrong instant.

## When a history falls short

One shortfall rule ([`warmup_history_validator.py`](../../python/framework/validators/warmup_history_validator.py)),
measured against the most demanding worker per timeframe; each pipeline decides what it means.

| Finding | Backtest | Mock or live session |
|---|---|---|
| a timeframe with no bar at all | scenario refused — `warmup_history_empty` | refused at startup |
| fewer bars, the source holds no more | warning — `warmup_quality` | warning |
| the venue could not be read | — | refused (live) |
| a requirement beyond `bar_max_history` | scenario refused — `warmup_capacity` | refused at startup |
| a bar not complete, repeated or out of order | defect, raised | defect, raised |
| the seam cannot be closed at the first tick | — | live: refused at the first tick, before any decision, and shown as a refusal |
| a worker whose two warmup answers differ | refused when the worker is created | refused when the worker is created |

The capacity check exists because the renderer keeps at most `bar_max_history` bars per timeframe
and would trim a larger history at injection, silently: a profile requiring 71 M5 bars with a
capacity of 30 ran its RSI on 30 bars for every session it made.

A worker states its warmup through two doors: the classmethod `calculate_requirements(config)`,
which a backtest asks before any worker exists, and the instance's `get_warmup_requirements()`,
which a session loads by. The worker factory asks both whenever it creates a worker — CORE or a
user's — and refuses one whose answers differ, because the two pipelines would then warm up on
different histories.

## What a session records

A session writes the history it started from into its run directory, `io/warmup_history.json` —
at injection, and again once the seam at the first tick has closed. A live session's warmup comes
from the venue and exists nowhere else afterwards, and the parity proof compares a backtest of the
session's window against exactly that history. One object per file:

```json
{
 "symbol": "ETHUSD", "source": "venue",
 "read_at": "2026-10-12T19:24:05.121+00:00",
 "selected_for": "2026-10-12T19:25:00.884+00:00",
 "first_tick": "2026-10-12T19:25:00.884+00:00",
 "reread_at_first_tick": ["M1"],
 "timeframes": {"M5": {"required": 71, "bars": [["2026-10-12T13:30:00+00:00", 2411.3, ...], ...]}}
}
```

`read_at` is the boot read, `selected_for` the instant the recorded bars are complete at — the
first tick once the seam has closed. Each bar is `[opening instant, open, high, low, close, volume,
tick count]`. A backtest does not write this file: its history is the archive's, and the same
selection over the same archive reproduces it. The archive does change — every import renders the
bar files again — so the parity report (#512) records which render of the bar files its backtest
read; the bar index's `rendered_at` shows a new render, and only a data hash per file (#583) will
show whether the values changed.

## Thinly traded symbols — said, not corrected

On a thinly traded (illiquid) symbol many intervals pass without a single trade. That is a matter of
trading activity, not of volatility: such a symbol may move little or in jumps, but what counts here
is how often anything trades at all. Kraken publishes a row for every interval regardless — a flat
bar, volume and trade count zero — while the archive skips a period in which no tick arrived. So a
live warmup of such a symbol holds flat bars a backtest of the same window never has, and its
indicators start from other values.

How often it happens, over each venue's last 721 rows:

| Symbol | M1 | M5 | M30 | D1 |
|---|---|---|---|---|
| ETHUSD | 0 | 0 | 0 | 0 |
| BTCUSD · SOLUSD · DOTUSD | — | 0 | 0 | 0 |
| DASHUSD | 454 | 187 | 0 | 0 |
| BCHGBP | 720 | — | — | — |

Measured 2026-10-12 (M1) and 2026-10-13 (M5, M30, D1); — not measured. DASHUSD is in the production
profiles on purpose, as a thinly traded case.

A live session says so when it happens, once per timeframe, in its session log:

```
⚠️  DASHUSD M5: 18 of 71 warmup bars hold no trade — the venue fills quiet periods with flat bars,
    the archive skips them; a backtest of this window warms up on different bars
```

The flat bars are deliberately kept rather than dropped: the seam at the first tick tells a missing
period by its absence, and could then no longer tell an empty period from one the venue has not
committed yet. The same symbols run into a second limit: Kraken reaches back 720 **intervals**, not
720 bars, so where many are empty few real bars remain, while the archive reaches further.

## Known limits

Recorded, not built — none of them touches a liquid symbol read by a daily strategy:

- A live warmup bar carries the venue's volume and trade count, an archive bar the sums of what
  the collector recorded, so a volume-reading worker starts from different values (#512 compares
  the two, from the session's record).
- The first bar a run renders could be pre-filled — live from the venue's forming row, a backtest
  from the archive's ticks before the start — so that it is whole rather than partial. It matters
  only to workers reading a bar's high, low or volume.

The tests that pin all of this, across the three paths: [Warmup History Tests](../tests/framework/warmup_history_tests.md).
