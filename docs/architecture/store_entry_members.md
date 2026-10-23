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
[`run_layout_constants.py`](../../python/framework/constants/run_layout_constants.py); the writers
build their paths from it and the declaration reads the same constants. That module imports
nothing, so a writer, a reader and the declaration can each import it without a cycle — measured
2026-10-22: through the report specs the declaration would have loaded 719 modules in 1.6 s and
could not have been imported by the report writer, the run stream appender or the logger base.

## Shapes, moments and write modes

The shape decides how a file is written and how it is read.

| Shape | What it is | Written |
|---|---|---|
| snapshot | one document saying what is true now | replaced whole — atomically where something reads it while it is written |
| stream | records appended line by line, a header line first naming the stream and its schema version | appended and flushed; a crash can cut the last line |
| report output | derived by the reporting pipeline at the run's end | once, at the end |
| log | lines for a person to read, never parsed by the code | appended |

The moment says when a member's file **first appears** — and so what a run that stopped at that
point already holds. A file created at the start and filled at the end appears at the start.
`at start` · `as it runs` (when something happens, perhaps never) · `at end` (written by the
reporting once the run has ended) · `on demand` (by a command run afterwards). Only a member
written at the end may be listed among the artifacts: a file listed there that a session writes
before its report would make a session that died before its report read as reported.

## What a run directory may hold

<!-- generated by: store_cli.py entry-members runs --markdown -->
| Member | File | Shape | simulation | autotrader | Listed in |
|---|---|---|---|---|---|
| [`header`](run_origin_and_code_identity.md#what-a-run-header-carries) | `header.json` | snapshot | at start | at start | — |
| [`backtest_global_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_global_log.log` | log | at start | — | — |
| [`backtest_summary_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_summary.log` | log | at start | — | — |
| [`backtest_system_info_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `{log_prefix}_system_info.log` | log | at start — system information logging is switched on | — | — |
| [`scenario_log`](../process_execution_guide.md#6-per-scenario-logger-files) | `scenario_logs/{log_prefix}_{scenario_no}_{scenario}.log` | log | as it runs — one per scenario | — | — |
| [`session_global_log`](../autotrader/autotrader_observability.md#logging) | `autotrader_global.log` | log | — | at start | — |
| [`session_summary_log`](../autotrader/autotrader_observability.md#logging) | `autotrader_summary.log` | log | — | at start | — |
| [`session_boot_log`](../autotrader/autotrader_architecture.md#session-logs--daily-rotation-and-what-is-kept) | `autotrader_session.log` | log | — | at start | — |
| [`session_day_log`](../autotrader/autotrader_architecture.md#session-logs--daily-rotation-and-what-is-kept) | `session_logs/autotrader_session_{day}.log` | log | — | as it runs — one per trading day; the retention removes the oldest | — |
| [`scenario_events`](trade_execution_visibility.md#event-stream-csv-eventscsv) | `events/events_{scenario}.csv` | report output | at end — one per scenario that placed an order | — | — |
| [`session_events`](trade_execution_visibility.md#event-stream-csv-eventscsv) | `events.csv` | report output | — | at end — the session placed an order | — |
| [`scenario_diagnostics`](diagnostics_csv_sink.md#lifecycle-framework-owned) | `diagnostics/{sink}_{scenario}.csv` | report output | at end — the logic declares a sink and recorded a row | — | — |
| [`session_diagnostics`](diagnostics_csv_sink.md#lifecycle-framework-owned) | `diagnostics/{sink}.csv` | report output | — | at end — the logic declares a sink and recorded a row | — |
| [`field_study_capture`](../tests/live_field_study/field_study_guide.md#jsonl-schema) | `field_study.jsonl` | stream · v2.0 | — | at start — a field-study profile | — |
| [`security_cert_capture`](../tests/security_cert/security_cert_guide.md#the-capture) | `security_cert.jsonl` | stream · v2.0 | — | at start — a security-study stage | — |
| [`aggregated_portfolio`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/aggregated_portfolio.json` | report output | at end | — | `artifacts` |
| [`block_splitting`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/block_splitting.json` | report output | at end — a profile run that split blocks | — | `artifacts` |
| [`booking_periods`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/booking_periods.json` | report output | at end | at end | `artifacts` |
| [`broker`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/broker.json` | report output | at end | at end — the session built its broker configuration | `artifacts` |
| [`cold_start`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/cold_start.json` | report output | — | at end — the boot found something to inherit | `artifacts` |
| [`execution_stats`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/execution_stats.json` | report output | at end | at end | `artifacts` |
| [`feed_stability`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/feed_stability.json` | report output | at end | at end | `artifacts` |
| [`order_history`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/order_history.json` | report output | at end | at end | `artifacts` |
| [`pending_orders`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/pending_orders.json` | report output | at end | at end | `artifacts` |
| [`portfolio`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/portfolio.json` | report output | at end | at end | `artifacts` |
| [`profiling`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/profiling.json` | report output | at end | — | `artifacts` |
| [`robustness`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/robustness.json` | report output | at end — robustness mode is on | — | `artifacts` |
| [`run_meta`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/run_meta.json` | report output | at end | — | `artifacts` |
| [`run_summary`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/run_summary.json` | report output | at end | at end | `artifacts` |
| [`safety`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/safety.json` | report output | — | at end — a risk baseline was taken | `artifacts` |
| [`scenario_details`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/scenario_details.json` | report output | at end | — | `artifacts` |
| [`signal`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/signal.json` | report output | at end | at end | `artifacts` |
| [`trade_history`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/trade_history.json` | report output | at end | at end | `artifacts` |
| [`venue_account`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/venue_account.json` | report output | — | at end — the session asked its venue | `artifacts` |
| [`warnings_errors`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/warnings_errors.json` | report output | at end | at end | `artifacts` |
| [`worker_decision`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/worker_decision.json` | report output | at end | at end | `artifacts` |
| [`execution_stats_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/execution_stats.csv` | report output | at end | at end | `artifacts` |
| [`order_history_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/order_history.csv` | report output | at end | at end | `artifacts` |
| [`trade_history_csv`](reporting_pipeline.md#report-sections--domain--migration-status) | `io/trade_history.csv` | report output | at end | at end | `artifacts` |
| [`order_events`](order_event_stream.md#storage) | `io/order_events.jsonl` | stream · v2 | at end — a scenario recorded an order event | at start | `stream_files` |
| [`state_frames`](live_telemetry_architecture.md#the-recorded-state-frame--not-this-stream) | `io/state_frames.jsonl` | stream · v1 | — | at start — the profile records state frames | `stream_files` |
| [`tick_stream_events`](../autotrader/autotrader_architecture.md#what-a-session-records) | `io/tick_stream_events.jsonl` | stream · v1 | — | at start — the session reads its ticks from a venue | — |
| [`warmup_history`](warmup_history.md#what-a-session-records) | `io/warmup_history.json` | snapshot | — | at start — a worker requires bars | — |
<!-- end of generated table -->

Where a member is written only sometimes, the cell says when. The `{name}` parts of a path stand
for one variable part of a file name — a scenario, a trading day, a sink — and never span a
directory.

## What a sweep directory holds beside its combinations

<!-- generated by: store_cli.py entry-members sweeps --markdown -->
| Member | File | Shape | simulation | Listed in |
|---|---|---|---|---|
| [`mount_build_log`](parameter_optimization_system.md#what-a-sweep-directory-holds) | `mount_build.log` | log | at start — mount reuse is switched on | — |
| [`ranking`](parameter_optimization_system.md#what-a-sweep-directory-holds) | `ranked.csv` | report output | on demand — the report command ran | — |
<!-- end of generated table -->

## Adding a member

1. Name the file in `run_layout_constants.py`, and build the writer's path from that constant.
2. Add its id to `RunEntryMemberId` and declare it in `run_entry_members.py`: path, shape, what it
   holds, who writes it and when, the write mode, its document — and its index column only if the
   run list is meant to serve it, which changes what the API serves and raises its contract.
3. Make sure a census run writes it. Where none can — a member only a venue or a profile run
   produces — name the test that does in the census's short table of exceptions.
4. Regenerate the table above: `python python/cli/store_cli.py entry-members runs --markdown`. A
   test holds this document and the declaration together.

A writer that adds a file without these steps is caught by the census, and a member no run
writes any more is caught by it too.

## What does not belong here

- **The bytes.** The declaration names files; it does not read or write them. A writer and a
  reader per shape that the store hands out are the next step of #621.
- **The run's own content.** What a report section or a stream line carries is its own document,
  linked from each member above.
- **Other stores.** The carry-overs and the archives keep one file per entry today and declare no
  members.
