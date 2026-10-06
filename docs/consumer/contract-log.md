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

## Version 22 — 2026-10-06 (#524, #568)

The documentation this API answers with is now served by it, and every route says which document
describes it.

- `GET /api/v1/docs` lists every document this server carries, each with the routes it describes;
  `key` is `["name"]`. `GET /api/v1/docs/{name}` serves one in full as `text/markdown` — this
  API's only answer that is not JSON.
- `GET /api/v1/docs/search?q=…` ranks the documentation's PASSAGES — the text under one heading —
  against a query, best first. A hit names its document, its heading and the line it starts at, so
  it can be opened at the answer rather than at the top; `key` is `["document", "heading", "line"]`.
  It is called a passage and not a section because in this API a *section* is one section of a run
  report, and one word carrying two contracts is how a reader comes to hold the wrong one.
  `terms_not_matched` lists the query's words that appear in no document at all — the field to
  read when a result looks wrong, because a word that is not our vocabulary contributes nothing to
  the ranking and that is invisible from outside.
- A new grant surface, `docs`: a token needs `docs:*`. **A token that enumerates its surfaces is
  refused on these three routes until it is re-minted**, and the server reads its tokens at boot,
  so the new grant takes effect on a restart.
- Three new error codes — `document_not_found` (404), `empty_query` (400) and `query_too_long`
  (400). An empty `q` is refused rather than answered with everything or with nothing: one
  character is enough to search, so an empty query is an error and not a stage of typing.
- Every route now answers with `Link: <…>; rel="describedby"`, naming the document that describes
  it. It is a header rather than a field so it also reaches the routes whose body is a bare array,
  and so a client that ignores it is unaffected.
- `Link` and the seven `X-Bar-*` headers are now published cross-origin. The bar headers were
  already served and were invisible to a browser client, which hides every response header that is
  not safelisted — and three of them carry semantics a caller cannot infer from the rows at all.

## Version 21 — 2026-10-02 (#555, viewer#21)


The worker-decision report says "not counted" instead of zero.

- `GET /api/v1/reports/runs/{run_id}/worker-decision`: every unit carries `worker_decision_tracked`
  — whether it counted its decisions and timed its workers. A backtest leaves it off by default,
  because the tracker sits on the hot path. Untracked, `decision_count`, `buy_signals`,
  `sell_signals`, `flat_signals`, `trades_requested` and the four `decision_*_time_ms` are null;
  they read 0, so a logic that decided on 2,737 ticks said it decided nothing. The unit still names
  its logic (`decision_logic_type`, `decision_logic_name`), which an untracked unit left empty, and
  `ticks_processed` is counted either way. `workers` stays empty when untracked — that empty list is
  true.
- `worker-decision`: a worker row's `compute_ratio_pct` is null when no tick was processed, and
  `ticks_idle` is null when the worker never computed — it read 0, which says "just computed". On
  `worker_totals`, which span several units' tick counts, both are null.

Every stored run was carried over: a unit with no logic type was not tracked — a tracker stamps the
type on every unit it counts — so its counters are null now; a unit with one is marked tracked and
keeps its figures. The logic name of an old untracked unit stays null, because only a new run
stamps it.

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

## Versions 17 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

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
