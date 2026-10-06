# Profiling

A slow run tells you only that it was slow. Without this section you know how long a backtest took
and not which step of the tick loop spent it, nor whether the market left enough time between
ticks for the work to fit — so "make it faster" starts with a guess. This section is the tick loop
measured: where each unit's time went, how much time the market gave between ticks, and what a
tick-processing budget dropped.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/profiling
```

**What is not here:** per-worker timings and the decision counters. Those are
[workers and decisions](/api/v1/docs/worker-decision) — this section measures the tick loop's
operations, one of which runs the workers and the logic together. The complete list of a run's
scenarios is [scenario details](/api/v1/docs/scenario-details).

Only a backtest writes this section; the live tick loop has no operation profiling, so the route
answers `artifact_not_produced` for a session. Read the run's `artifacts` on
[runs](/api/v1/docs/runs) first, and see [run kinds](/api/v1/docs/run-kinds) and
[errors](/api/v1/docs/errors).

## `units` is not the scenario roster

A scenario whose tick-loop profiling was switched off has **no row here at all** — it is left out
rather than reported empty. So `units` can be shorter than the run's scenario grid, and
[scenario details](/api/v1/docs/scenario-details) is the list that is complete. A run where every
scenario had it off answers an empty `units` and an `aggregate` of zeros.

No list in this section declares a row key — see [row keys](/api/v1/docs/row-keys) for what that
states. Within the run, a unit is its `name`, the same identity the other per-unit sections carry.

## What one unit row says

| Field | Meaning |
|---|---|
| `total_ticks` | the ticks the loop processed |
| `total_ms` | every operation's total time, added up |
| `avg_per_tick_ms` | `total_ms` divided by `total_ticks` |
| `bottleneck_operation` · `bottleneck_pct` | the operation with the largest share, and that share |

`operations` carries one row per measured operation — `total_time_ms`, `avg_time_ms`, `call_count`
and `pct`, the operation's share of the unit's total. **The list is sorted by share, largest
first**, so the first entry is the bottleneck named above.

## Two operations are the hot path, the rest is infrastructure

`worker_decision` — the workers and the decision logic together — and `order_execution` are the
intended hot path. **A large share there is the strategy doing its work, not something to remove.**
Everything else measured around them — `bar_rendering`, for instance — is infrastructure.

The run-level `bottlenecks` list states that classification per operation as `status`: `expected`
for one of the two above, `infra` for anything else that was some unit's bottleneck, and `none`
for an operation that was never any unit's bottleneck.

It is a classification and not a judgement. Whether an infrastructure bottleneck is a *problem* is
a verdict, and verdicts on a run arrive as warnings — see
[warnings and errors](/api/v1/docs/warnings-errors).

## How much time the market left between ticks

`inter_tick` is the distribution of **market-side** time between consecutive ticks. It is not
processing time; it is the budget the market handed the loop.

- `p5_ms` — the fastest 5 % of tick arrivals: 95 % of intervals are longer than this. It is the
  tightest budget the unit faced, and the figure worth comparing `avg_per_tick_ms` against.
- `p95_ms` — only 5 % of intervals are longer: the quiet phases, with long gaps between ticks.
- `min_ms`, `median_ms`, `mean_ms`, `max_ms` — the rest of the distribution.

**Session and weekend gaps are removed before any of it is computed.** Any interval longer than
`threshold_s` is excluded, `gaps_removed` counts how many were, and `interval_count` is how many
remain. `max_ms` is therefore the longest interval *after* that filtering, not the longest gap in
the data.

`inter_tick` is null where the unit has no distribution — nothing was recorded, or nothing
survived the filtering. See [nulls](/api/v1/docs/nulls).

## Clipping, and when it exists

`clipping` is null unless a tick-processing budget was configured for the run. Where one was, the
budget filter ran over the unit's ticks and the row says what it did:

| Field | Meaning |
|---|---|
| `ticks_total` | ticks before filtering |
| `ticks_kept` | ticks that passed the budget filter |
| `ticks_clipped` | ticks the filter removed |
| `clipping_rate_pct` | the share that was clipped |
| `budget_ms` | the budget value used for filtering |

## The run-level roll-up

`aggregate` folds the unit rows into one picture. Ratios are recomputed from summed components —
`avg_per_tick_ms` is the summed time over the summed ticks, never an average of the units'
averages.

| Field | Meaning |
|---|---|
| `scenarios` · `total_ticks` · `total_time_s` | how many units are folded here, and their totals |
| `most_common_bottleneck` · `most_common_bottleneck_pct` | the operation that was the bottleneck in the most units, and in what share of them |
| `p5_min_ms` · `p5_max_ms` | the range of the units' own P5 intervals — how uneven the tightest budget was across the run |
| `p95_processing_ms` | the 95th percentile of the units' `avg_per_tick_ms` |
| `suggested_budget_ms` | that figure plus a 10 % margin |
| `budget_active` | whether a tick-processing budget was configured |

Two lists sit beside them, and each leaves fields unset on purpose:

- `avg_operation_times` — one row per operation, carrying the cross-unit mean of the per-unit
  `avg_time_ms`, slowest first. **Only `avg_time_ms` is a figure on these rows**; the other
  operation fields are not summed into them.
- `bottlenecks` — one row per operation seen anywhere, with `scenario_count` (in how many units it
  was *the* bottleneck), `total_scenarios`, the `pct` between them, and the `status` above. Most
  frequent first.

The clipping roll-up — `clipping_total_ticks`, `clipping_total_kept`, `clipping_total_clipped` and
the distinct `clipping_budgets` across the units — is only meaningful when `budget_active` is
true.

## `scenarios_over_p5` is a count, not a verdict

It is how many units processed a tick slower, on average, than their own fastest 5 % of tick
intervals — the units whose work did not fit the tightest budget the market gave them.

It counts, and stops there. Whether that warrants a warning is decided elsewhere on the run and
surfaces as one; this number is the input, not the conclusion.

## `warmup_phases`

One entry per warmup phase, with its `name` and its measured `duration_s`. These belong to the
run, not to any unit: they sit outside the tick-loop figures above and are not part of any unit's
`total_ms`.
