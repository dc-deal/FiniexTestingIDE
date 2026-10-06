# Signal sources

Two runs can read the same external signal source over the same window and decide on completely
different data. A hole in the series that one run never notices costs the other minutes of stale
decisions, because the two ran with different staleness thresholds — and from the result alone that
is invisible. This section says, per source, what the source *was* and what the strategy *actually
decided on*.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/signal
```

**What is not here:** when a source went quiet and how often. The outage spans are
[feed stability](/api/v1/docs/feed-stability). What the gap categories mean is
[gaps](/api/v1/docs/gaps).

## Two planes, and they are allowed to disagree

A row carries both, and conflating them is the mistake this section exists to prevent:

- the **source** plane — provenance, cadence, extent, continuity. What the series *could* offer.
- the **decision basis** — the per-tick counters captured while the run read it. What the strategy
  *did* read.

A gap in the series shorter than the run's staleness threshold produces **zero** stale ticks: the
next snapshot arrives before the old one ages out, and the strategy never notices. Measured on a
real signal archive, a 22-minute hole on 2026-07-23 cost no stale ticks at a 30-minute threshold
and seven minutes of them at a 15-minute one. Same data, same coverage figure, different decision
basis. That product — data times parameter — is the thing a parameter sweep has to be able to see.

## `series_kind`: whether there is a source plane at all

`archive` means the run read a finished series, which can be analysed for continuity. `feed` means
the envelopes arrived while the session ran, and there is no archive to analyse. It is not the same
question as where the *ticks* came from, which is [run kinds](/api/v1/docs/run-kinds).

A feed row therefore carries no continuity analysis, and says so by absence rather than by zeros:

- `gap_counts` is `{}` — **not measured**, which for a feed means not measurable. Rendering it as
  "no gaps" asserts continuity for a series nobody examined.
- `coverage_ratio` on every usage is null, not `1.0`. See [nulls](/api/v1/docs/nulls).
- `cadence_seconds` is the interval the producer reports for itself, not a measured median — a
  session that received a handful of envelopes has no sample to take a median from.

`units` is empty when the run bound no signal source at all, by either route.

## One row is one source

`source` names it. Inside the row, `usages` is one entry per scenario's use of that source for one
symbol — so a scenario reading two symbols from the same source has two entries.

`scenario` on a usage carries the unit's name — the scenario in a backtest, the session's unit in a
live session — which is the join into every other section of the run. This response declares no row
key; see [row keys](/api/v1/docs/row-keys) for what that means and for the join.

## Provenance: empty means unknown, never "unchanged"

These fields describe what produced the series. Each is a plain string, and an **empty** one means
the producer did not stamp it — never that the value was constant.

| Field | What it says |
|---|---|
| `data_origin` | `live` or `synthetic`; `mixed` where the series holds both |
| `config_fingerprint` | the producer's input-configuration hash |
| `prompt_version` | the producer's prompt generation |

`mixed` is the value to design against. On `prompt_version` it means the run spans a prompt change,
so it spans **two series** — different prompts yield different scores for the same news, and scores
are not comparable across that boundary. On `config_fingerprint` it means the same for the
producer's configuration as a whole: a drop in performance between two windows whose fingerprint
moved may be the source changing rather than the strategy.

`prompt_version` is empty on an archive row. The archive's runtime projection does not carry the
prompt provenance, so the field is unanswerable there rather than unchanged.

## Extent and cadence

`snapshot_count` is how many envelopes the series holds. `archive_start` and `archive_end` bound
it; on a `feed` row they bound what actually arrived during the session.

`cadence_seconds` is the median distance between snapshots on an archive row — measured, so it
reflects what the series does rather than what the producer intends.

## The gap map lists only the categories it found

`gap_counts` maps a category to a count, and a category with no gaps is **left out**. So
`{"short": 3}` means three short gaps and none of anything else, while `{}` means nothing was
measured at all.

A signal source never carries `weekend` or `holiday`. Those categories exist for markets that
close, and a signal producer runs without market hours — a quiet Sunday in a signal series is a
real gap, not a closure. What each category means is in [gaps](/api/v1/docs/gaps).

## `sequence` is a sentence, not a field to parse

Envelopes carry a stream position, and `sequence` is a one-line verdict on it: contiguous, how many
positions are missing, and the span it covers. It is written for a reader; its wording differs
between an archive and a feed, so do not branch on it.

What it will tell you that matters: **`not verifiable` is a third state.** A series from before the
producer stamped a stream position cannot be checked for holes, and that is not the same answer as
"checked and contiguous". `sequence` is empty where the verdict was not stated at all.

## `trigger_reasons`: why each producing pass ran

A map from reason to envelope count — `scheduled` for the regular grid, `boot` for the first pass
after a producer restart, `breaking` for an out-of-band wake, `manual` for an operator, `external`
for an API caller. The key set belongs to the producer, so treat it as open and tolerate a reason
you do not recognise.

`trigger_unknown` counts the envelopes carrying no reason at all. It is kept apart deliberately: a
series that was only partly stamped would otherwise read as though the composition covered all of
it. Do not fold an unknown into `scheduled` — a restart or an out-of-band wake folded into the grid
is exactly what the field exists to separate.

## What a usage row says the run decided on

| Field | Meaning |
|---|---|
| `scenario` / `symbol` | the unit that read the source, and the symbol it read |
| `window_start` / `window_end` | the scenario's window, or the session's span |
| `coverage_ratio` | the share of that window not swallowed by a gap in the series |
| `fresh_ticks` · `stale_ticks` · `blind_ticks` | the decision basis |
| `fresh_ratio` | fresh over the three counters together |

`window_end` is empty where the scenario ended on a tick cap rather than on a date; the coverage
ratio is then measured to the end of the series.

`coverage_ratio` is null where there was nothing to measure against — a feed, or a window with no
end on either side. It is deliberately not defaulted to `1.0`, which would claim full coverage of a
window nobody could examine.

## fresh, stale and blind

Three mutually exclusive classes that add up to the ticks the unit processed:

- **fresh** — a snapshot resolved, and it is younger than the run's staleness threshold
- **stale** — a snapshot resolved, but it has aged out
- **blind** — nothing resolved at all

**`blind` is not a gap in the series.** A hole *inside* the series resolves to the last snapshot
before it, which makes those ticks `stale`. `blind` means there was nothing there to resolve, which
in practice happens at the head of a run — before the first snapshot the window reaches.

`fresh_ratio` is `0.0` when the unit processed no ticks at all, and the counters are all zero for a
scenario that never produced a run unit. Read the counters together with the run's unit roster
rather than treating a zero as "it decided on nothing".

## The clamps, and why they are counted here

`availability_clamps` and `max_clamp_correction_ms` are the one pair here that describes how the
series was *read*, rather than what the producer sent.

A producer-side clock correction can move a snapshot's availability stamp **backwards**. Letting
that through would make a snapshot visible earlier than the one before it, which is the one
direction that is look-ahead — a backtest seeing information before it existed. The resolution gate
refuses, and `availability_clamps` is how often that refusal was needed.

`max_clamp_correction_ms` is the largest backwards step absorbed, and the count cannot be read
without it: one clamp of two milliseconds is jitter, one of four hours is a story.

Both are counted by that gate rather than read from a figure the producer reports, so a backtest
and a live session answer the same numbers over the same series. They sit on the **usage** rather
than on the source because each unit orders its own window-trimmed series, and a source-level
number could not say which window met the correction.
