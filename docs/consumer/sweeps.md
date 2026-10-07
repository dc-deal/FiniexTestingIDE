# Parameter sweeps

A sweep runs one strategy over many parameter combinations, and every combination is its own run.
In the run index they are a crowd of sibling rows with nothing to say which search they belong to
or which one won. These two routes answer exactly that: every sweep on record, and one sweep's
combinations ranked by the objective the sweep itself declared.

**Routes**

```
GET /api/v1/sweeps
GET /api/v1/sweeps/{sweep_id}
```

**What is not here:** the reports of a single combination. Every ranked row carries its `run_id`,
and that is the hinge into the report routes — see [runs](/api/v1/docs/runs).

## A sweep is not a run

It has no run id of its own, and therefore none of the report sections a run has. It is a family
of runs, defined by the runs that name it: in the run index each combination is a `simulation`
whose `parent_id` is the sweep's id and whose `parent_kind` is `sweep`. Both routes here are
served from the run-results ledger, which is where a sweep's identity and its KPIs live.

## The list

```json
{ "key": ["sweep_id"], "sweeps": [ ... ], "count": 4 }
```

Newest first, one row per sweep:

| Field | Meaning |
|---|---|
| `sweep_id` | its identity, and what `{sweep_id}` takes |
| `started` | the earliest run start in the sweep |
| `duration_s` | the last run's start minus the first run's start |
| `run_count` | how many distinct combinations it holds |
| `ok_count` · `error_count` | how many produced usable data, and how many did not |
| `decision_logic_type` · `decision_version` | the strategy that was swept |
| `base_config` | the scenario set that was swept, with the sweep's own tag stripped off |
| `symbols` | the symbols its runs read |
| `objective` · `maximize` | the KPI it ranks by, and the direction |

`duration_s` is the spread of the **starts**, not how long the sweep took: it is computed from
the run starts alone, so the last run's own duration falls outside it. It is `0.0` where there is
nothing to span — a sweep of one run.

`objective` is empty where the sweep recorded none.

## One sweep's combinations

```json
{ "key": ["run_id", "currency"], "sweep_id": "...",
  "objective": "expectancy", "maximize": true, "combinations": [ ... ], "count": 12 }
```

Ranked, not alphabetical: the question a sweep asks is which combination won. The ranking uses the
objective and the direction **the sweep declared**, because ranking by anything else would answer
a question the sweep never asked. Where a sweep recorded neither, the response says what was used
instead — `expectancy`, maximized — in its own `objective` and `maximize` fields, so you are
never left to assume.

**Combinations are ranked within each account currency, currencies in order.** A money objective
in two currencies is not one scale: one list ranking EUR rows against USD rows by `net_pnl`
compares nothing.

### Why a row is keyed by run *and* currency

A run books one ledger row per booking period, so a sweep's rows far outnumber its combinations.
Sorted as they lie, one combination would appear several times over and the top ten would be the
ten best **days** rather than the ten best parameter sets. The ranking therefore folds each run's
periods back into one row per run and account currency before it sorts, and that is what `key`
declares. See [row keys](/api/v1/docs/row-keys) and
[booking periods](/api/v1/docs/booking-periods).

### What the fold cannot answer

Three groups of fields are null on a folded row, each for its own reason:

- **A reading of one account** — `final_equity`, `unrealized_pnl`, `open_position_count`. A
  combination of several scenarios is several accounts, and the latest reading of one account is
  not the run's. Null since contract 17.
- **The order counts** — `orders_submitted`, `orders_executed`, `orders_rejected`,
  `sl_tp_triggered`. A booking period carries none, so a row folded from periods has nothing to add
  up. They read `0` before contract 18, which turned an absence into a measured zero.
- **The streaks** — `max_consecutive_wins`, `max_consecutive_losses`. A run of winners can cross a
  period boundary, so 2 and 3 in adjacent periods may be a run of 5, and no arithmetic over two
  summaries recovers it.

[Nulls](/api/v1/docs/nulls) has the general rule: render absence as absence.

### The error combinations are not in the ranking

A row's `status` is `ok` or `error`, and `error` means the run produced no usable data, with
`error` carrying the reason. Those rows are recorded in the ledger but never rank — so the detail
list leaves them out while the sweep's own `error_count` still counts them.

The two counts are also counted over different things: `run_count` in the list counts
**combinations**, while `count` in the detail counts **rows** — one per combination and account
currency.

### What a row says about the code and the data behind it

A ranking is comparable only where the things ranked are. Several fields exist so you can check
that before trusting the order:

- `logic_version` — the version of the logic that produced the row. Null means it was written
  before row versioning existed, so the version is *unknown* rather than old. A ranking that mixes
  versions compares measures that changed underneath.
- `git_commit` · `git_branch` · `git_dirty` — `git_dirty` covers **every** repository a
  component of the run came from, not only the main one, and it reads `true` where the code
  state could not be determined. It never says the code was clean; it has meant this since
  contract 4.
- `trial_count` — how many candidates this run was selected **from**. Null is unknown; `1` is a
  statement, that this run was the only candidate. A result picked as the best of 500 and a result
  nobody compared cannot be discounted alike.
- `app_version` · `decision_logic_type` · `decision_version` · `worker_versions` ·
  `strategy_config_json` — the last is the run's full resolved strategy configuration, carried as
  a JSON **string** rather than as an object.
- `sweep_params` — this combination's concrete grid point: the parameters that make it the row it
  is.

`run_kind` is derived from the row rather than stored; inside a sweep it reads `sweep` on every
row.

### `records_pruned_at`

When this row's run records were removed by a prune, and empty while they were not. The row
deliberately outlives its evidence, and this is what stops it from quietly claiming its figures
can still be checked against the records they came from. A row carrying a stamp here is a row
whose underlying records are gone.

### The KPIs

`net_pnl`, `expectancy`, `win_rate`, `profit_factor`, `account_max_drawdown`, `total_fees` and
the trade counts are what a combination is judged on. Several of them are null rather than zero,
and the distinction is the point: `profit_factor` is null where the combination had no losing
trade and the ratio is therefore undefined, and `gross_profit`, `gross_loss`, `max_equity` and
`account_max_drawdown_pct` are null on rows written before those columns existed. A fabricated
zero would assert a measurement nobody took.

`signal_fresh_ratio` is the weakest signal channel of the run, and null where no signal worker was
involved at all — deliberately not `1.0`, which would claim a perfect feed. See
[signal](/api/v1/docs/signal).

## Errors

An unknown sweep id is `404 sweep_not_found`. The whole refusal vocabulary is in
[errors](/api/v1/docs/errors).
