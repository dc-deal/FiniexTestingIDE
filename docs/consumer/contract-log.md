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

## Version 50 — 2026-11-04 (#646)

A run that failed before it measured anything no longer reads like a session that traded and broke
even.

- [Deployments](/api/v1/docs/deployments), a changed meaning: such a session serves **no figure** —
  `net_pnl`, `max_drawdown` and `max_drawdown_pct` null where they read `0.0` — and no currency, null
  where it read the empty string. It stands with the sessions that have booked nothing yet, in the
  list row whose `currency` is null.
- Deployments, the detail: every session carries **`booking_state`** — `booked`, `failed` (it failed
  before it measured anything) or `not_booked_yet`. The sessions without booked figures follow the
  booked ones as one series, oldest first and numbered from 1, and mark no gap and no change.
- [Sweeps](/api/v1/docs/sweeps): `net_pnl`, `win_rate`, `total_fees`, `total_trades`,
  `winning_trades`, `losing_trades` and `r_trade_count` of a combination are nullable in the schema.
  A ranked combination never carries null there: a run that failed is left out of the ranking, as
  before.

Nothing removed or renamed; one field added — `booking_state`.

## Version 49 — 2026-11-02 (#645)

A deployment is listed from its first session's start, not from its first sealed trading day. Until
now a new deployment stood in no list for up to a day while its session ran.

- [Deployments](/api/v1/docs/deployments), a changed meaning: which sessions a deployment holds now
  comes from the runs that name it, and what they booked from the ledger as before. A session that
  has booked no trading day yet, because it runs before its first seal or ended before it, stands in
  the list and in the detail **without figures**.
- Deployments, the list: such sessions have no account currency yet, so they form a row of their own
  with **`currency: null`**, after the deployment's other rows. A deployment whose earlier sessions
  booked days shows that row beside its booked ones. Its session moves into its currency's row at its
  first seal. The key stays `["deployment_id", "currency"]`.
- Deployments, the list row and the session row: **`net_pnl` and `currency` are nullable**, null on
  such a session and never a zero. Drawdowns were nullable before.
- `/deployments/{deployment_id}`: answers 200 for such a deployment instead of `404`. Every run
  `unfinished` counts is now in `sessions`, either without an `ended` or without figures.
- [Booking periods](/api/v1/docs/booking-periods), `/deployments/{deployment_id}/booking-periods`:
  answers 200 with an empty list for such a deployment. `sessions_without_periods` also counts its
  sessions that have not sealed a day yet.
- [Errors](/api/v1/docs/errors): `deployment_not_found` now means that no session in the run index
  and no ledger row names the identity.

## Version 48 — 2026-11-01

A consumer can tell which installation answers, now that this API exists on more than one system.

- [Server](/api/v1/docs/server), `/health`: `host_id`, the identity of the installation answering, the
  same value as `origin_host` on [activities](/api/v1/docs/activities) and [runs](/api/v1/docs/runs). It
  stays when an address changes, so key a system by it. Null where the server cannot read it; `/health`
  keeps answering.
- `/health`: `host_name`, a name for people to read, from the installation's configuration; null where
  none is set. Two installations may carry the same one, so it is no key.

Two fields added on an open route; nothing removed or renamed.

## Version 47 — 2026-10-30 (#559)

A deployment notices when its sessions ran different code, in a strategy's repository as much as in
this application's.

- [Deployments](/api/v1/docs/deployments), a changed meaning: `changed` on a list row is also true
  when only the code moved. **`changed: true` with `advisory: null` is a new state:** the advisory
  still speaks of the profile alone and stays null for a code change. `configuration_changed`, new
  on the list row, says what `changed` said so far.
- Deployments, the session rows: `code_changes`, one entry per repository whose code differs from the
  previous session's: `repository_label`, `old_commit`, `new_commit`, `commits_between`,
  `change_direction` (`forward`, `back`, `unknown`, `uncommitted`, `added`, `removed`) and `subject`.
  Empty when nothing moved, and on a session recorded before this contract. `code_changes_key` on
  the detail is empty: an entry's identity is its place in the list, and `repository_label` can repeat.
- Deployments, the list rows and the detail: `code_summary`, with `code_stands`, `sessions`,
  `last_change_at` and `age_days`; null under two code stands.
- [Run header](/api/v1/docs/run-header): a repository in `code_identity` carries `commit_subject` and
  `recent_commits`, filled for a session of a deployment and null on every other run.
- [Sweeps](/api/v1/docs/sweeps): a combination carries `code_stand`, null on every one, because only
  a session records it.

Nothing removed or renamed; one meaning changed: `changed`.

## Version 46 — 2026-10-30 (#625)

A session holds no stop or target for a position any more, so the fields and values that said who held
one are gone.

- [Portfolio](/api/v1/docs/portfolio) and the state frames: `protective_level_enforcement` and
  `take_profit_enforcement` are removed from the open-position rows. `stop_loss` and `take_profit`
  stay: a backtest's position may carry one.
- [Order history](/api/v1/docs/order-history) and [order events](/api/v1/docs/order-events): the
  `end_reason` `protection_released` is no longer produced. A run recorded before this contract may
  still hold it, and it stays served. The `rejection_reason` `close_withheld` is removed; no recorded
  run holds it.
- Every report route answers `409 artifact_unreadable` for a stored artifact that no longer matches
  its model. Some answered 500 before. See [errors](/api/v1/docs/errors).
- A close's `order_id` is its position's on every row. A rebuilt stop's own id, `protect_` and the
  position's id, is no longer produced.
- `expectancy` can be null, on [run summaries](/api/v1/docs/run-summary), [trade analytics](/api/v1/docs/trade-history),
  booking periods, ledger rows and robustness windows, where no trade has an R. It used to read `0.0`.
  A run recorded before this contract keeps its `0.0` beside `r_trade_count` 0.
- [Sweeps](/api/v1/docs/sweeps): a sweep ranked by `expectancy` leaves such a run out and counts it in
  `unranked`, and a sweep that recorded no objective ranks by `net_pnl`. The sweep list carries
  `features_beyond_venue`. It is not on the run list: the run header holds it.

## Versions 45 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

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
