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

## Version 28 — 2026-10-11 (#508)

A run says how many of its fills were booked at the venue's own fee and how many at our estimate,
and what each came to.

- `portfolio`: every unit row carries `fee_source_totals` — one entry per fee source its fills
  carry, `venue` or `estimate` as on the order-event stream's `fee_source`, each with `fill_count`
  (the fills) and `fill_fees` (what their fees came to, in the account currency). A backtest has
  only `estimate`: it has no venue to ask. Empty where the unit executed nothing. Every
  per-currency row carries the same, summed over its units — and so does the `headline` of every
  block on `aggregated-portfolio`.
- `order-history`: every row carries `fee_source` — where its `commission` came from, `venue` or
  `estimate`. Null where nothing executed.

Both are additions; no existing field changed. A run recorded before this contract serves an empty
`fee_source_totals` and a null `fee_source`, until it is run again.

## Version 27 — 2026-10-11 (#508)

A live fill's fee is the venue's own charge, and every executed order event says where its fee
came from.

- The order-event stream: `fee_source` on every event that executed something — `venue` where
  `fee` is the figure the venue reported for that fill, `estimate` where it is our fee model's
  (the declared rate times the order's value). A backtest is always `estimate`: it has no venue to
  ask. Null on an event that executed nothing, on a fill nothing could be booked for whose venue
  reported no fee, and on every event recorded before this contract.
- `fee` on a live fill — and with it the order history's `commission`, the trade history's fees
  and every fee figure summed from them — is now the venue's own charge wherever the venue reports
  it in the answer that says the order filled, no longer the declared rate's estimate. Same fields,
  another source: a live run before this contract and one after can differ in fees for the same
  orders. A live event that still says `estimate` where its venue reports the fee means the answer
  came without it; the session's log says so as well.

## Version 26 — 2026-10-09 (#582)

A run's header is served whole, and the run list says who started each run.

- `GET /api/v1/reports/runs/{run_id}/header` — new: the record every run writes at its start —
  what it is, what it was started from, who started it and which code ran. Every path in it is
  relative to its repository; no path of the machine is served. `run_not_found` for an unknown run,
  `run_header_missing` for a run the index lists whose header file is gone, `artifact_unreadable`
  for a header that no longer matches its model — naming the fields that fail. Its
  document, [run header](/api/v1/docs/run-header), is the authoritative list of every key, with its
  meaning and the label to show it under.
- The run list: every run carries who started it — `origin_channel` (`cli`, `sweep`, `direct` or
  `api`), `origin_client`, `origin_principal` and `origin_host`, the header's `origin` block
  flattened. Null on a run that predates the block.
- *Origin* on its own keeps one meaning: who started a run. Three served fields still carry the
  word for something else — the configuration directory row's `origin` (where a file lives), a
  feed-health episode's `origin` (real or stress-injected), and `origin_classes` /
  `origin_evidence_grades` on scenario details and sweep combinations (the class of instance that
  produced a run's data); a later contract renames them.
- The run list: `fixture_superseded` is `true` also for a run of a catalog entry this side no
  longer declares — nothing can produce it again, so it is current no more.

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

## Versions 23 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

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
