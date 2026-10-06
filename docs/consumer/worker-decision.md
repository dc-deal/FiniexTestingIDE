# Workers and decisions

A run's results say what the strategy achieved and nothing about what it cost to get there: how
many ticks reached the algorithm, how many decisions it made of them, and which of its workers
spent the time. This section answers that, one row per unit, plus each worker's timings summed
across the whole run.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/worker-decision
```

**What is not here:** how a single tick splits into worker, decision and coordination shares. That
breakdown is derived from the tick loop's own operation timings and stays with
[profiling](/api/v1/docs/profiling). The list of the run's units is
[scenario details](/api/v1/docs/scenario-details); what became of the orders a decision asked for
is [execution stats](/api/v1/docs/execution-stats).

Every run writes this section, whichever kind it is: a backtest answers one row per scenario, a
live session one row for the session. See [run kinds](/api/v1/docs/run-kinds).

## Two lists

`units` is one row per unit. `worker_totals` is one row per distinct worker name, its timings
summed across every unit, highest total time first.

Neither list declares a row key — see [row keys](/api/v1/docs/row-keys) for what that absence
states. The unit's `name` is the same identity the other per-unit sections carry, so a row here
joins onto them; within a unit, `worker_name` tells the worker rows apart.

## `worker_decision_tracked` is the field to branch on

It says whether this unit counted its decisions and timed its workers. **A backtest leaves it off
by default**, because the tracker sits on the hot path and counting costs time there.

Untracked, the unit answers:

- `decision_count`, `buy_signals`, `sell_signals`, `flat_signals`, `trades_requested` — **null**
- the four `decision_*_time_ms` — **null**
- `workers` — an **empty list**, and that empty list is true: nothing was timed

and it still answers `decision_logic_type`, `decision_logic_name` and `ticks_processed`. The logic
is named either way, and the ticks are counted either way.

Null here means *not measured* — a setting on the run, so a later run can carry the figure. See
[nulls](/api/v1/docs/nulls). These fields used to read `0`, so a logic that had decided on
thousands of ticks reported that it decided nothing.

`decision_logic_type` and `decision_logic_name` are null only where the unit produced no decision
statistics at all — a session that ended at startup, for instance.

**Runs stored before contract 21 were carried over, and one field could not be.** A unit with no
logic type was not tracked, so its counters are null now; a unit with one is marked tracked and
keeps its figures. The *name* of an old untracked unit's logic stays null, because only a new run
stamps it. See [the contract log](/api/v1/docs/contract-log).

## What a tracked unit counted

`decision_count` is how many decisions the logic made. `buy_signals`, `sell_signals` and
`flat_signals` are what it decided; `trades_requested` is how many trades it asked for — a
request, not an execution.

`decision_total_time_ms` with `decision_avg_time_ms`, `decision_min_time_ms` and
`decision_max_time_ms` are the decision logic's own time, separately from the workers below.

## Coordination is counted in both pipelines

| Field | Meaning |
|---|---|
| `ticks_processed` | the ticks that reached the algorithm — counted whether or not tracking was on |
| `parallel_workers` | whether this unit's workers ran in parallel |
| `parallel_time_saved_ms` | the time that parallelism saved over the unit |
| `parallel_avg_saved_per_tick_ms` | that saving divided by `ticks_processed` |

**A live session recorded before 2026-09-24 reports `0` ticks**, beside its real decision count:
the orchestrator counted them, nothing collected the result. The two per-worker figures derived
from that count — the compute ratio and the idle distance — then read `0.0 %` and `0` rather
than reporting nothing.

## What one worker row says

`worker_type` and `worker_name` name it. `call_count` is the number of **actual computes**, not
the number of ticks it was offered, and `total_time_ms`, `avg_time_ms`, `min_time_ms` and
`max_time_ms` are its timings.

`compute_basis` is the cadence the worker ran on — `live` or `bar_close`. A worker on a bar-close
cadence computes far less often than ticks arrive, so a `call_count` well below the unit's
`ticks_processed` is its cadence rather than a fault.

| Field | Meaning | Null when |
|---|---|---|
| `compute_ratio_pct` | `call_count` as a percentage of the unit's `ticks_processed` | no tick was processed |
| `ticks_idle` | ticks since the worker's last real compute | the worker never computed |

`last_compute_tick` is the tick index of that last real compute, and reads `-1` where the worker
never computed — which is the case `ticks_idle` answers with null.

`ticks_idle` previously read `0` in that case, which says the opposite of what happened: zero
ticks idle reads as "it computed on this very tick".

## The totals row, and what is not rolled up

On a `worker_totals` row, `avg_time_ms` is recomputed from the summed total over the summed call
count — never an average of averages — and `min_time_ms` and `max_time_ms` are the extremes
across the units, not of the sums.

`compute_ratio_pct` and `ticks_idle` are **null** on every totals row, because the row spans
several units' tick counts and there is no single denominator to divide by. `compute_basis` and
`last_compute_tick` describe one worker inside one unit and are not rolled up either — read those
from the unit rows.
