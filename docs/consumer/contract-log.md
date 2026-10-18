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

## Version 34 — 2026-10-18 (#606)

A price the venue quoted or traded is now served as the decimal the venue wrote.

- Every price a run read from the tick archive — a tick's `bid`, `ask` and `last`, the open, high,
  low and close of a bar whose price basis is `order_driven`, a fill at a recorded quote — is the
  decimal the collector recorded: `0.87957`, where a run before this contract read
  `0.8795700073242188`. The archive was re-imported from its raw files on 2026-10-17 and keeps
  double precision from now on.
- `real_volume` keeps every decimal the venue reported.
- New document [prices](/api/v1/docs/prices): which prices are the venue's decimals and which are
  computed, and how to round a computed one for display by the instrument's `tick_size`.

No field was added or removed. A run recorded before this contract keeps the values it read.

## Version 33 — 2026-10-16 (#602)

A backtest over data recorded without a quote now pays a spread. Each such tick is given the quote
the spread estimated for the previous day implies, and what the fills crossed against it is
reported as a figure of its own beside the measured spread.

- `GET /api/v1/reports/runs/{run_id}/trade-history`: every trade row carries
  `modelled_spread_cost` — the spread its fills crossed against a modelled quote. It is inside
  `gross_pnl` like `spread_cost`, beside `total_fees` and never inside it, and the two add up to
  the trade's effective spread: a fill against a recorded quote counts in `spread_cost`, one
  against a modelled quote in `modelled_spread_cost`. 0 where every quote was recorded. Every
  `scenario_totals` row carries `total_modelled_spread_cost`, always a number.
- `portfolio`: every unit row carries `total_modelled_spread_cost`; `aggregated-portfolio` sums it
  per block.
- `booking-periods`: every period carries `modelled_spread_cost` beside `spread_cost`; the
  deployment rows carry it as `period_modelled_spread_cost`. Null on a period recorded before this
  contract.
- `scenario-details`: every row carries `modelled_spread` — `factor`, `estimator_version`,
  `ticks_without_quote`, `ticks_modelled`, `median_spread_bps` and `day_digest` — null where the
  scenario read no tick without a quote.
- **A change of meaning on data without a quote:** a backtest's fills, the marks of its open
  positions and its stops now see a spread there, so its `gross_pnl` and `net_pnl` are lower than
  those of the same set run before this contract. `spread_cost` there stays what contract 32 made
  it — nothing was measured.

Fields added, none removed or renamed; the P&L of a backtest over data without a quote changes.
Its documents are [trade history](/api/v1/docs/trade-history),
[portfolio](/api/v1/docs/portfolio), [aggregated portfolio](/api/v1/docs/aggregated-portfolio),
[booking periods](/api/v1/docs/booking-periods) and
[scenario details](/api/v1/docs/scenario-details).

## Version 32 — 2026-10-15 (#599)

A gap says whose silence it is, and a symbol with years of a venue's history no longer has to be
read in one piece. And a unit's trade totals carry the spread it paid.

- `GET /api/v1/brokers/{broker}/symbols/{symbol}/gaps`: every gap carries `origin_class` — the
  origin class of the tick file it lies in or follows (`production`, `development`, `unknown`,
  `external`), null where no tick file could be found — and `origin_class_after`, which differs
  from it only at a seam, where one origin's data ends and another's begins. A gap in `external`
  data lies in a venue's published history: nobody traded there, and there is nothing to
  re-collect.
- New beside the gaps: `gaps_by_year` — per year and origin class the short, moderate and large
  gaps and the hours without data, always over the whole archive.
- **`key` is replaced by `keys`**, one entry per list, because the answer now carries two:
  `{"gaps": ["start"], "gaps_by_year": ["year", "origin_class"]}`. Read the gaps' key from
  `keys.gaps`.
- Two query parameters narrow `gaps`, never `gap_counts` or `gaps_by_year`: `origin_class` (only
  the gaps in data of that class) and `since` (only the gaps that start at or after it, ISO-8601;
  anything else is refused with `400 invalid_timestamp`). Without them every gap is served, as
  before.
- `reason` no longer guesses at a cause: "check data collection" and "restart/connection?" are
  gone, because the reason cannot know whose data surrounds a gap. A coverage cache built before
  this contract still serves the old words until it is rebuilt.
- `category` declares its closed set in the schema; its values are unchanged.
- `GET /api/v1/reports/runs/{run_id}/trade-history`: every `scenario_totals` row carries
  `total_spread_cost` — the unit's measured `spread_cost` summed, beside `total_fees` and never
  inside it. On a quote-driven market it is usually the main cost while `total_fees` reads zero.
  It is null where the unit read data without a quote, for the whole unit even where only part of
  its window lacked one, and on a run recorded before this contract.
- The portfolio's `total_spread_cost` follows the same rule, and is therefore now nullable: null
  for a unit that read data without a quote, where it read `0.0`, and on the aggregated portfolio
  — with `avg_spread` — null as soon as one unit's is.

Fields and parameters added, `key` replaced by `keys`, one wording changed, and three spread
figures may be null. Its documents are [gaps](/api/v1/docs/gaps),
[trade history](/api/v1/docs/trade-history), [portfolio](/api/v1/docs/portfolio) and
[aggregated portfolio](/api/v1/docs/aggregated-portfolio).

## Version 31 — 2026-10-14 (#595)

The machine this server runs on can be asked how full it is.

- `GET /api/v1/machine-resources` — new, with a token and no grant: the processors and the memory
  of the machine the server, the backtests and the live sessions run on. `memory` names the limit
  that binds — a container's own where one is set below the machine's — and where it was read
  (`limit_source`: `cgroup` or `machine`), how much is in use, and the two thresholds it is graded
  against. `cpu` counts the processors that bind and says how busy the machine was over `window_s`,
  the seconds since the previous reading of this route by any caller. `pressure_level` (`ok`,
  `warn`, `critical`) grades memory alone, and `pressure_reasons` says why in a sentence for a
  person. Its document is [server](/api/v1/docs/server).

A route added; no field changed its shape.

## Version 30 — 2026-10-14 (#561)

A venue's published history can enter the archive, and what an input file lacks is named.

- `GET /api/v1/validation-checks` and the findings of a run: `spreadless_tick_data` is gone.
  `data_no_quote` counts every input file without a quote, whatever the reason — an older
  collector format or a venue's history that keeps no quote. New beside it: `data_no_arrival_time`,
  `data_arrival_time_reconstructed`, `data_missing_trades` and `data_origin_external`, and
  `data_synthetic`, which takes over the synthetic-signal warning that `signal_availability`
  carried. A run recorded before this contract may still name `spreadless_tick_data`; the catalog
  no longer lists it, so show the id itself.
- `origin_classes` on a ledger row and in the scenario details may hold `external`: data no
  collector of ours recorded, such as a venue's published history. It is never admissible for a
  parity measurement.

An id removed and a value added; no field changed its shape.

## Versions 29 and earlier

One line each. Every one of these moved a shape or a meaning; what they moved is summarised here
rather than spelled out, because a consumer this far behind needs the list of steps, not each step's
reasoning. The full text of a compressed version is in this repository's history.

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
