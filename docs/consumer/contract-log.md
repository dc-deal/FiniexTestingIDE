# API Contract Log

A consumer of this API records the contract number its fixtures were captured under and asserts
it at start-up, so a stale mock fails locally instead of silently answering with an old shape. The
server tells them only what moved into the CURRENT number: `GET /api/v1/contract` returns
`changes` for this version and nothing older. A consumer whose fixtures are several versions
behind needs every step in between, and this log is where they are kept — newest first.

Not here: what each route serves TODAY. That is what the rest of these documents are for —
[`/api/v1/docs`](/api/v1/docs) lists them. This log answers only *what changed between the version
my fixtures hold and the one this server serves*.

**It is deliberately not a deprecation channel.** No compatibility layers ship, so a promise that
an old shape still works would be one we never keep. What is offered instead is true: the server
states which contract it IS, in every response's `X-Api-Contract` header as well as on
`/api/v1/contract`, and a stale fixture then fails loudly and locally — no connection needed,
because the number travels in the response it was captured from.

## What an entry promises

The number moves by one whenever a route moves, a response model moves, or a field starts meaning
something else. Nothing else moves it — not a release, not a bug fix that leaves the shapes alone.

**A change of MEANING counts as much as a change of shape**, and it is the one worth watching: a
field that keeps its type and starts answering a different question breaks a consumer just as
silently, and more so, because nothing fails to parse. Those changes are written here in the same
words as the rest, and `/api/v1/contract` carries the current version's lines in its `changes`.

What the server tells you is only the CURRENT version's lines. Every earlier one is here, and
nowhere else — the old lines are not kept in the server. So a consumer several versions behind
reads downwards from the version their fixtures hold until they reach the one being served.

A test holds the newest heading in this log to the number the server answers with, so a version
cannot ship without its entry.

## Version 62 — 2026-11-21 (#499)

A session whose start found orders of another owner on its own instrument, on an account declared
exclusive, starts and holds its entries: it manages and closes its position and opens nothing new
until those orders are gone or an operator lifts the hold.

- [Order history](/api/v1/docs/order-history): **`rejection_reason: entries_held`** (new value) — a
  new entry refused while the session held its entries. Closing the position and cancelling its
  orders are never refused for it.
- [Order history](/api/v1/docs/order-history) and [order events](/api/v1/docs/order-events):
  **`end_reason: entries_held`** (new value), with `initiator: framework` — a resting entry
  cancelled when the hold began, because the venue filling it would have been new risk.
- [Deployments](/api/v1/docs/deployments): **`exclusivity_violated_at`** on a session row (new) —
  when its start found the declared exclusivity false, its start in ISO-8601 UTC: it held its
  entries, or an operator had accepted those orders at an earlier start. Its money figures stand,
  but their denominator is not provably the bot's own; null where the start found nothing.
- [Sweeps](/api/v1/docs/sweeps): **`exclusivity_violated_at`** on a combination (new) — null on
  every backtest, which meets no venue at its start.

**A row recorded before this contract** carries `logic_version` 22 or older and null in
`exclusivity_violated_at`: not recorded, never a clean start.

Two values and two fields added; nothing changed or removed.

## Version 61 — 2026-11-21 (#650)

A request that meets a store index another process is writing waits for it, and says so when it
cannot.

- [Errors](/api/v1/docs/errors): **`503 store_index_busy`** (new), carrying `Retry-After`. Every
  route that reads a store index — the run list, deployments, sweeps, the configuration directory
  and the others — waits a few seconds for another process that is writing it, and answers with
  this code when the index does not come free in that time. Such a request used to end in a bare
  `500`, or, while the index was being rebuilt by two requests at once, in one for the slower of
  them; it now waits for the first one and answers `200`.

One error code added; nothing changed or removed.

## Version 60 — 2026-11-20 (#647)

Coverage and the scenario details say whose data was there and whose was read.

- [Market data](/api/v1/docs/market-data): **`origin_classes`** on a symbol's coverage (new) —
  whose data its bars were rendered from, over every timeframe, comma-joined like every other
  `origin_classes`: `external` for a venue's published history, `production` and `development` for
  our own collection; empty where the bars were
  rendered before the origin was stamped.
- [Market data](/api/v1/docs/market-data): **`collected_since`** on a symbol's coverage (new) —
  where our own collection takes over from a venue's published history, ISO-8601 UTC; `null` where
  the bars hold no external history before collected data.
- [Scenario details](/api/v1/docs/scenario-details): **`origin_classes`** on each entry of
  `data_brokers` (new) — whose data that broker's scenarios read, comma-joined, as each scenario
  row already says it.

Three fields added; nothing changed or removed.

## Version 59 — 2026-11-19 (#647)

What a run read says more, and one count says what its name says: a file without a stamp.

- [Sweeps](/api/v1/docs/sweeps): **`unstamped_input_files`** on a combination counts the files whose
  origin no stamp attests — evidence grade other than `stamped` — whatever their class. A venue's
  published history carries the stamp of the converter that brought it in, so it no longer counts.
  A changed meaning: a row with `logic_version` 21 or older counted the files that were not
  production data stamped by their producer, so every external file counted there.
- [Sweeps](/api/v1/docs/sweeps): **`server_clock_rules`** (new) — the server clock rules the
  combination's tick files' times were converted with, comma-joined, such as `UTC+0h`; empty where no
  file stated one.
- [Sweeps](/api/v1/docs/sweeps): **`external_warmup_timeframes`** (new) — the bar timeframes whose
  warmup reached back before our own collection, into a venue's published history; empty where none
  did.
- [Warnings and errors](/api/v1/docs/warnings-errors): a mock session reports what its replay read,
  as a backtest does — `data_origin_external` for a venue's published history,
  `data_origin_unstamped` for files no producer stamped, and what the files lack.

**A row recorded before this contract** carries `logic_version` 21 or older, the old meaning of
`unstamped_input_files`, and empty values in the two new fields.

Two fields added, one meaning changed; nothing removed.

## Version 58 — 2026-11-16 (#461)

An AutoTrader session runs on a timeline, as a backtest does since version 57: it waits for the next
thing that falls due, is woken when a tick or a venue's answer arrives, and runs each instant in the
backtest's phase order.

- [Order events](/api/v1/docs/order-events): **`ts_event`** (new) — the venue's own stamp of the
  step, where it reports one. Today that is a live order's fill in full, stamped with the time the
  venue closed the order; null in a backtest, a mock session and a dry run, and on every other event.
- A changed meaning for every time an AutoTrader session records — `event_time` on the order events,
  a state frame's instant, a trade's entry and exit: it is when a tick or an answer reached the
  session, no longer the venue's stamp of the tick, so a live session's times move by the feed's
  delay. A backlog the session fell behind on is one slice, the algo computing once on its newest
  tick, and a venue's answer resolves when it arrives rather than at the next tick or heartbeat —
  except while a stopping session waits for its last orders, which reads them a heartbeat apart.
- [Worker and decision](/api/v1/docs/worker-decision): a session's `ticks_processed` no longer
  counts the ticks of a backlog behind its newest — they never reached the algorithm, as a
  backtest's clipped ticks do not.
- [Order events](/api/v1/docs/order-events) and [pending orders](/api/v1/docs/pending-orders): a
  live `in_flight_ms` still runs until the session reads the answer, and the session now reads it
  right after it arrives — later only by a pass still running then — no longer up to one heartbeat
  after it, except while a stopping session waits for its last orders.

**A session recorded before this contract** carries null in `ts_event`, its times are the venue's
stamp of each tick, and its `in_flight_ms` can run up to one heartbeat long; it can differ from a
session of the same data recorded now.

One field added, three meanings changed; nothing removed.

## Versions 57 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

- **Version 57** — 2026-11-16 (#460): A backtest runs on its timeline — `algo_passes` and `ghost_passes` on [scenario details](/api/v1/docs/scenario-details), the [worker and decision](/api/v1/docs/worker-decision) rows and [profiling](/api/v1/docs/profiling), with the per-pass time beside the per-tick one, several shares and budget figures taken per pass, the `instant_resolution` operation and the `data_arrival_time_offset` [validation check](/api/v1/docs/validation-checks); a backtest recorded before it carries null counts.
- **Version 56** — 2026-11-15 (#666): A list says what a reader opens a run or a sweep to find out — `total_units`, `failed_count` and `succeeded_count` on every row of [runs](/api/v1/docs/runs) and on a [sweep](/api/v1/docs/sweeps)'s combinations, `best_combinations` on the sweep list, `total_final_equity` and `total_initial_balance` on a combination; a sweep whose objective cannot rank its combinations is refused with `409 sweep_objective_unrankable`.
- **Version 55** — 2026-11-12 (#379): A worker draws its outputs on a run's chart as [indicator series](/api/v1/docs/indicator-series) — the open `GET /api/v1/drawing-types` schema route, an `indicator_series` row per drawing worker on the [recorded streams](/api/v1/docs/recorded-streams) timeline and the series in its window, whose `limit`, `matched`, `returned` and `next_from` now count over every stream; `stream_files` on [runs](/api/v1/docs/runs) names the series files.
- **Version 54** — 2026-11-12 (#497): `drawdown_started_at` on the [portfolio](/api/v1/docs/portfolio) is the instant the equity curve began on the run's own clock — the replayed time in a backtest — where it was the wall clock; empty when a session ended before its first pass.
- **Version 53** — 2026-11-09 (#557): `entry_tick_index` and `duration_ticks` on [trade history](/api/v1/docs/trade-history) are null on a trade whose position a session restored at boot, because no tick of the closing session is its entry; `duration_s` still answers.
- **Version 52** — 2026-11-09 (#557): Every figure a console printed is a field the API serves — `duration_ticks` and the per-trade analytics on [trade history](/api/v1/docs/trade-history), the failed counts on [order history](/api/v1/docs/order-history), `open_position_count` and the active pending counts, `total_costs` on the [aggregated portfolio](/api/v1/docs/aggregated-portfolio), `execution_rate_pct` on the [run summary](/api/v1/docs/run-summary), the worker and decision totals, the profiling figures, `keys` on the lists and the check `usable_funds_negative`; a figure folded over rows an older run does not keep is null there.
- **Version 51** — 2026-11-05 (#646): A deployment no longer reads twice in the list: the sessions without booked figures stand in no row of their own, each booked row of [deployments](/api/v1/docs/deployments) counts them as `sessions_without_figures` and `sessions_failed`, and the row with `currency: null` is served only for a deployment that has booked nothing.
- **Version 50** — 2026-11-04 (#646): A run that failed before it measured anything no longer reads like a session that traded and broke even — such a [deployments](/api/v1/docs/deployments) session serves no figure and no currency (null, not `0.0` and the empty string), every session in the detail carries `booking_state` (`booked`, `failed`, `not_booked_yet`), and a [sweep](/api/v1/docs/sweeps) combination's figures are nullable in the schema while a ranked one never carries null.
- **Version 49** — 2026-11-02 (#645): A deployment is listed from its first session's start — sessions without a sealed day stand in the list and the detail without figures, in a row of their own with `currency: null`; `net_pnl` and `currency` nullable; `/deployments/{deployment_id}` and its booking periods answer 200 for such a deployment, and `deployment_not_found` means no run and no ledger row names it.
- **Version 48** — 2026-11-01: The installation answering names itself on `/health` — `host_id`, the key, the same value as `origin_host` on activities and runs, and `host_name`, for people to read and no key.
- **Version 47** — 2026-10-30 (#559): A deployment notices when its sessions ran different code — `changed` also true when only the code moved, `configuration_changed` beside it, `code_changes` on the session rows, `code_summary` on the list rows and the detail, `commit_subject` and `recent_commits` on the run header's repositories, `code_stand` on a sweep's combinations.
- **Version 46** — 2026-10-30 (#625): A session holds no stop or target for a position any more — `protective_level_enforcement` and `take_profit_enforcement` removed from the open-position rows, `protection_released` and `close_withheld` no longer produced, `409 artifact_unreadable` for a stored artifact that no longer matches its model, `expectancy` null where no trade has an R, and a sweep ranked by `expectancy` counts such runs in `unranked`.
- **Version 45** — 2026-10-30 (#625): A venue's API decides which levels a run can use — `exit_level` on every [trade history](/api/v1/docs/trade-history) row, `features_beyond_venue` on the [run header](/api/v1/docs/run-header), `order_feature_not_declared` as a `rejection_reason`, four new validation checks, and an exit a strategy keeps itself fills in a backtest at the next price after its level.
- **Version 44** — 2026-10-27 (#631): What every live session knows about its venue is recorded — `GET /api/v1/reports/runs/{run_id}/venue-api-performance` (new), `reconciliation` on the [venue account](/api/v1/docs/venue-account), `fee_report` and `venue_fee_expected` on [broker](/api/v1/docs/broker), a fill's slippage on [order history](/api/v1/docs/order-history) and in `fill_slippage` on [execution statistics](/api/v1/docs/execution-stats), and a protective exit's `exit_slippage` measured against the level that fired.
- **Version 43** — 2026-10-27 (#580): A unit's recorded streams can be read over time: a timeline to draw the axis from, and a window to read them a page at a time — a finished run's and a running session's alike. Its document is [recorded streams](/api/v1/docs/recorded-streams).
- **Version 42** — 2026-10-27 (#476): A booking period counts the orders it handled, so a run's order counts can be read from its periods again — on a [sweep](/api/v1/docs/sweeps) combination, a changed meaning: every `orders_*` field and `sl_tp_triggered` is the sum of its periods' own counts, null before.
- **Version 41** — 2026-10-26 (#626): What a carry-over may carry across a change: a rehearsal's readings never reach real money, and a start that continues an open position under a changed configuration says so.
- **Version 40** — 2026-10-25 (#476): A trading day ends at its boundary, every trading day has its row (`tick_count`, new), and a session's days are booked while it runs.
- **Version 39** — 2026-10-25 (#547): A backtest names the configuration it ran with — `rendered_config_id` on a run's [header](/api/v1/docs/run-header) is filled for a backtest too, naming its rendered scenario set: one section per scenario with every parameter default filled in, beside `config_id`, the file the run was given; empty on an older backtest and where the configuration could not be rendered or frozen.
- **Version 38** — 2026-10-21 (#611): A running session can be asked how it stands — `GET /api/v1/reports/runs/{run_id}/state-frames/latest`, one row per unit with the latest frame, its age, its cadence (`cadence_timeframe`, `cadence_s`) and `recording`; the codes `state_frames_not_recorded` and `state_frame_not_written_yet`; and `stream_files` and `recording` on the run list. Its document is [state frames](/api/v1/docs/state-frames).
- **Version 37** — 2026-10-20 (#598): A session trading real money starts without a carry-over only when its start is declared the bot's first — `origin.first_start` on the [run header](/api/v1/docs/run-header), `true` for such a start and `false` on every other run, a backtest and a run recorded before this version.
- **Version 36** — 2026-10-19 (#547): A backtest keeps the broker configuration it traded with — `broker_config_id` on every row of [broker](/api/v1/docs/broker) is set on a simulation row too, and `config_hash` leaves out when a value was fetched, so on a dynamic broker a hash recorded before this version differs from a later one over the same values.
- **Version 35** — 2026-10-19 (#603): What runs on the machine is served (`GET /api/v1/activities`, keyed by `activity_id`, with each activity's memory and the machine's pressure verdict), the machine routes and the broker list need a grant (`machine`, `brokers`), every symbol row carries `tick_size`, `from` and `to` on the bars and ATR routes accept an ISO-8601 instant only (`400 invalid_timestamp`), every booking period carries `account_max_drawdown`, `account_max_dd_pct`, `unrealized_pnl` and `open_position_count`, and `activity_registry_unreadable` (500) is new.
- **Version 34** — 2026-10-18 (#606): A price the venue quoted or traded is served as the decimal the venue wrote — a tick's `bid`, `ask` and `last`, an `order_driven` bar's prices and a fill at a recorded quote read `0.87957` where they read `0.8795700073242188`, `real_volume` keeps every decimal, and the new document [prices](/api/v1/docs/prices) says how to round a computed price by `tick_size`.
- **Version 33** — 2026-10-16 (#602): A backtest over data recorded without a quote pays a spread, reported beside the measured one — `modelled_spread_cost` on every trade row and booking period, `total_modelled_spread_cost` on the portfolio and on the scenario totals, `modelled_spread` on the scenario details; the P&L of such a backtest is lower than before.
- **Version 32** — 2026-10-15 (#599): A gap says whose silence it is, and a unit's trade totals carry the spread it paid — `origin_class` and `origin_class_after` on every gap, `gaps_by_year` beside them, `key` replaced by `keys`, the `origin_class` and `since` parameters, `reason` without its guessed causes, and `total_spread_cost` on every trade-history `scenario_totals` row and the portfolio, null where a unit read data without a quote.
- **Version 31** — 2026-10-14 (#595): The machine this server runs on can be asked how full it is — `GET /api/v1/machine-resources`, new, with the memory limit that binds and where it was read (`limit_source`), the processors and how busy they were over `window_s`, and `pressure_level` with its `pressure_reasons`; a route added, no field changed its shape.
- **Version 30** — 2026-10-14 (#561): A venue's published history can enter the archive, and what an input file lacks is named — `spreadless_tick_data` gives way to `data_no_quote`, with new checks `data_no_arrival_time`, `data_arrival_time_reconstructed`, `data_missing_trades`, `data_origin_external` and `data_synthetic`, and `origin_classes` may hold `external`, never admissible for a parity measurement.
- **Version 29** — 2026-10-13 (#588): A live session that refused at its first tick is told apart from one that crashed — `refused_at_first_tick` on the `warnings-errors` outcome, `true` when the warmup history could not be completed at the first tick, `false` on every other run, on a backtest and on a run recorded before.
- **Version 28** — 2026-10-11 (#508): A run says how many of its fills were booked at the venue's own fee and how many at our estimate — `fill_fees_by_source` on every portfolio unit row, per-currency row and aggregated `headline` (entries `venue` / `estimate` with `fill_count` and `fill_fees`; empty where nothing executed), and `fee_source` on every order-history row (null where nothing executed).
- **Version 27** — 2026-10-11 (#508): A live fill's fee is the venue's own charge wherever the venue reports it, and every executed order event says where its fee came from — `fee_source` (`venue` or `estimate`; null on an event that executed nothing, on a fill nothing could be booked for whose venue reported no fee, and on every event recorded before it); the same fee fields from another source, so a live run before and one after can differ in fees for the same orders.
- **Version 26** — 2026-10-09 (#582): A run's header is served whole — `GET /api/v1/reports/runs/{run_id}/header`, with `run_not_found`, `run_header_missing` and `artifact_unreadable` — and the run list says who started each run: `origin_channel`, `origin_client`, `origin_principal` and `origin_host`; `fixture_superseded` is `true` also for a run of an entry no longer declared.
- **Version 25** — 2026-10-08 (#576): The runs a consumer pins became entries of a fixture catalog — `fixture_superseded` on every run of the run list says whether a run is its entry's current production (`false`), an older or failed one (`true`), or no catalog production at all (`null`).
- **Version 24** — 2026-10-08 (#576): Every run says what it is for and under which contract its
  reports were written — `run_purpose` (`regular`, `fixture`, `certificate`) and `report_contract`
  on the run list; `run_purpose` and `config_description` on the configuration directory.
- **Version 23** — 2026-10-07 (#362): Every step of an order's life is recorded —
  `order-events` and `venue-account` were added — and the order counts, the order history and
  the pending-order counters are counted from that record, one status per way an order can end:
  `orders_sent` was replaced, and `orders_executed` and `orders_rejected` changed meaning.
- **Version 22** — 2026-10-06 (#524, #568): The documentation is served by the API — `GET /api/v1/docs`, `GET /api/v1/docs/{name}` (markdown) and `GET /api/v1/docs/search` over passages — behind a new `docs` grant surface (tokens re-minted, server restarted); three error codes (`document_not_found`, `empty_query`, `query_too_long`); every route answers with `Link: …; rel="describedby"`, and `Link` and the `X-Bar-*` headers are published cross-origin.
- **Version 21** — 2026-10-02 (#555, viewer#21): The worker-decision report says "not counted" instead of zero — `worker_decision_tracked` per unit, null counters and timings on an untracked one, and null `compute_ratio_pct` / `ticks_idle` where nothing was measured.
- **Version 20** — 2026-10-02 (viewer#21, #557): A refused order says what was refused — its side,
  symbol, direction and size — `execution_time` became `event_time`, and the order history says
  "absent" as null and its closed values as enums.
- **Version 19** — 2026-10-01 (#547, viewer#21): A session records the broker configuration it
  traded with (`broker_config_id` on `broker`), and the pending-orders unit list declares its key.
- **Version 18** — 2026-09-29 (viewer#21, #557): The figures an aggregate inventory found wrong were
  corrected: `total_fees` is the closed trades' fees beside a new `fees_charged`, a trade that
  realised nothing is neither a winner nor a loser, streaks and the drawdown trio are one account's,
  and a spot trade's excursion is tracked between its entry and its close.
- **Version 17** — 2026-09-29 (viewer#21): Every figure says which ACCOUNT it is about: a
  one-account figure is null where several are folded together, and the sum carries its own name.
- **Version 16** — 2026-09-29 (viewer#21): The directory names its brokers the way the run reports
  do (`data_broker_types`), so one word means one thing across the API.
- **Version 15** — 2026-09-29 (viewer#21): The run list says what each run DID — `results` per
  account currency, `run_outcome` and the warning and error counts — in the one request that lists
  it.
- **Version 14** — 2026-09-29 (viewer#21): `data_source` split into `data_broker_type` and
  `data_sentiment_type`, each meaning its own thing; every `GET` route began answering `HEAD` as
  well.
- **Version 13** — 2026-09-29 (viewer#21): `execution_time_ms` became milliseconds, as its name
  says; the CORE test components and the production profiles were renamed.
- **Version 12** — 2026-09-28 (viewer#21): The vocabulary contract: an AutoTrader session's `group`
  is `autotrader`, every run carries `ticks_from`, `orders_to` and `data_windows`, and every
  `segment_*` field became `period_*`.
- **Version 11** — 2026-09-27 (#554): A configuration file whose name is also taken by one of the
  other kind is `unreadable`, with that as its reason.
- **Version 10** — 2026-09-27 (viewer#21): `GET /api/v1/validation-checks` was added, and an empty
  `key` became a declaration in its own right rather than an omission.
- **Version 9** — 2026-09-27 (viewer#21): A response serving several lists declares `keys`, one per
  list; several sections gained a key, and figures nothing counted became null instead of zero.
- **Version 8** — 2026-09-27 (viewer#21): `/health` also answers `started_at` and `uptime_s`, so a
  restart is something a consumer sees rather than infers.
- **Version 7** — 2026-09-25 (#554): `GET /api/v1/directory` was added — every configuration file
  that can start a run — on a new `directory` grant surface.
- **Version 6** — 2026-09-25 (viewer#21): Every error code names its CAUSE; the eight bare
  `not_found` answers on the bars and broker routes became six named ones.
- **Version 5** — 2026-09-25 (viewer#21): A 404 on a report section names its cause — four of
  them — where it used to read `run_not_found` for every one.
- **Version 4** — 2026-09-24 (#551): `GET /api/v1/caller` was added, and `git_dirty` changed MEANING
  to cover every repository a run's code came from, reading true where the state could not be
  determined.
- **Version 3** — 2026-09-23 (#538, #546): `/reports/runs/{run_id}/config` serves the configuration
  a run was commissioned with, resolved from the run-config store; the per-run copy was retired.
- **Version 2** — 2026-09-23 (#539, #537): The deployment routes and a run's booking periods were
  added, every list began declaring `key`, and `reconciles` became three-state.
- **Version 1**: Before the number existed. Responses carry no `X-Api-Contract` header, so a fixture
  without one predates everything above.
