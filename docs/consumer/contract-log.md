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

## Version 25 — 2026-10-08 (#576)

The runs a consumer pins are now entries of a fixture catalog on this side — produced and checked
by one command — and the run list says which of them is current.

- The run list: every run carries `fixture_superseded`. `false` — the run was made by its catalog
  entry's CURRENT catalog production, so it is the one to pin. `true` — an older catalog production
  made it, or one that failed its check. A replaced run that passed its check stays until this side
  releases it, so a pin on it keeps working until you move it. `null` — no catalog production made
  this run, which is every ordinary run and every test run. Derived each time the list is served;
  nothing in the run changes.

An addition; no existing field changed.

## Version 24 — 2026-10-08 (#576)

Every run says what it is FOR, and under which contract its reports were written.

- The run list: every run carries `run_purpose` — `regular`, `fixture` or `certificate`. A
  `fixture` run was constructed to show something: every run a test starts, and every run a
  consumer pins — its numbers are built, not earned. A `certificate` run is a release-gate run
  whose record becomes a certificate. It is what the run's configuration declares, stamped at the
  start. For a run recorded before this, the value is filled in when the run index is rebuilt, from
  the CURRENT declaration of its configuration where that configuration can still be read; `null`
  before that rebuild and where none is found.
- The run list: every run carries `report_contract`, the contract its reports were written under.
  A run older than a contract that added a figure serves that figure as `0` or `null` — compare
  this number with `/api/v1/contract` to tell an old run from a wrong one. `null` on a run recorded
  before this.
- The configuration directory: every row carries `run_purpose` — `regular` when the file declares
  nothing, `null` when the file could not be read — and `config_description`, the file's
  `description`, Markdown allowed, `null` when it has none. A `run_purpose` the file misspells, any
  `run_purpose` in a file whose `origin` is not `configs` — the user's own files always run as
  `regular` — or a `description` that is not text makes its row `unreadable`, with the reason in
  `reason`.

All three are additions; no existing field changed.

## Version 23 — 2026-10-07 (#362)

Every step of an order's life is recorded, and the order counts, the order history and the
pending-order section are counted from that record — one status for each way an order can end.

- New: `GET /api/v1/reports/runs/{run_id}/order-events`, the run's order-event stream. `events`
  holds one line per step in an order's life — submitted, accepted, refused, triggered, modified,
  cancelled, filled, expired, an answer that was lost and how it was settled — and for a live
  session `broker_truth` holds what the venue reported when the session asked it: at its start, at
  its end, and when the session's comparison with the venue changed. `keys` declares both lists
  `["scenario_name", "seq"]`, and `seq` runs across both within a unit. It is served while a run
  is still going; `truncated_tail` says a session was stopped in the middle of a line, and
  `scenario_name` and `order_id` narrow it. The run list names a run's streams in the new
  `stream_files`.
- New: `GET /api/v1/reports/runs/{run_id}/venue-account`, live sessions only — what the venue held
  at the session's start and at its end (open orders and positions counted, the balances as the
  venue reported them, a part it could not read named in `unread_parts`) and the reconciliation in
  between: `reconcile_lines`, `divergent_lines`, `last_reconcile_state`, `last_divergence`. `key` is
  `["name"]`. A backtest has none, and neither has a dry run against a real venue, whose account
  reads never reach the venue — 404 `artifact_not_produced`.
- The order counts — `execution-stats` (every unit and `totals`), `run-summary`,
  `aggregated-portfolio` and a sweep's `combinations`: `orders_sent` is gone. In its place there is
  one count per way an order starts or ends — `orders_submitted`, `orders_adopted` (an order a
  previous session sent, taken over at this session's start), `orders_executed`, `orders_denied`
  (refused before anything was sent), `orders_rejected`, `orders_cancelled`, `orders_expired`,
  `orders_undelivered` and `orders_unaccounted`. **Two meanings changed:** `orders_executed` now
  counts the fills of closing orders too, and `orders_rejected` counts the venue's refusals only.
  `execution-stats` adds `orders_failed` — denied, rejected, undelivered and unaccounted together —
  so a consumer need not add them up.
- `order-history` keeps a row per submission and one per way an order ended. `status` gains
  `denied`, `undelivered` and `unaccounted` and loses `submitted` and `partial`. New:
  `order_type` (as the order was asked), `close_type` (`full` or `partial`, on a close row),
  `initiator` — who ended the order: `strategy`, `framework` or `venue` — and `end_reason`:
  `cancel_requested`, `protection_released`, `order_timeout`, `resolution_ceiling`, `session_end`,
  `scenario_end`, `venue_cancelled`, `venue_expired`. `swap` and `slippage_points` were removed.
- `rejection_reason` gains `unaccounted_order`, `position_not_found` and `close_withheld` and loses
  `broker_unreachable` and `unresolved_write`: an answer that never came is not a refusal, and the
  order events follow it as `unresolved` and then `resolved`.
- `pending-orders` is counted from the order events, and **an AutoTrader session now has a row** —
  it had none. The counters carry the names of what they count: `total_submitted`,
  `total_accepted`, `total_rejected`, `total_never_confirmed` (undelivered and unaccounted) and
  `total_expired` replace `total_resolved`, `total_filled`, `total_timed_out` and
  `total_force_closed`. `avg_in_flight_ms`, `min_in_flight_ms`, `max_in_flight_ms` and
  `in_flight_count` — the time from a submission to the venue's answer — replace the four
  `*_latency_*` fields, and `never_confirmed_orders` lists the orders behind
  `total_never_confirmed`. The `pending_*` fields of `aggregated-portfolio` follow the same names.
- `trade-history` adds `close_type`, `entry_lots` (the position's size when it opened) and
  `position_closes` (how many records the position produced in its unit).
- Deployments say whether real money moved. `GET /api/v1/deployments` adds `orders_to` per row —
  where its sessions' orders went, each value once: `["venue"]` real money, `["simulated"]` a
  rehearsal, both a deployment that did each — and a row mixing the two is `changed`. On
  `GET /api/v1/deployments/{deployment_id}` each session carries `orders_to` and
  `orders_to_changed`, the advisory carries `orders_to` and now also fires on a mix alone, and a
  sweep's `combinations` carry `orders_to` too. A single run said it already, as `orders_to` on the
  run list.

A run recorded before this contract serves `0` in the new counts and the pending-order counters,
and `null` in the new order-history fields and in `orders_to` on deployments, until it is run
again.

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

## Versions 20 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

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
