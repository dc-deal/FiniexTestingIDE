# The run index

Every report route is addressed by a run id, and a run id is a timestamp and a hash. Without an
index you would be guessing at those, and you would have to open each run in turn to find out what
it was, what it did and whether it even holds the section you want. This is that index: every
recorded run, newest first, each row saying what the run was, which kind of run it was, what it
produced — and, joined into the same request, what it did.

**Routes**

```
GET /api/v1/reports/runs
```

**What is not here:** the sections of a single run. Each one has its own document, and
[`/api/v1/docs`](/api/v1/docs) lists them. A sweep's combinations are in
[sweeps](/api/v1/docs/sweeps), a deployment's sessions in [deployments](/api/v1/docs/deployments);
both are families of runs, and neither is itself a run.

## One row is one run

```json
{ "key": ["run_id"], "results_key": ["currency"], "runs": [ ... ], "count": 3 }
```

A run is indexed from what it records about itself when it **starts**, so a run that crashed still
appears — with nothing in `artifacts`. Every indexed run is listed whether or not it produced a
report; an index with no rows is an ordinary `200`, never a 404. A lookup by `run_id` on any other
route is an exact match against this list.

`results_key` belongs to the nested `results` list further down: a run with two account currencies
has two entries there, and folding them into one loses data. See
[row keys](/api/v1/docs/row-keys).

## What the run was

| Field | Meaning |
|---|---|
| `run_id` | its identity, and its address on every other report route |
| `group` | which of the two pipelines produced it — `simulation` or `autotrader` |
| `name` | the scenario set it ran, or the profile it ran |
| `start_time` | when the run began |
| `parent_id` · `parent_kind` | the sweep or deployment it belongs to, and which of the two that id names |
| `ticks_from` · `orders_to` | which KIND of run this is |
| `data_windows` | the market window each unit was declared to cover, one per unit |
| `app_version` · `git_commit` | the program version and the commit it ran from |
| `config_snapshot` · `config_id` | the configuration it was commissioned with |
| `run_purpose` | what the run is FOR — `regular`, `fixture` or `certificate` |
| `report_contract` | the contract its reports were written under |
| `fixture_superseded` | for a run of the fixture catalog, whether it is no longer its entry's current one |
| `origin_channel` · `origin_client` · `origin_person` · `origin_host` | who started the run: how, which client, for which account, on which installation — the header's `origin`, see [run header](/api/v1/docs/run-header) |

`group`, the two parent fields, `ticks_from`, `orders_to`, `data_windows` and `run_purpose` are the
subject of [run kinds](/api/v1/docs/run-kinds), and it is worth reading before branching on any of
them. The
trap in one line: **`group` is the pipeline, never the nesting** — a sweep combination is a
`simulation` whose parent names its sweep.

`start_time` is when the run was executed. `data_windows` is what it was asked to **cover**, one
window per unit and never one span over all of them. A date filter asks the second question, not
the first.

`config_snapshot` is the configuration's **file name** — the same name
[the directory](/api/v1/docs/directory) addresses a file by. `config_id` is the registered identity
of that configuration's **content**: two runs naming the same id ran the same configuration, and a
changed file mints a new one. It is empty on a run that started before that registry existed, and
on one whose configuration could not be registered. The configuration itself is served by
[config](/api/v1/docs/config).

`report_contract` is the [contract](/api/v1/docs/contract-log) number of the code that started the
run. A later contract can add a figure that an older run serves as `0` or `null`; when this number
is below the one the server answers with, check the contract log before reading such a figure as a
measurement. `null` on a run recorded before the field.

`fixture_superseded` is about the runs this side produces for consumers to pin — its fixture
catalog. `false`: the run was made by its entry's current catalog production, so it is the one to
pin. `true`: an older catalog production made it, one that failed its check, or a production of an
entry the catalog no longer declares. A replaced run that
passed its check stays until this side releases it, so a pin on it keeps working until you move
it. `null`: no catalog production made the run — every ordinary run and every test run. A list
hiding superseded fixtures filters on `true` alone.

## `artifacts` and `reporting`, read as a pair

`artifacts` names every report file the run persisted. `has_reports` is derived from that list
being non-empty, so the two can never disagree.

**The two pipelines produce different sets of sections.** An AutoTrader session has no scenario
details, no profiling and no aggregated portfolio, so a client that assumes one fixed list of
sections gets a 404 for the difference — `artifact_not_produced`. Read `artifacts` first and ask
only for what is there. [Errors](/api/v1/docs/errors) has the four 404s of one run and what each
one asks of you.

`reporting` is `expected` or `none` and says whether the run was ever **commissioned** to report.
Read it together with `artifacts`: without the pair, a run that crashed before its report phase
and a run that was never meant to write one look identical. [Run kinds](/api/v1/docs/run-kinds)
has the three cases side by side.

A run with no artifacts at all exists as logs only, which is a legitimate state and not an
omission.

## `stream_files` — the run's streams

`stream_files` names the run's streams: today the [order events](/api/v1/docs/order-events). A
live session writes them as it runs, a backtest with its report. They are kept out of `artifacts`
on purpose. A live
session writes its stream from its first order, so a session that ended without its report still
has one — counted as an artifact, it would read as reported. Ask for a stream whenever it is
listed, whatever `artifacts` says.

## What the run did

The figures come from the run-results ledger, joined into this same request — so listing runs
costs one call rather than one call per run.

`results` is one entry per account currency: `currency`, `net_pnl`, `total_trades`, folded from
that run's booking periods. It has three states and they are not interchangeable — null, `[]`, and
a list. Null means the ledger holds nothing for this run (it is still going, it died before its
close, or it was never commissioned to report); `[]` means it closed with no figures to record.
[Nulls](/api/v1/docs/nulls) has the general rule.

**Nothing here is summed across currencies, and nothing should be.** The entries are folded per
currency precisely because a P&L column added over two currencies is not a number.

`run_outcome` is how the run ended:

| Value | Meaning |
|---|---|
| `success` | every unit completed and nothing logged an error |
| `finished_with_errors` | the run completed, but errors were logged |
| `failed` | units failed, or a session ended in an emergency shutdown |
| `crashed` | the process did not complete |

Three counts stand beside it: `error_count`, `warning_count` — findings a validator produced —
and `log_warning_count`, warnings the run itself logged. The two warning channels are
deliberately separate; [warnings and errors](/api/v1/docs/warnings-errors) explains them. All
four are null where they were not recorded, and **not recorded is not the same as clean**.

`tick_timespan_seconds` is the **market** time the run's units processed, covered together so a
stretch two scenarios share counts once. It is neither wall-clock time nor a sum over the units,
and it is null where it was not recorded — the figure arrived in contract 17.

## `size_bytes` is zero for two different reasons

Bytes on disk, stamped once when the run finished rather than measured when you ask. It reads
`0` both on a run indexed before the figure existed **and** on a run still in flight —
indistinguishable on purpose, because both mean "no figure was recorded". Render it as unknown;
an empty run is the one thing it does not mean.

## Four lists, four counts, and they differ on purpose

A simulation run's scenarios appear in four places, and the four counts are not meant to agree.
Each route answers a different question, so four correct answers read as a contradiction unless
you know which question you asked:

| Stage | What it is | Where |
|---|---|---|
| **declared** | every scenario the configuration names, switched-off ones included | [config](/api/v1/docs/config) |
| **attempted** | every enabled scenario the engine tried, each with its outcome | [scenario details](/api/v1/docs/scenario-details) |
| **produced** | every scenario that ran and wrote results | [portfolio](/api/v1/docs/portfolio) and the other per-unit sections |
| **counted** | what the run's own KPIs are summed over | [run summary](/api/v1/docs/run-summary) |

The run summary states that difference itself — which units are missing from its figures, and why
— so you never have to compare two routes to learn what a number leaves out.

**Scenario details is the authority for "which scenarios does this run have".** It is the one
section built from the batch rather than from the results, so a scenario that produced nothing is
still a row there rather than an absence.

An AutoTrader session has no scenario grid at all: a session is one unit. The list of one bot's
sessions is [deployments](/api/v1/docs/deployments).
