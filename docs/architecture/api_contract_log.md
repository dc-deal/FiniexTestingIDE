# API Contract Log

A consumer of this API records the contract number its fixtures were captured under and asserts
it at start-up, so a stale mock fails locally instead of silently answering with an old shape. The
server tells them only what moved into the CURRENT number: `GET /api/v1/contract` returns
`changes` for this version and nothing older. A consumer whose fixtures are several versions
behind needs every step in between, and this log is where they are kept — newest first.

Not here: what each route serves today (the endpoint table in
[`api_server_architecture.md`](api_server_architecture.md#endpoints)), and why the contract is a
number rather than a deprecation promise (the module docstring of `python/api/api_contract.py`).

## How a change is recorded

In the same change that moves a route or a response model, or changes what a field means:

1. Raise `API_CONTRACT_VERSION` in `python/api/api_contract.py` by one.
2. Replace `CHANGES` with the new version's lines — one line per change, for someone who cannot
   read this repository. The old lines are not kept in code: this log is the history.
3. Add the new version at the top of this log, with its date and its issue.

A change of MEANING counts as much as a change of shape: a field that keeps its type and starts
answering a different question breaks a consumer just as silently, and more so, because nothing
fails to parse.

The server serves the current version's lines and this log keeps every version. A test holds the
newest heading here to `API_CONTRACT_VERSION`, so step 3 cannot be skipped unnoticed.

## Version 20 — 2026-10-02 (viewer#21, #557)

A refused order now says what was refused, and the order history says "absent" as null.

- `GET /api/v1/reports/runs/{run_id}/order-history`: a rejected row states its side (`action`), its
  `symbol`, its `direction` and its `requested_lots`, and when it was refused. Every rejection used
  to leave them empty, so `?symbol=` dropped all of them without a word — 12 rows with 2 rejections
  unfiltered, 10 rows and none with the filter. A rejection can be on either side: a partial close
  below the symbol's minimum is refused on the close side. Runs recorded before this contract carry
  the symbol and the side, taken from their own records; their direction, size and time stay null,
  because the records never had them.
- `order-history`: `execution_time` is renamed `event_time`. On these rows it is a point in time —
  when the row's event happened, on the run's clock: the fill, the refusal, the expiry; null on a
  `pending` row. Every other `execution_time` in the API is how long something ran.
- `order-history`: a value that does not exist is null, never an empty string or `0.0` —
  `position_id`, `direction`, `action`, `requested_lots`, `executed_lots`, `executed_price`,
  `event_time`, `rejection_reason`, `rejection_message`. A zero price reads as a price. `direction`,
  `action`, `status` and `rejection_reason` are enums, so the schema lists their values; the values
  themselves are unchanged.
- `order-history`: an expired row states its direction and its requested lots, and the expiry of a
  close-side order — a protective stop — says `close`; it said `open` for every expiry.
- `GET /api/v1/reports/runs/{run_id}/pending-orders`: an active order's `order_type` (`limit`,
  `stop`, `stop_limit`) and `direction` are enums. The two active-order lists hold the orders still
  resting when the unit's data ended; in a backtest the same orders are recorded `expired` in
  `order-history` in that same step, so they are not open. A STOP or STOP_LIMIT whose trigger was not
  reached sits in the stop list; a STOP_LIMIT whose stop triggered becomes a limit order.

Every stored run was carried over to this shape, so an old run answers like a new one.

## Version 19 — 2026-10-01 (#547, viewer#21)

A session now records the broker configuration it traded with, and one more list says what keys
its rows.

- `GET /api/v1/reports/runs/{run_id}/broker`: every unit carries `broker_config_id` beside
  `config_hash`. It is the run-config store id of the broker configuration an AutoTrader session
  froze at its start — the symbol specifications from the venue's cache, the seed's fee structure
  and the fee tier the venue reported — which `config_hash` only digests in eight characters. A
  later backtest of the same window reads that frozen copy instead of whatever the cache holds by
  then. Empty for a simulation unit, which reads the archive's broker files when it runs, and on a
  session recorded before the freeze existed.
- `GET /api/v1/reports/runs/{run_id}/pending-orders` declares `key: ["name"]` for `units` — the
  unit name, which a scenario set cannot repeat (it is refused at validation) and an AutoTrader
  session has once. The nested `active_limit_orders` / `active_stop_orders` lists and
  `order-history` declare no key yet; both come with #557. Old runs serve the key too: the
  default fills in on read, nothing to re-fetch.

Both are additions with a default; no existing field changed.

## Version 18 — 2026-09-29 (viewer#21, #557)

The numbers the aggregate inventory for #557 found wrong, each corrected before that refactor
starts — so the refactor can be held to changing no number.

- `GET /api/v1/reports/runs/{run_id}/portfolio` and `…/run-summary`: `total_fees` is the fees of
  the CLOSED trades — the population `trade-history`, `booking-periods` and the ledger sum. What the
  run charged, open positions included, is the new `fees_charged`. One unit read 169.30 in one file
  and 113.20 in the next; it now reads `total_fees 113.20 · fees_charged 169.30` in both.
- `portfolio`, `run-summary` and the ledger rows: a trade that realised exactly nothing is neither
  a winner nor a loser. `losing_trades` counted it; `win_rate` is unchanged, and `avg_loss` no
  longer divides by a trade that lost nothing. Trades +10, 0, −5 are 1 winner and 1 loser.
- `portfolio.aggregates` and `run-summary`: when no account declined, the drawdown trio names the
  first account and its peak. It answered `max_equity 0.0` and no unit.
- `run-summary` and the `trade-history` analytics: a streak is the longest of ONE account. Over
  several scenarios their trades were interleaved by time, so A's win, B's win and A's win read as a
  run of three that no account had.
- `GET /api/v1/reports/runs/{run_id}/booking-periods`: `unit_totals[].opening_equity` is `null`
  when the unit's first period did not record an opening. It showed the second period's.
- `GET /api/v1/sweeps/{sweep_id}`: combinations are ranked within each account currency,
  currencies in order — `net_pnl` in EUR and in USD is not one scale. `orders_sent`,
  `orders_executed`, `orders_rejected` and `sl_tp_triggered` are `null` on a row folded from booking
  periods, which carry no order counts; they read `0`.
- `GET /api/v1/deployments/{deployment_id}`: each currency is its own series — `index`, `gap_hours`
  and the change marks restart per currency instead of measuring against the other currency's last
  session.
- `GET /api/v1/reports/runs/{run_id}/aggregated-portfolio`: a spot row takes its base / quote split
  and its value estimate from the unit, which stamps them from the broker config. The symbol string
  was split three characters from the end, and the initial-value estimate dropped an initial base
  holding whenever the account ended without one.
- `GET /api/v1/reports/runs/{run_id}/trade-history`: a spot trade's excursion (`mae_*`, `mfe_*`) is
  tracked between its entry and its close. It was measured at those two instants alone, so a winner
  that dipped first read `mae_pnl 0` — 31 of 40 stored spot trades. Runs recorded before this
  contract keep their values: the ticks would have to be replayed.

The stored runs were corrected wherever their own records answer it exactly — every unit's trade
rows were complete, and every rebuilt aggregate reproduced its stored net P&L.

## Version 17 — 2026-09-29 (viewer#21)

Every figure says which ACCOUNT it is about. A backtest of several scenarios is several independent
accounts — one balance each — while an AutoTrader session is one, and three routes answered "final
equity" three ways for the same run: a sum no account ever held, the last row's own account, and
whichever account closed last.

- `GET /api/v1/reports/runs/{run_id}/run-summary` and `…/portfolio` (`aggregates[]`): `final_equity`
  is ONE account's closing equity and is `null` when the currency spans several; the sum is
  `total_final_equity`, beside `total_initial_balance` and `unit_count`. `account_max_drawdown_unit`
  names the account the drawdown, `max_equity` and `account_max_dd_pct` describe. `unrealized_pnl`
  and `open_position_count` stay sums.
- `GET /api/v1/reports/runs/{run_id}/aggregated-portfolio`: `max_equity` / `max_equity_scenario` are
  `highest_equity` / `highest_equity_scenario` — the highest peak of ANY account. The peak the
  drawdown fell from is `headline.max_equity`; the two shared one name. `recovery_factor` is `null`
  over several accounts, since it divided their summed P&L by one account's decline.
- `GET /api/v1/reports/runs/{run_id}/booking-periods`: every period carries `opening_equity` — the
  previous period's close, or a unit's first observed value, never `final_equity − net_pnl`, which
  would drop what was still open — and its costs split: `commission_cost` + `swap_cost` is
  `total_fees`, `spread_cost` is measured and stands beside it. `unit_totals` folds each unit's
  periods into its total by the ledger's own reductions (rates rebuilt from their components, the
  drawdown from the period that owns it, a streak not at all). `keys` replaces `key`:
  `{"periods": ["unit_name", "period_no"], "unit_totals": ["unit_name"]}`. `final_equity` is
  `total_final_equity`, the sum over `unit_totals` — it was the last row's own figure.
- `GET /api/v1/deployments/{deployment_id}/booking-periods`: the rows carry the same period fields.
- `GET /api/v1/reports/runs/{run_id}/trade-history`: every execution carries `shared_by` — how many
  trade rows of its UNIT carry that fill. Per unit, because two scenarios on one symbol mint the
  same synthetic ids: counted across a whole run, one id of three scenarios reads as seven siblings.
  A partial close's `commission_cost` includes its exit fee, so `commission_cost + swap_cost ==
  total_fees` on every row; it fell short by exactly that fee at a maker/taker venue.
- `GET /api/v1/reports/runs` and `GET /api/v1/directory` (`last_run_figures`): every run carries
  `tick_timespan_seconds` — the market time its units PROCESSED, covered together so a stretch two
  scenarios share counts once. `run-summary` carries it too, beside `tick_timespan_total_seconds`,
  the sum. Measured on `20260929_075109_9c62dd40`: 464 h covered, 928 h summed.
- `GET /api/v1/sweeps/{sweep_id}`: `final_equity`, `unrealized_pnl` and `open_position_count` are
  `null` on a row that folds several accounts.

The stored runs were back-filled with every value that is exactly derivable from what they already
held; the rest answers `null`.

## Version 16 — 2026-09-29 (viewer#21)

The directory names its brokers the way the run reports do, so one word means one thing across the
API.

- `GET /api/v1/directory` and `…/directory/{file}`: a row's `broker_types` is `data_broker_types` —
  the brokers whose archives its scenarios read. For an AutoTrader profile it follows
  `scenario_settings.data_broker_type` where one is declared, else the profile's `broker_type`: a
  mock session may replay another broker's archive than the one it trades against, and the old
  field named the trading broker.
- `GET /api/v1/directory/{file}`: a scenario's `broker_type` is `data_broker_type`, the name its
  run's `scenario-details` rows have carried since contract 14.
- `GET /api/v1/reports/runs/{run_id}/warnings-errors`: an artifact written before contract 15 now
  carries the three counts on `outcome` too — counted from its own rows, the values the run list
  already served for that run. The shape is unchanged; what changed is that a stored run no longer
  answers `null` here and a number there. `null` remains only where nothing recorded them.

## Version 15 — 2026-09-29 (viewer#21)

The run list says what each run DID, in the one request that lists it — so a consumer choosing which
run to open no longer has to open them one by one.

- `GET /api/v1/reports/runs`: every run carries `results`, one entry per account currency —
  `currency`, `net_pnl`, `total_trades` — folded from its booking periods by the ledger's own declared
  reductions and never summed across currencies; `results_key` is `["currency"]`. THREE states: null
  when the run-results ledger holds nothing for the run (still going, died before its close, or
  `reporting: none`), `[]` when it closed without figures, a list otherwise. Beside it `run_outcome`
  (`success` | `finished_with_errors` | `failed` | `crashed`), `error_count` (ERROR records in the error
  pot), `warning_count` (Tier-1 findings) and `log_warning_count` (Tier-2 WARNING records) — null where
  not recorded, never zero. The stored runs were back-filled from their own warnings-errors artifacts.
- `GET /api/v1/reports/runs/{run_id}/warnings-errors`: `outcome` carries the same three counts. They
  are counted once, the same way in both pipelines; the warning ROWS are not a count, because a
  backtest summarizes its whole Tier-2 pot in one row while an AutoTrader session writes one per entry.
  Null on an artifact written before this version. The outcome's `run_outcome` is typed as its four
  values: the JSON is unchanged, the schema now names what it may hold.
- `GET /api/v1/directory` and `…/directory/{file}`: every row carries `last_run_figures` — what the
  newest run started from that file did, exactly the figures the run list carries for it; null when
  the file never ran or the ledger holds nothing for its newest run.
- `GET /api/v1/sweeps/{sweep_id}`: `key` is `["run_id", "currency"]`. The ranking folds each run's
  booking periods into one row per run and currency before it sorts, and the per-period key declared
  until now did not describe the rows it served.

## Version 14 — 2026-09-29 (viewer#21)

`data_source` meant two things: in a report, the broker a unit read its ticks from; in a stress
configuration, whichever input of a scenario an outage hits, ticks or signals. Each meaning now has
its own name, and the report's name is the one the scenario configuration has always used.

- `GET /api/v1/reports/runs/{run_id}/portfolio` and `…/scenario-details`: a unit's `data_source` is
  `data_broker_type` — the broker whose tick archive the unit read, and the key the bar routes are
  addressed by. The portfolio row's `sentiment_source` is `data_sentiment_type`. The stored runs
  were migrated, so an older run serves the new names too.
- `GET /api/v1/reports/runs/{run_id}/scenario-details`: the per-broker roll-up `data_sources` is
  `data_brokers`, and its rows carry `data_broker_type` where they carried `broker_type`; `keys`
  reads `"data_brokers": ["data_broker_type"]`.
- A stale-data stress event names the input it makes stale in `stale_data_source` (was
  `data_source`), beside its `stale_start_date` and `stale_end_date`. The new key appears in every
  configuration served from now on; a run recorded before this version serves its configuration
  as it was run, with the old key.
- Every `GET` route answers `HEAD` as well: the same status and headers, including
  `X-Api-Contract`, and no body. It was refused with `405 Method Not Allowed`.

## Version 13 — 2026-09-29 (viewer#21)

- `GET /api/v1/reports/runs/{run_id}/scenario-details`: `execution_time_ms` is MILLISECONDS, as its
  name says. It carried seconds under that name: 1.98 for 13,584 ticks was two seconds, not two
  milliseconds. It is now measured on the monotonic clock, from the scenario subprocess's start to
  its result, and the stored runs were migrated, so an older run serves milliseconds as well.
- The CORE test components are named `CORE/test_probes/<name>` — `deterministic_probe`,
  `event_probe`, `margin_stress_probe`, `multi_position_probe`, `outage_probe`,
  `sample_probe_worker` — where they were `CORE/backtesting/backtesting_<name>`: they run in mock
  sessions as well as in backtests, so "backtesting" was the wrong word. Their worker instance is
  `probe_worker` (was `backtesting_worker`). The new strings appear wherever a configuration is
  served — a run's configuration, a directory row, a ledger row — and the stored runs carry them
  too.
- `GET /api/v1/directory`: the four production AutoTrader profiles are `<symbol>_production.json`
  (were `<symbol>_live.json`); their `profile_name` changed with the file name.

## Version 12 — 2026-09-28 (viewer#21)

The vocabulary contract: the words the API serves follow the project glossary
([`docs/glossary.md`](../glossary.md)), and a run says which kind of run it is.

- `GET /api/v1/reports/runs`: an AutoTrader session's `group` is `autotrader` — it read `live`,
  which also named a real venue and real money elsewhere. Every AutoTrader session carries it,
  mock, dry run or real orders alike, and `live` no longer occurs in any `run_type` the API serves.
- `GET /api/v1/reports/runs`: every run carries `ticks_from` (`archive` | `venue`) and `orders_to`
  (`simulated` | `venue`), recorded at the run's start from its RESOLVED configuration — never read
  back from a profile file that may have changed since. With `group` they separate the kinds:

  | Kind | `group` | `ticks_from` | `orders_to` |
  |---|---|---|---|
  | Backtest | `simulation` | `archive` | `simulated` |
  | Mock session | `autotrader` | `archive` | `simulated` |
  | Dry run | `autotrader` | `venue` | `simulated` |
  | Real-money session | `autotrader` | `venue` | `venue` |

- `GET /api/v1/reports/runs`: `data_windows` — the market window each unit was DECLARED to cover,
  one per unit (`unit_name`, `start_date`, `end_date`; `end_date` null means open: a tick-limited
  scenario, or a venue session, whose window is recorded at its start and therefore stays open on
  the record even after it ended — when it ended is the run's completion, not a window field).
  Deliberately no single span over all units: it
  would cover the gaps between scenarios. What a scenario actually processed stays on
  `scenario-details`. All three fields are null on a run recorded before this version — unknown,
  never a guess.
- `GET /api/v1/sweeps/{sweep_id}`: a combination row's `config_snapshot` — the full strategy
  configuration as JSON — is `strategy_config_json`. The run index's `config_snapshot`, a FILE NAME,
  keeps its name.
- `GET /api/v1/directory`: the AutoTrader test profiles moved from the folder `backtesting` to
  `mock`; the one live-adapter probe among them moved to `observation`.
- `GET /api/v1/reports/runs/{run_id}/config`: a configuration recorded from this version on names a
  scenario `scenario_name` and a profile `profile_name`. A run recorded before keeps the snapshot it
  recorded.
- Booking periods: every `segment_*` field is `period_*` — `period_no`, `period_opened_at`,
  `period_closed_at`, `period_close_reason`, `period_max_equity`, `period_min_equity`,
  `period_max_drawdown` — on `GET /api/v1/reports/runs/{run_id}/booking-periods`,
  `GET /api/v1/deployments/{deployment_id}/booking-periods` and a sweep's combination rows, and
  every declared `key` names `period_no` (`["unit_name", "period_no"]` on a run's periods). The
  stored runs and ledger rows were migrated, so a run recorded before this version serves the new
  names as well — the one rename here that reaches back.

## Version 11 — 2026-09-27 (#554)

- `GET /api/v1/directory`: `status: unreadable` also means the file's NAME is taken by a
  configuration of the other kind — a scenario set and an AutoTrader profile named alike — with
  that as its `reason`. The row keeps its `kind`, because the file itself parsed. Both pipelines
  refuse to start a run from such a name: a run records its configuration by file name alone, so
  the two could never be told apart afterwards. No such pair exists today.

## Version 10 — 2026-09-27 (viewer#21)

- `GET /api/v1/validation-checks`, OPEN like `/timeframes`: every validation check a finding can
  name, with its `check` id (the one served in `run-summary.units_absent[].checks` and
  `warnings-errors.warnings[].check`), a `title` and a one-sentence `description`. `key` is
  `["check"]`.
- `warnings-errors` declares `keys`: `errors` → `["name"]` (one row per unit), `warnings` → `[]`.
  An EMPTY key is a declaration: a warning has no identity beyond its position, because nothing
  folds two identical ones into one.
- An error's `detail` is written for the person reading the answer; a consumer may show it as it
  comes, and branches on `error`. `artifact_not_produced` was reworded: it no longer names a field
  of another response.

## Version 9 — 2026-09-27 (viewer#21)

- A response serving SEVERAL lists declares `keys`, one entry per list; a response with one list
  keeps `key`. New keys:
  - `scenario-details`: `units` → `["name"]`, `data_sources` → `["broker_type"]`;
  - `portfolio`: `units` → `["name"]`, `aggregates` → `["currency"]`;
  - `trade-history`: `trades` → `["scenario_name", "position_id", "exit_tick_index"]` (a partial
    close books several records of one position, and two scenarios of one symbol count from the
    same position number), `analytics` → `["currency"]`, `scenario_totals` →
    `["scenario_name", "currency"]`;
  - `run-summary`: `currencies` → `["currency"]`, `units_absent` → `["name"]`;
  - `broker`: `key` → `["broker_type"]`.

  The per-unit lists share `name`, and that is a JOIN, meant as one: the roster in
  `scenario-details` knows every scenario, `portfolio.units` the ones that produced, and
  `run-summary.units_absent` the ones that did not.
- `run-summary`: `units_declared`, `units_disabled` and `units_absent` are `null` on a run
  recorded before contract 6 — NOT STATED — where they read 0 before, which the equation then
  disproved. The equation holds wherever they are stated.
- `run-summary`: every `units_absent` row carries `reason_code` — the cause for a program, in the
  vocabulary of `scenario-details`' `error_type`: `ValidationError`, an exception's class (an
  AutoTrader session aborted at startup included), or `NoResults` — and `checks`, the stable ids of the
  checks that refused it (`warmup_quality`, `tick_stretch_gap`, …).
- `GET /api/v1/directory`: `key` is `["file"]`. A file name is one entry across every root and both
  kinds, resolved by precedence; it was `["kind", "file"]`.
- `scenario-details`: `buy_signals`, `sell_signals`, `flat_signals` and `trades_requested` are
  `null` when nothing counted them — the decision tracker is off by default in the simulation
  (`performance_tracking.worker_decision_tracking`) — where they read 0 before on every row, a
  figure that was never measured. `worker_count` is the number of workers the scenario DECLARES,
  refused scenarios included; it read 0 whenever the tracker was off. Artifacts written before
  this version keep their stored zeros.

## Version 8 — 2026-09-27 (viewer#21)

- `GET /api/v1/health` also answers `started_at` — when this server process started serving,
  ISO-8601 UTC, new on every restart — and `uptime_s`, the seconds since then, measured by the
  server on its monotonic clock so a consumer needs no clock of its own. A restart is something a
  consumer sees rather than infers. The route stays open and carries nothing a stranger could use:
  which code the server runs is not on it.

## Version 7 — 2026-09-25 (#554)

- `GET /api/v1/directory` lists every configuration file that can start a run — scenario sets and
  AutoTrader profiles, files that never ran included (`run_count: 0`) — with what each declares
  (scenario counts declared and enabled, symbols, market types, decision logic and workers after
  the per-scenario cascade; for a profile its bot id, adapter and declared `dry_run`) and its run
  figures from the run index. `key` is `["kind", "file"]`.
- A file that does not parse is a row with `status: unreadable` and its `reason`, never an error —
  a file being edited is broken for minutes at a time. `status: readable` means read, not
  validated.
- Served from a cache at most `FRESHNESS_S` (30 s) old; `?refresh=true` walks the roots now.
- `GET /api/v1/directory/{file}` adds the file's scenarios, read fresh, and the run ids started
  from it, newest first. An unknown file is `404 config_file_not_found`.
- A new grant surface, `directory`: a token needs `directory:*`.

## Version 6 — 2026-09-25 (viewer#21)

- Every error code names its CAUSE, declared once with its status and its sentence
  (`python/api/api_error_catalog.py`). The eight bare `not_found` answers on the bars and broker
  routes became:
  - `broker_not_found` — the broker is not in the bar index;
  - `symbol_not_found` — the broker has no such symbol;
  - `no_bars_indexed` — the symbol is indexed but holds no bars;
  - `timeframe_not_rendered` — no bars exist for that timeframe;
  - `coverage_report_unavailable` — the coverage cache holds no report for it yet;
  - `no_bars_in_range` — bars exist, but none in the requested window.

  The 500 `config_error` became `market_type_not_configured`. Every other code is unchanged; the
  full table is in [`api_server_architecture.md`](api_server_architecture.md#error-responses).
- `run-summary` says which units its figures are NOT summed over, in both pipelines:
  `units_declared` (every unit the configuration names, `enabled: false` ones included),
  `units_disabled` and `units_absent` — `[{name, reason}]`, the attempted units that produced
  nothing. `units_declared == units_disabled + len(units_absent) + unit_count`, from two sources.
  An AutoTrader session is declared 1; one that aborted at startup is absent, with its emergency
  cause.
  A run recorded before this version reads `units_declared: 0` — report artifacts are written once
  and nothing back-fills them, so there 0 means "not stated" and the equation does not hold.

## Version 5 — 2026-09-25 (viewer#21)

- A 404 on `/reports/runs/{run_id}/<section>` now names its CAUSE in `error`. It used to read
  `run_not_found` for every cause, although the run was usually right there:
  - `run_not_found` — no such run in the run index;
  - `reports_not_commissioned` — the run was started with `reporting: none`;
  - `run_not_completed` — the run has no report artifact yet: it is still running, or it ended
    before its report phase, and from the server's side the two look the same;
  - `artifact_not_produced` — the run persisted other sections but not this one, because its
    pipeline does not write it or its outcome left nothing to write.

  `/config` keeps its own two (`run_not_found`, `config_snapshot_missing`).
- `scenario-details`: every `units[]` row carries `market_type`, resolved once from the same owner
  as `data_sources[].market_type`, so a filter reads a field instead of joining the two. The row's
  `data_source` IS the broker type — there is deliberately no second field for it. A run
  recorded before this version has an empty `market_type` on its rows while `data_sources[]`
  carries it: report artifacts are written once and nothing back-fills them.

## Version 4 — 2026-09-24 (#551)

- `GET /api/v1/caller` says who the server takes the caller to be: `client` (the consumer the
  token authenticates as), `account` with `account_kind` (`person` | `service`) and
  `display_name` (on whose behalf it calls), `grants` as a list, and the token's `note`.
- It requires a token while gating is on, and no grant.
- `enforced` is the server's gating state. While it is false nothing verifies a presented token,
  so every identity field is null, even for a caller that sent a valid one.
- `RunResultRow.git_dirty` on `/sweeps/{sweep_id}` changed MEANING, not shape. It used to cover
  this repository only; it now covers every repository a component of the run came from, so a
  strategy from an uncommitted algo repository reads `true`. A code state that could not be
  determined also reads `true`, where it used to read `false` — nothing says it was clean.

## Version 3 — 2026-09-23 (#538, #546)

- `/reports/runs/{run_id}/config` serves the configuration a run was commissioned with, parsed,
  resolved from the run-config store through the run's `config_id`.
- `config_snapshot` on that response is the SOURCE file name. The per-run copy that used to sit
  in the run directory is retired.
- Two distinct 404s: `run_not_found` for an unknown identity, and `config_snapshot_missing` for a
  run that predates the store and therefore carries no id to resolve through.

## Version 2 — 2026-09-23 (#539, #537)

- `deployments`: three read-only routes — `/deployments`, `/deployments/{id}` and
  `/deployments/{id}/booking-periods` — on a new `deployments` grant surface.
- `/reports/runs/{run_id}/booking-periods` serves the run's booking periods as a stored artifact.
  A run from before it answers 404.
- `BookingPeriodsReport.reconciles` is THREE-state: `true` | `false` | `null`, where `null` means
  the run reports no figure in this currency and the check did NOT run.
- `BookingPeriodsReport.run_net_pnl` and `run_total_trades` are nullable for the same reason.
- `BookingPeriodsReport` gained `currencies`. One table is ONE account currency, and this names
  EVERY currency the run booked, the shown one included; the console is what filters the shown
  one out when it prints.
- `RunResultRow.segment_trade_count` was REMOVED. Read `total_trades`, which is the same record
  count and always was.
- `segment_max_drawdown` is now a MAGNITUDE, matching `account_max_drawdown` and the excursion
  columns; the sign is a display decision.
- Every list response declares `key` — what makes one of its rows unique.
- Deployment rows carry `bot_id`, the one identity that does not move.

## Version 1 — before the number existed

Responses carry no `X-Api-Contract` header. A fixture without the header was captured before
version 2 and predates everything above.
