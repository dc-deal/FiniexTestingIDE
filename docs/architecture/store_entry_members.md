# Store Entry Members — What One Run Directory May Hold

A session that died before its report used to read as reported after the run index was rebuilt.
The index listed as a run's `artifacts` every file in its `io/` folder that was not on a short list
of exceptions, so the temporary file an interrupted atomic write leaves behind — or any file a new
writer added without telling the index — counted as a report. "Not completed" hangs on that list
being empty. The run store now declares every file a run directory may hold, and everything that
needs to know is derived from that one declaration: the two lists the run list serves, the
directories the pruner treats as a run's own, and a census that runs real runs and fails on a file
nobody declared.

This document is the declaration's reference: the levels it sits in, the shapes a file can have,
the tables of every member, and how to add one. Where the stores themselves are, and of what kind,
is [data_storage_layout.md](data_storage_layout.md); how the run list serves the two lists is
[api_server_architecture.md](api_server_architecture.md).

---

## The levels

```
CATALOG     which stores exist, where, of what kind          StoreCatalog
 ├ INDEX    which entries a store holds — searched           one per store
 └ MEMBERS  what ONE entry may hold — every file, its shape   the entry members
```

A store whose entry is a single file declares no members. A store whose entry is a directory — a
run, a sweep — declares every file it may hold, in
[`run_entry_members.py`](../../python/framework/store/entry_members/run_entry_members.py) and
[`sweep_entry_members.py`](../../python/framework/store/entry_members/sweep_entry_members.py). A
sweep directory also holds its combinations, and those are runs, declared by the run store; the
catalog records that nesting, so a sweep's size is its own files and its combinations are counted
once, as runs.

`python python/cli/store_cli.py entry-members runs` prints the run store's table;
`--markdown` prints it as the tables below.

## The store is the authority over its entries

The declaration says **what** an entry holds, never **how** it is stored. A member has an identity
(`RunEntryMemberId`), a path inside the entry, a shape, the pipelines that write it and when, the
way its bytes reach the disk, the document that explains it, and — for a stream — the version of
its line shapes. The path is the address on disk today; a store kept somewhere else later brings
its own address for the same identity, and a caller that asks by identity does not change.

Three things are derived from it, and none of them keeps a list of its own:

- **The run list.** `artifacts` lists the files of `io/` the reporting writes at the run's end;
  `stream_files` lists the served streams. Both are allow-lists: a file no member declares for the
  column is listed in neither. A test pins what each column serves, because a change there changes
  what a consumer of the run list reads.
- **The pruner.** The directories a run directory holds are the first parts of the member paths;
  they go with their run and are never an orphan of their own.
- **The census.** A suite that makes a real sweep and real mock sessions and reads what they left
  ([run_entry_census_tests.md](../tests/framework/run_entry_census_tests.md)).

The names come from one place too. Every file and directory name a run writes, and every stream's
schema version, is a constant in
[`run_layout_constants.py`](../../python/framework/constants/run_layout_constants.py), and the
declaration reads them. That module imports nothing, so a writer, a reader and the declaration can
each import it without a cycle — measured 2026-10-22: through the report specs the declaration
would have loaded 719 modules in 1.6 s and could not have been imported by the report writer, the
run stream appender or the logger base.

## Writing a run's files

A writer names a member and hands over the content; the path comes from the declaration, never
from the writer. `RunEntryWriter(run_dir, run_type)` has one method per shape — a report section,
a snapshot, a table, a stream, a strategy file — and refuses a member its pipeline does not write
or a member of another shape than the method writes. Two kinds still receive a path from it and
open the file themselves: a log, which its logger keeps open (`log_path`), and the acceptance
studies' captures, which keep their own writer (`path_of`) until #631 records what they need
through the session's own records. A stream opened by a session's writer joins the session's stream
sealer, which seals its trading days into day records
([Day Records](run_stream_day_records.md)); a stream that may not be missing is opened as required,
so a failed open ends the start — the order-event stream is. A logger writes the log its member
names, and a logger without a member writes no file.

A sweep directory's own files — the mount build's log and the ranking — are written the same way
by `SweepEntryWriter(sweep_dir)`; each combination is a run.

## Reading a run's files

A reader asks for a member too. `RunEntryReader` finds a run by its id through the run index — an
exact match, the guard an id arriving from a URL needs — and `RunDirReader(run_dir)` reads a run
directory a caller already holds: the run itself while it reports, a walk that finds runs by their
header, the pruner, a certificate. Both read a header and a report section, and a stream as one —
its sealed trading days and its own file (`read_stream`, `read_stream_ends`, `read_stream_index`) —
and ask the disk whether a file is there. What a file holds is read by its own reader, with that
reader's errors: a section that no longer fits its model raises the validation error, a missing
header raises like any missing file. A walk searches by the member's declared path, and a message
names a file by the member's declared file name.

A test holds the code to this: a layout constant that names a part of a member's path is imported
only by its sources, such a file name never stands as a literal, and no path is joined from a
report section's file name outside the section writer
([store_tests.md](../tests/framework/store_tests.md)). One module keeps its own name rule on
purpose — the session-log retention deletes only names of eight digits, narrower than the declared
day log — and the test lists it with that reason.

## Shapes, moments and write modes

The shape decides how a file is written and how it is read.

| Shape | What it is | Written |
|---|---|---|
| snapshot | one document saying what is true now | replaced whole — atomically where something reads it while it is written |
| stream | records appended line by line, a header line first naming the stream and its schema version | appended and flushed; a crash can cut the last line |
| report output | derived by the reporting pipeline at the run's end | once, at the end |
| log | lines for a person to read, never parsed by the code | appended |
| strategy output | a strategy's own file, kept with the run and never read by the framework | replaced whole, atomically, whenever the strategy writes it |
| day record | one trading day of a session's stream, sealed at the day's boundary ([run_stream_day_records.md](run_stream_day_records.md)) | once, whole and atomically; never again |

The moment says when a member's file **first appears** — and so what a run that stopped at that
point already holds. A file created at the start and filled at the end appears at the start.
`at start` · `as it runs` (when something happens, perhaps never) · `at end` (written by the
reporting once the run has ended) · `on demand` (by a command run afterwards). Only a member
written at the end may be listed among the artifacts: a file listed there that a session writes
before its report would make a session that died before its report read as reported.

## The footprint a run closes with

A run writes `footprint.json` as its very last step: every file its directory holds at that moment,
with its size, and — for each member written only sometimes — whether this run expected it, and
why. A run with a footprint is **closed**; without one it never got there. That is the run's own
commit, written after the ledger row in a backtest and after the console summary in a session, so
nothing but a log line follows it. A sweep has no footprint of its own; its combinations each close
as runs.

Read against the declaration and the disk, every member of the run's pipeline is in one state:

| State | Meaning |
|---|---|
| `present` | on disk, and in the footprint |
| `skipped` | written only sometimes, and this run did not expect it — the reason says why |
| `failed` | expected, or always written by this pipeline, and not there at the close |
| `unknown` | written only sometimes, and nothing says whether this run expected it |
| `missing` | in the footprint, gone from the disk since |
| `late` | on disk, but written after the run closed |

A run without a footprint is `running` while its activity is listed, `not_closed` once it is not,
and `not_commissioned` when it was never meant to report. A failed writer never reads as `skipped`:
the condition is judged from what the run knew at its close, so an expected file that is absent is
`failed`, and the run's log carries the writer's own error. A run that stopped early closes too — a
session its startup checks or its cold start refused, a backtest none of whose scenarios ran — and
a file it never got to reads as `skipped` with the step it stopped before (`the session stopped
before its tick loop`), never as `failed`.

`python python/cli/store_cli.py run-footprint <run_id>` prints it and exits 1 when something needs a
look:

```
🧾  run 20261023_063210_881fe783 (autotrader) — closed 2026-10-23 06:32:14 UTC
  present   io/trade_history.json · io/state_frames.jsonl · … (27)
  skipped   io/cold_start.json — the boot found nothing to inherit
  FAILED    io/tick_stream_events.jsonl — expected — the session reads its ticks from a venue — but not written
```

## Strategy files

A strategy may keep files of its own with its run — the setup it acted on, a channel it drew, a
table it wants to look at afterwards. They go into `strategy_files/` in the run directory, one
folder per scenario in a backtest (`strategy_files/<scenario>/<name>`), and nothing in the
framework reads or serves them.

```python
self.strategy_file('last_setup.json').write_json({'gate': 'up', 'entry_price': 1.0842})
self.strategy_file('notes.txt').write_text('…')
```

- **Written at once and atomically, replaced on every write.** A file is the old one or the new
  one, never half. The strategy names the file and never sees a path, so it cannot write anywhere
  else in the run.
- **A name holds no folder, does not start with a dot and does not end in `.tmp`** — the last two
  are the names a temporary write takes.
- **A write that fails never ends the run.** A refused name, an unwritable disk or content that
  is no JSON is said once on the run's channel, as an error, and the write answers `False`.
- **Write at decision moments, never per tick.** One write of a small file cost about 0.2 ms on a
  local disk and about 12 ms on the development container's bridged mount (measured 2026-10-23).
- **The footprint counts them, per member, as a number and a size** instead of listing them; a
  run whose strategy wrote none reads as skipped, never as failed.

Not here: what the framework has to read back. A strategy's memory across restarts belongs in
the algo state store (`uses_state_persistence()`), and a table the run's report should carry in a
diagnostics sink (`diagnostics_csv`). The CORE logic `trend_channel_reference` shows the helper
behind its `write_strategy_files` parameter.

## What a run directory may hold

<!-- generated by: store_cli.py entry-members runs --markdown -->
| Member | File | Shape | simulation | autotrader | Listed in | Served at |
|---|---|---|---|---|---|---|
| [`header`](run_origin_and_code_identity.md#what-a-run-header-carries) | `header.json` | snapshot | at start | at start | — | `…/header` |
| [`footprint`](store_entry_members.md#the-footprint-a-run-closes-with) | `footprint.json` | snapshot | at end — the run was commissioned to report | at end | — | — |
| [`backtest_global_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_global_log.log` | log | at start — log files are switched on | — | — | — |
| [`backtest_summary_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_summary.log` | log | at start — log files are switched on | — | — | — |
| [`backtest_system_info_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_system_info.log` | log | at start — log files and system information logging are switched on | — | — | — |
| [`scenario_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `scenario_logs/{log_prefix}_{scenario_no}_{scenario}.log` | log | as it runs — one per scenario, when log files are switched on | — | — | — |
| [`session_global_log`](../autotrader/autotrader_observability.md#logging) | `autotrader_global.log` | log | — | at start — log files are switched on | — | — |
| [`session_summary_log`](../autotrader/autotrader_observability.md#logging) | `autotrader_summary.log` | log | — | at start — log files are switched on | — | — |
| [`session_boot_log`](../autotrader/autotrader_architecture.md#session-logs--daily-rotation-and-what-is-kept) | `autotrader_session.log` | log | — | at start — log files are switched on | — | — |
| [`session_day_log`](../autotrader/autotrader_architecture.md#session-logs--daily-rotation-and-what-is-kept) | `session_logs/autotrader_session_{day}.log` | log | — | as it runs — one per trading day, when log files are switched on; the retention removes the oldest | — | — |
| [`scenario_events`](trade_execution_visibility.md#event-stream-csv-eventscsv) | `events/events_{scenario}.csv` | report output | at end — one per scenario that placed an order | — | — | — |
| [`session_events`](trade_execution_visibility.md#event-stream-csv-eventscsv) | `events.csv` | report output | — | at end — the session placed an order | — | — |
| [`scenario_diagnostics`](diagnostics_csv_sink.md#lifecycle-framework-owned) | `diagnostics/{sink}_{scenario}.csv` | report output | at end — the logic declares a sink and recorded a row | — | — | — |
| [`session_diagnostics`](diagnostics_csv_sink.md#lifecycle-framework-owned) | `diagnostics/{sink}.csv` | report output | — | at end — the logic declares a sink and recorded a row | — | — |
| [`scenario_strategy_file`](store_entry_members.md#strategy-files) | `strategy_files/{scenario}/{name}` | strategy output | as it runs — the strategy writes a file of its own | — | — | — |
| [`session_strategy_file`](store_entry_members.md#strategy-files) | `strategy_files/{name}` | strategy output | — | as it runs — the strategy writes a file of its own | — | — |
| [`field_study_capture`](../tests/live_field_study/field_study_guide.md#jsonl-schema) | `field_study.jsonl` | stream · v2.0 | — | at start — a field-study profile | — | — |
| [`security_cert_capture`](../tests/security_cert/security_cert_guide.md#the-capture) | `security_cert.jsonl` | stream · v2.0 | — | at start — a security-study stage | — | — |
| [`aggregated_portfolio`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/aggregated_portfolio.json` | report output | at end | — | `artifacts` | `…/aggregated-portfolio` |
| [`block_splitting`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/block_splitting.json` | report output | at end — a profile run that split blocks | — | `artifacts` | — |
| [`booking_periods`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/booking_periods.json` | report output | at end | at end | `artifacts` | `…/booking-periods` |
| [`broker`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/broker.json` | report output | at end | at end — the session built its broker configuration | `artifacts` | `…/broker` |
| [`cold_start`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/cold_start.json` | report output | — | at end — the boot found something to inherit | `artifacts` | — |
| [`execution_stats`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/execution_stats.json` | report output | at end | at end | `artifacts` | `…/execution-stats` |
| [`feed_stability`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/feed_stability.json` | report output | at end | at end | `artifacts` | `…/feed-stability` |
| [`order_history`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/order_history.json` | report output | at end | at end | `artifacts` | `…/order-history` |
| [`pending_orders`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/pending_orders.json` | report output | at end | at end | `artifacts` | `…/pending-orders` |
| [`portfolio`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/portfolio.json` | report output | at end | at end | `artifacts` | `…/portfolio` |
| [`profiling`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/profiling.json` | report output | at end | — | `artifacts` | `…/profiling` |
| [`robustness`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/robustness.json` | report output | at end — robustness mode is on | — | `artifacts` | — |
| [`run_meta`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/run_meta.json` | report output | at end | — | `artifacts` | — |
| [`run_summary`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/run_summary.json` | report output | at end | at end | `artifacts` | `…/run-summary` |
| [`safety`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/safety.json` | report output | — | at end — a risk baseline was taken | `artifacts` | — |
| [`scenario_details`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/scenario_details.json` | report output | at end | — | `artifacts` | `…/scenario-details` |
| [`signal`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/signal.json` | report output | at end | at end | `artifacts` | `…/signal` |
| [`trade_history`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/trade_history.json` | report output | at end | at end | `artifacts` | `…/trade-history` |
| [`venue_account`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/venue_account.json` | report output | — | at end — the session asked its venue | `artifacts` | `…/venue-account` |
| [`venue_api_performance`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/venue_api_performance.json` | report output | — | at end — the session watched its venue API | `artifacts` | `…/venue-api-performance` |
| [`warnings_errors`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/warnings_errors.json` | report output | at end | at end | `artifacts` | `…/warnings-errors` |
| [`worker_decision`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/worker_decision.json` | report output | at end | at end | `artifacts` | `…/worker-decision` |
| [`execution_stats_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/execution_stats.csv` | report output | at end | at end | `artifacts` | — |
| [`order_history_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/order_history.csv` | report output | at end | at end | `artifacts` | — |
| [`trade_history_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/trade_history.csv` | report output | at end | at end | `artifacts` | — |
| [`order_events`](order_event_stream.md#storage) | `io/order_events.jsonl` | stream · v2 | at end — a scenario recorded an order event | at start — the session got through its startup checks | `stream_files` | `…/order-events` |
| [`state_frames`](live_telemetry_architecture.md#the-recorded-state-frame--not-this-stream) | `io/state_frames.jsonl` | stream · v1 | — | at start — the profile records state frames | `stream_files` | `…/state-frames/latest` · `…/units/{unit_name}/recorded-streams` · `…/units/{unit_name}/recorded-streams/window` |
| [`tick_stream_events`](../autotrader/autotrader_architecture.md#what-a-session-records) | `io/tick_stream_events.jsonl` | stream · v1 | — | at start — the session reads its ticks from a venue | — | — |
| [`diagnostics_rows`](diagnostics_csv_sink.md#lifecycle-framework-owned) | `diagnostics/{sink}.jsonl` | stream · v1 | — | as it runs — the logic declared a diagnostics sink | — | — |
| [`trades`](../autotrader/autotrader_architecture.md#what-a-session-records) | `io/trades.jsonl` | stream · v1 | — | at start — the session reached its tick loop | — | — |
| [`order_events_day`](run_stream_day_records.md#what-a-day-record-holds) | `days/{day}/io/order_events.parquet` | day record | — | as it runs — the session got through its startup checks, and a trading day ended while the session ran | — | — |
| [`state_frames_day`](run_stream_day_records.md#what-a-day-record-holds) | `days/{day}/io/state_frames.parquet` | day record | — | as it runs — the profile records state frames, and a trading day ended while the session ran | — | — |
| [`tick_stream_events_day`](run_stream_day_records.md#what-a-day-record-holds) | `days/{day}/io/tick_stream_events.parquet` | day record | — | as it runs — the session reads its ticks from a venue, and a trading day ended while the session ran | — | — |
| [`trades_day`](run_stream_day_records.md#what-a-day-record-holds) | `days/{day}/io/trades.parquet` | day record | — | as it runs — the session reached its tick loop, and a trading day ended while the session ran | — | — |
| [`diagnostics_rows_day`](run_stream_day_records.md#what-a-day-record-holds) | `days/{day}/diagnostics/{sink}.parquet` | day record | — | as it runs — the logic declared a diagnostics sink, and a trading day ended while the session ran | — | — |
| [`warmup_history`](warmup_history.md#what-a-session-records) | `io/warmup_history.json` | snapshot | — | at start — a worker requires bars | — | — |
| [`algo_state_restored`](../user_guides/algo_state_persistence_guide.md#lifecycle) | `io/algo_state_restored.json` | snapshot | — | at start — the logic keeps its state and a saved state was found | — | — |
<!-- end of generated table -->

Where a member is written only sometimes, the cell says when. The `{name}` parts of a path stand
for one variable part of a file name — a scenario, a trading day, a sink — and never span a
directory.

## What a sweep directory holds beside its combinations

<!-- generated by: store_cli.py entry-members sweeps --markdown -->
| Member | File | Shape | simulation | Listed in | Served at |
|---|---|---|---|---|---|
| [`mount_build_log`](parameter_optimization_system.md#what-a-sweep-directory-holds) | `mount_build.log` | log | at start — mount reuse is switched on | — | — |
| [`ranking`](parameter_optimization_system.md#what-a-sweep-directory-holds) | `ranked.csv` | report output | on demand — the report command ran | — | — |
<!-- end of generated table -->

## Adding a member

1. Name the file in `run_layout_constants.py`. The declaration reads the constant; the writer asks
   the run entry writer for the member, the reader asks a run reader, and neither builds the path
   itself.
2. Add its id to `RunEntryMemberId` and declare it in `run_entry_members.py`: path, shape, what it
   holds, who writes it and when, the write mode, its document — its index column only if the
   run list is meant to serve it, which changes what the API serves and raises its contract, and
   its `served_route` when a route below a run serves the file. A test holds the declared routes
   to the routes the API mounts. A stream a unit's timeline and window serve declares its
   `recorded_stream_kind` instead of a route of its own (#580); a test holds every kind to exactly
   one stream, and that stream to the one time field its pages are cut by.
3. Where a pipeline writes it only sometimes, have that pipeline's report coordinator judge it at
   the close — an expectation in its `_footprint_expectations`, so a run that did not write it
   reads as skipped with the reason, or as failed. A member nobody judges reads as unknown, and
   the census fails on it unless it names where the member is judged instead.
4. Make sure a census run writes it. Where none can — a member only a venue or a profile run
   produces — name the test that does in the census's short table of exceptions.
5. Regenerate the table above: `python python/cli/store_cli.py entry-members runs --markdown`. A
   test holds this document and the declaration together.

A writer that adds a file without these steps is caught by the census, and a member no run
writes any more is caught by it too.

## What does not belong here

- **The bytes.** The declaration names files; it does not read or write them. The run entry writer
  and reader do, by member.
- **The run's own content.** What a report section or a stream line carries is its own document,
  linked from each member above.
- **Other stores.** The carry-overs and the archives keep one file per entry today and declare no
  members.
