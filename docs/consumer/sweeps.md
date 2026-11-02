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
| `features_beyond_venue` | the order features its scenario set allowed beyond what the venue's API holds, comma-joined; empty for every other sweep |
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
  "objective": "net_pnl", "maximize": true, "combinations": [ ... ], "count": 12,
  "unranked": 0 }
```

Ranked, not alphabetical: the question a sweep asks is which combination won. The ranking uses the
objective and the direction **the sweep declared**, because ranking by anything else would answer
a question the sweep never asked. Where a sweep recorded neither, the response says what was used
instead — `net_pnl`, maximized — in its own `objective` and `maximize` fields, so you are
never left to assume.

**Combinations are ranked within each account currency, currencies in order.** A money objective
in two currencies is not one scale: one list ranking EUR rows against USD rows by `net_pnl`
compares nothing.

### A combination without a value is counted, not ranked

`unranked` counts the combinations the ranking left out because they carry no value for the
objective. That happens only with a figure some runs did not measure — `account_max_drawdown` of
a session whose days were all sealed before its first valuation, `expectancy` of a run none of whose
trades has an R, or a column a row was written before. So a sweep that declares `expectancy` over a
strategy keeping its own stop serves `combinations: []`, `count: 0` and every combination in
`unranked`: it ran, and the figure it asked to rank by was not measured. A figure that can be
undefined for a run — `profit_factor` without a losing trade, `avg_win_r` and `avg_loss_r`, the
excursion means, a reading of one account — is refused as an objective instead: leaving out
the runs without a profit factor would drop the runs that never lost. Without `unranked`, a
shorter list would read as the whole sweep.

### Why a row is keyed by run *and* currency

A run books one ledger row per booking period, so a sweep's rows far outnumber its combinations.
Sorted as they lie, one combination would appear several times over and the top ten would be the
ten best **days** rather than the ten best parameter sets. The ranking therefore folds each run's
periods back into one row per run and account currency before it sorts, and that is what `key`
declares. See [row keys](/api/v1/docs/row-keys) and
[booking periods](/api/v1/docs/booking-periods).

### What the fold cannot answer

Two groups of fields are null on a folded row, each for its own reason:

- **A reading of one account** — `final_equity`, `unrealized_pnl`, `open_position_count`. A
  combination of several scenarios is several accounts, and the latest reading of one account is
  not the run's. Null since contract 17.
- **The streaks** — `max_consecutive_wins`, `max_consecutive_losses`. A run of winners can cross a
  period boundary, so 2 and 3 in adjacent periods may be a run of 5, and no arithmetic over two
  summaries recovers it.

The order counts — every `orders_*` field and `sl_tp_triggered` — DO fold: since contract 42 each
booking period counts the orders it handled, and a combination is the sum of its periods. They are
null on a combination recorded before that, when a period carried none.

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
- `code_stand` — which code a session started on, per repository. Null on every combination, because
  only a session records one; [deployments](/api/v1/docs/deployments) is where it is read.
- `trial_count` — how many candidates this run was selected **from**. Null is unknown; `1` is a
  statement, that this run was the only candidate. A result picked as the best of 500 and a result
  nobody compared cannot be discounted alike.
- `app_version` · `decision_logic_type` · `decision_version` · `worker_versions` ·
  `strategy_config_json` — the last is the run's full resolved strategy configuration, carried as
  a JSON **string** rather than as an object.
- `sweep_params` — this combination's concrete grid point: the parameters that make it the row it
  is.
- `param_hash` — a fingerprint of the strategy parameters the run decided with, over all its
  scenarios. Equal hashes mean the same parameters; it does not say which ones differ.
- `profile_hash` — a fingerprint of the simulator settings the run executed with — the latency and
  slippage models, the random seeds and the tick budget, for example — after the application's own
  defaults were merged in under the scenario set. Equal hashes mean the same settings. No
  configuration id covers that layer, so this fingerprint is all a backtest keeps of it. Empty on
  a row written before it existed.
- `input_plane` · `data_format_versions` · `origin_classes` · `origin_evidence_grades` ·
  `price_bases` — what kind of data the run read, over all its scenarios, as
  [scenario details](/api/v1/docs/scenario-details) says it per scenario; `input_files` counts the
  files it read and `unstamped_input_files` those that were not production data stamped by their
  producer, which a measurement may not rest on. They say what kind of data and how much, never
  which files. `features_beyond_venue` names the order features the run was allowed beyond what its
  venue's API holds, empty for every other row. Empty, and the counts null, on a row written before they existed.
- `rendered_config_id` · `broker_config_id` — what the combination ran with: the run-config
  store's ids of its rendered scenario set and of the broker configuration it traded under,
  comma-joined where it traded under several. `config_hash` on the run's
  [broker](/api/v1/docs/broker) section digests the symbol specifications and fees inside the
  latter. Empty on a row written before they existed.
- `period_tick_count` — how many ticks the combination's booking periods applied, summed. Null on a
  row written before it existed.

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
`account_max_drawdown_pct` are null on rows written before those columns existed.
`account_max_drawdown` is null where no drawdown was measured. A fabricated zero would assert a
measurement nobody took.

`signal_fresh_ratio` is the weakest signal channel of the run, and null where no signal worker was
involved at all — deliberately not `1.0`, which would claim a perfect feed. See
[signal](/api/v1/docs/signal).

## Errors

An unknown sweep id is `404 sweep_not_found`. The whole refusal vocabulary is in
[errors](/api/v1/docs/errors).
