# Scenario details

A backtest is many scenarios, and the sections that report money only know the ones that produced
some. A scenario refused before it ran has no portfolio row, no trades and no timings — read those
sections alone and a scenario that was rejected looks exactly like one that was never configured.
This section is the run's full scenario grid: one row per scenario the run attempted, failed ones
included, saying what each one read, what it did, and why it stopped.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/scenario-details
```

**What is not here:** how many trades a scenario closed. That is `total_trades` on
[portfolio](/api/v1/docs/portfolio), joined on the unit's name — see
[row keys](/api/v1/docs/row-keys). What the workers and the decision logic cost is
[workers and decisions](/api/v1/docs/worker-decision); where tick time went is
[profiling](/api/v1/docs/profiling).

Only a backtest writes this section. A live session is one unit and has no scenario grid at all,
so the route answers `artifact_not_produced` for one — read the run's `artifacts` on
[runs](/api/v1/docs/runs) before asking, and see [run kinds](/api/v1/docs/run-kinds) and
[errors](/api/v1/docs/errors).

## The authority for which scenarios a run has

This is the one section built from the run's own batch rather than from its results, so a scenario
that produced nothing is still a row. Where another per-unit section lists fewer units, this list
is the complete one.

```json
{ "keys": { "units": ["name"], "data_brokers": ["data_broker_type"] } }
```

`units` is one row per scenario; `data_brokers` rolls those same rows up per broker. The unit's
`name` is the value every other per-unit section joins on — see
[row keys](/api/v1/docs/row-keys).

## Three statuses

| `status` | What happened |
|---|---|
| `success` | the scenario ran and reported nothing wrong |
| `hybrid` | it ran **and** carries an error — a partial result |
| `failed` | it never ran: preparation or validation refused it, so there is no execution data |

`error_type` and `error_message` say why, and are empty on a scenario that reported nothing wrong.

A `failed` row still carries everything that is known before a scenario runs: its name, symbol,
broker, market type, the provenance fields below, its account currency and its declared worker
count. What it does not carry are the execution figures — `ticks_processed` is 0, the tick times
are empty, the decision counters are null.

That the provenance survives a failure is deliberate: a scenario that failed over development data
and one that failed over production data are different failures, and this row is the only place
per scenario where that distinction outlives the run.

## Which data the scenario read

`data_broker_type` is the broker whose ticks the scenario read, and it is the key the
[market data](/api/v1/docs/market-data) routes are addressed by. `market_type` says what that
broker is. It is resolved once when the report is built, from the configuration that owns the
answer, so the figure never drifts when that configuration is later edited. It sits on the row as
well as on the roll-up below, so no reader has to join the two to learn whether a scenario traded
crypto or forex.

Four fields describe the files themselves. Each is a **comma-joined list of the distinct values
across the scenario's files, sorted**: one scenario reads many files and they need not agree.

| Field | What it says |
|---|---|
| `data_format_versions` | the data format version the files were written in |
| `origin_classes` | what the producing instance was — `production`, `development` or `unknown` |
| `origin_evidence_grades` | how well that is known — `stamped`, `attested` or `unknown` |
| `price_bases` | which price the scenario's bars were rendered from |

**Read the class and the grade together.** `production` from the producer's own stamp and
`production` from a claim recorded afterwards are the same word and a different fact. `stamped`
means the producer wrote its identity into the file and that was resolved; `attested` means a
dated claim covers files written before it could; `unknown` means nobody has said anything, which
is the honest reading of an unmapped identity and of everything written before the field existed.

`price_bases` is empty where the scenario mounted no bar file — bars are the only archive that
stamps a basis — and it is never filled in from what the broker is *declared* to be, because what
a file was rendered from and what a render would produce today are two questions that disagree
while a re-render is unfinished. The vocabulary is the one
[market data](/api/v1/docs/market-data) serves beside its bars.

The sorting is not cosmetic. The same set of files produces the same string whatever order they
were read in, so two runs over the same data compare as strings. The run as a whole records the
same answer; this row is the grain that says *which scenario* — and a set mixing brokers or data
eras is exactly where the run-wide answer stops being enough.

## The per-broker roll-up

`data_brokers` groups the rows by broker, sorted by broker type. Each entry carries that broker's
`market_type`, its `scenario_count` — failed scenarios included, because it is a roll-up of the
same rows — the distinct `symbols` it served, sorted, and the `price_bases` de-duplicated across
all of that broker's scenarios rather than concatenated.

The grouping is derived once and served to every surface rather than rebuilt by whoever wants it,
so two consumers grouping the same rows cannot arrive at two answers.

## What the scenario did

| Field | Meaning |
|---|---|
| `ticks_processed` | how many ticks reached the strategy |
| `first_tick_time` · `last_tick_time` | ISO-8601 UTC; empty where there was no tick |
| `tick_timespan_seconds` | the market time those ticks span |
| `execution_time_ms` | wall time the scenario took, preparation to result, on a monotonic clock |

The last two are different clocks and routinely differ by orders of magnitude: one is how much
market the scenario covered, the other how long the machine took to replay it.

`tick_timespan_seconds` is `0.0` where no tick was processed, not null. No tick is no market time,
and the row states that as a figure.

A scenario recorded before contract 13 carries **seconds** in `execution_time_ms`, despite the
name. See [the contract log](/api/v1/docs/contract-log).

## The decision counters are null when nothing counted them

`buy_signals`, `sell_signals`, `flat_signals` and `trades_requested` are what the decision logic
decided. They are **null when nothing counted them**: the decision tracker is off by default in a
backtest, because counting sits on the hot path. A `0` is a count; null is the absence of one. See
[nulls](/api/v1/docs/nulls).

Which state a unit was in is stated outright, as `worker_decision_tracked` on
[workers and decisions](/api/v1/docs/worker-decision). That is the field to branch on.

A scenario recorded before contract 9 reads `0` on all four whether or not anything counted them,
because the row served its defaults as figures.

These are the strategy's own decisions, not the external signal feed. That has its own section —
[signal](/api/v1/docs/signal).

## Account currency and worker count

`account_currency` is the currency the scenario's P&L is denominated in.
`account_currency_explicit` says whether the configuration set it or whether it was derived. A
derived currency is a correct answer rather than a missing one, but it is the configuration's to
override.

`worker_count` is what the scenario **declares** — the number of worker instances in its strategy
configuration. It is therefore known for a scenario that never ran, which is why it is a
declaration and not a count of what executed. What those workers actually cost is on
[workers and decisions](/api/v1/docs/worker-decision).
