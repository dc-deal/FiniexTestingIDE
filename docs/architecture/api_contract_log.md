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
  scenario, or a venue session that has not ended). Deliberately no single span over all units: it
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
