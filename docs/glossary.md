# Glossary

A word in this project means one thing. Several of them used to mean several — *live* named a
pipeline, a broker adapter, a recompute cadence and real money, sometimes within one report — and
a reader who learned a word in one place carried the wrong meaning into the next. This page fixes
the meaning of each term; the document linked from an entry is where it is explained.

Where one word has several legitimate meanings, each carries a qualifier and the bare word is not
used alone: the entry for the word lists the qualified terms. A name in the code is used unchanged
when the code is meant (`adapter_type`, `run_type`), and trading vocabulary stays English
everywhere.

What earns an entry is chosen the way a book's index is: by what a reader will look up, not by
completeness. A term is here when a reader meets it without the code — an API field, a line on a
screen or in the console, a configuration key, a heading — and a guess from the word alone would
go wrong, or when the term is this project's own coinage. A word with a second meaning here is
listed always. A word used in its ordinary sense is not, and neither is a name nobody outside its
module sees. A word someone has reached for that is not the one we use points to the one we do
(*market clock* — see *canonical clock*). A term that qualifies is added in the same change that
starts using it.

Not here: how things work. The [Introduction](introduction_to_the_ide.md) shows how the kinds of
run fit together; the linked documents explain the rest.

---

**acceptance study** — A real-money session sequence whose record becomes a release certificate:
the *field study* proves the order path, the *security study* the account's protective rules.
Both run on one engine and write one capture format. Not a test suite — the suite only validates
the certificate it left. See [Security Study](tests/security_cert/security_cert_guide.md).

**accepted** (an order event) — The venue took the order: it answered with its reference, the
order started resting, or it filled in its answer. Recorded once per order, before its first fill;
in a backtest when the order arrives at the simulated venue. Not an order-history status — the order
history records how orders end. See [Order-Event Stream](architecture/order_event_stream.md).

**account** — One run unit's own balance. A backtest has one per scenario, each started from its
own capital; an AutoTrader session has one. A figure that means one account's value (`final_equity`,
`max_equity`, the drawdown) is null wherever it would have to speak for several, and a sum over
several is a *total* that says so. See *total final equity*.

**account model** — How an account holds value: `spot` (an inventory of assets) or `margin` (a
balance plus margin arithmetic). Configured as `trading_model`, read at runtime as `spot_mode`.
See [Market Model](architecture/market_model.md).

**active orders** (`active_limit_orders`, `active_stop_orders` on the pending-orders report) — The
orders still resting when a run unit's data ended. In a backtest the same orders are recorded as
*expired* in that same step, so the lists say what was waiting at the end, never that it is still
open. A LIMIT sits in the limit list, a STOP or STOP_LIMIT whose trigger was not reached in the stop
list. See *resting*.

**activity** — Two meanings, always qualified by where it stands. *Something running on the
machine*: a backtest, a sweep, an AutoTrader session, an import, a discovery cache build, the test
suite or the API server, entered in the machine's activity list while it runs and keyed by its own
`activity_id` (`activity_kind`, `activity_state`, `activity_label` on `/api/v1/activities`). An
activity can be a run, but it is not one: an import or the test suite writes no run, and an
`activity_state` is not a run outcome. And *how busy a market was* — the activity measure of a bar
or a profile (`primary_activity_metric`, a bar's tick count). See
[What runs on this machine](architecture/activity_registry.md).

**adapter** (broker adapter) — The code that speaks one venue's API behind one interface the rest
of the framework uses. Chosen by `adapter_type`: `mock` for the broker-neutral `MockBrokerAdapter`,
`live` for a real venue's adapter. See [Adapter Development](user_guides/adapter/adapter_development_guide.md).

**adopted** (`adopted` order event, `orders_adopted`) — An order a previous session sent and this
one took over at its start: a resting order found at the venue under this bot's key, or a protective
order carried over with its position. Counted apart from `orders_submitted`, which is what this
session sent; an execution rate divides by both. See *carry-over*.

**alert episode** — One symptom as the operator hears it: an *operator alert* when it starts, the
all-clear once it has stayed away for `all_clear_after_s`, and a recurrence before the all-clear
inside the same episode — so a breaker or a feed flapping around its threshold is two messages,
not one per crossing. Not the *disturbance episode* a run report counts
(`disturbance_episode_count`), one per stale stretch of an input: one alert episode can span
several of them. See [Unattended Operation](autotrader/unattended_operation.md) and, for the
disturbance episode, [Outage Handling](user_guides/outage_handling_guide.md).

**algo** — The code of a strategy: its decision logic and its workers, whether shipped with the
framework (CORE) or written by a user. A user's algos are kept in `user_algos/`. See
[User Algo Workspace](user_guides/user_modules_and_hot_reload_mechanics.md).

**archive** (tick archive) — The imported tick data a backtest and a mock session replay. The one
part of the application that stays backward compatible: data from every collector format ever
accepted is still read. See [Data Import](data_pipeline/data_import_pipeline.md).

**AutoTrader session** — One start of an AutoTrader profile: one run, run type `autotrader`, one
run unit. A mock session, a dry run or a real-money session. See
[AutoTrader Architecture](autotrader/autotrader_architecture.md).

**backtest** — A run of the simulation pipeline over a scenario set, on archived ticks with
simulated fills. See [Process Execution](process_execution_guide.md).

**balance · equity · holding · position** — A *balance* is an amount of one asset or currency in
the account. *Equity* is the account's value (in code, the account value): balance plus unrealised
P&L at margin, quote balance plus base asset times mid at spot. A *holding* is a spot base-asset
balance, which the venue cannot describe as a position. A *position* is our record of one open
exposure. See [Capital and Safety](autotrader/autotrader_capital_and_safety.md).

**basis** — Never used alone. The qualified terms: *compute basis*, *decision basis*, *price
basis*, *signal basis*, *time basis*, *trade window basis* — each below.

**booking period** — One sealed period of one run unit, bounded by the market's trading day and
numbered from 1; the ledger holds one row per booking period and currency. In code
`BookingPeriod` and `period_no`. See [Accounting Periods](architecture/accounting_periods.md).

**broker** (`broker_type`) — Our configuration entry for one venue and product in
`market_config.json` (`kraken_spot`, `mt5`). Not the same as the venue. The broker whose ticks a
unit read is its *data broker*. See [Broker Config](broker_config_guide.md).

**broker configuration** (`broker_config_id`) — The symbol specifications and fee structure a run
trades with, assembled at its start: for a dynamic broker (`kraken_spot`) the runtime cache's specs
and the seed's fees — in a live-adapter session with `auto_detect_fee_tier` on, the venue's fee tier
replaces the seed's rates; for a static broker, and for a mock session, the seed file. `config_hash`
digests its symbol specifications and fee structure; every run, a session and a backtest
alike, freezes the content in the run-config store and names it as `broker_config_id` in its broker
section. See [Data Storage Layout](architecture/data_storage_layout.md).

**broker ref** (`broker_ref`) — The venue's handle for an order — a txid at Kraken, a ticket at MT5
— under one name above the adapter. Known once the venue has answered; an amend that replaces it
is recorded as `modified` with the old handle in `previous_broker_ref`. Not the *client order id*,
which is ours.

**broker truth** (`broker_truth` lines of the order-event stream) — What the venue reported when a
live session asked it: its open orders, its balances and, on a margin account, its positions,
written beside the session's own steps on the same counter. `read_reason` says when —
`session_start`, `session_end`, or `reconcile` when the reconciliation picture changed. Each part
is a value (an empty one says the venue holds nothing), unread (`null` and named in
`unread_parts`), or not read on that occasion (`null`, unnamed). Never in a backtest, whose venue is
its own book, and never in a dry run against a real venue, whose account reads never reach it. See
[Order events](consumer/order-events.md) and *venue account*.

**cache** — A derived store of computed results. Deleting it loses nothing; a "cache" whose
deletion loses data is misfiled. See [Data Storage Layout](architecture/data_storage_layout.md).

**cadence** — How often something recurs; always said with WHAT recurs, because the word means
different things in different places. The *state frame*'s cadence is a setting:
`state_frames.cadence_timeframe` in a profile (`M1` by default), one frame per period of that
timeframe on the *canonical clock*, served as `cadence_timeframe` and `cadence_s`. A signal
source's `cadence_seconds` is a measurement: the median distance between its snapshots in the
archive. How often a worker computes — on every tick or once per closed bar — is served as its
`compute_basis` (`live` · `bar_close`). See [State frames](consumer/state-frames.md).

**canonical clock** — The one clock a decision logic, a worker and the execution layer read:
`get_current_time()`. In a backtest it is the time of the replayed tick. In an AutoTrader session a
tick sets it to the tick's own time and an idle heartbeat to the machine's UTC clock, so timeouts
keep tracking real elapsed time: in a live-adapter session the two are one clock, in a mock session
it moves between replay time and the present. See *wall clock*, and
[Live Execution](architecture/live_execution_architecture.md#canonical-clock--idle-cadence-360).

**carry-over** — What one session hands the next session of the same bot: the algo's memory, and
the framework's record of keys, positions and the risk baseline. Keyed by the bot, overwritten, and
never kept per run. A document written under another profile name is not this bot's, except for a
*declared continuation*. See [Data Storage Layout](architecture/data_storage_layout.md).

**cascade** — How a configuration value is layered. For a scenario: application defaults, then the
set's `global` block, then the scenario. For an AutoTrader profile: `app_config.autotrader`
defaults under the profile. The `user_configs/` override is a separate mechanism, not a level.
See [Config Cascade](config_cascade_guide.md).

**catalog production** — One run of the fixture catalog's `produce` for one entry: the runs it
made and whether they carried every property the entry asserts. The newest one that passed is the
entry's *current fixture*. Not a *production* profile, which trades real money. See
[Fixture Catalog](architecture/fixture_catalog.md).

**certificate run** (`run_purpose: certificate`) — A release-gate run whose record becomes a
release certificate, such as a stage of an *acceptance study*. Never produced again and never deleted.
Not the certificate itself, which is a file beside the gate's test suite. See *run purpose*.

**client order id** (`client_order_id`) — Our key for an order, sent with it on the wire so the
venue's answers and its list of open orders can be matched back to us — and to this session, whose
run id it carries a piece of, beside a counter. Live only; a backtest sends none. Not the *order id*
(`pos_btcusd_47`), which never goes on the wire, and not the *broker ref*, which is the venue's.

**close type** (`close_type` on an order-history row and a trade) — Whether a close took the whole
position (`full`) or part of it (`partial`). Set once the close fills; empty on every other row.

**combination** — One point of a sweep's parameter grid, run as one ordinary backtest. See
[Parameter Optimization](architecture/parameter_optimization_system.md).

**complete at** (a bar) — A bar is complete at an instant when its period has ended by then: its
opening time plus its timeframe's duration lies at or before that instant. A bar is stamped with
the instant its period opens but known only when it ends, so a bar that *opened* before a start is
not therefore complete at it — the bar holding the start opened before it and closes after. See
[Warmup History](architecture/warmup_history.md).

**compute basis** — When a worker recomputes: `live` on every tick (the forming bar included) or
`bar_close` only when a bar closes. Not a kind of run. See [Worker Naming](user_guides/worker_naming_doc.md).

**config description** (`config_description`; `description` in the file) — Why a configuration
exists, in its own words, declared at the top of a scenario set or a profile; Markdown allowed.
Served on the configuration's directory row. Not the one-sentence description of a validation
check. See [Directory](consumer/directory.md).

**config hash** (`config_hash`) — Eight characters over two parts of a broker configuration: every
symbol's specification in it, traded or not, and the fee structure — never when a value was
fetched, and not the account terms (leverage, margin levels). Two runs with the same hash traded
under the same specifications and fees, but the hash cannot give them back — `broker_config_id`
names the copy that can. Where the symbols carry a fetch time, as on `kraken_spot`, its values
shifted once when the fetch time left the digest, so an older run's hash differs from a newer
run's over the same values. See [Broker Config](broker_config_guide.md).

**config id** (`config_id`) — The content id of the configuration a run was GIVEN: the scenario set
or profile as written, *frozen* when the run started. Not what it ran with — an AutoTrader
session's configuration with every default filled is `rendered_config_id`, and a sweep's
combinations all name the base set; what each changed is on its ledger row (`param_hash`,
`strategy_config_json`).

**config snapshot** — The FILE NAME of the configuration a run started from, recorded in its
header (`config_snapshot`). The full strategy configuration a ledger row carries is a different
field, `strategy_config_json`; the copy served at `/reports/runs/{run_id}/config` is the *frozen*
configuration the run was given (`config_id`). See [Data Storage Layout](architecture/data_storage_layout.md).

**continuous** — Never used alone. *Continuous mode*: a generator strategy that makes one window
per continuous data region. *Continuous data region*: a stretch of ticks with no interrupting gap.
*Continuous deployment*: the sessions of a bot declared `deployment.continuous: true` — in prose
simply a *deployment*. `sources.<id>.continuous` in `sentiment_config.json`: a signal source that
runs around the clock. See [Scenario Generator](generator/generator_block_splitting_architecture.md).

**CORE / type string** — How a configuration names a worker or a decision logic: `CORE/<name>`
resolves against the framework's registry, anything else is a file path. The former `USER/`
namespace no longer exists. See [Worker Naming](user_guides/worker_naming_doc.md).

**cost_min** (on a symbol's specification) — The smallest order VALUE a venue accepts, in the quote
currency — Kraken's `costmin`; `0.0` where the venue declares none. A second minimum beside
`volume_min`, which counts lots — base units where `contract_size` is 1, as on Kraken spot: an
order has to meet both. Not a fee. See
[Broker Configuration](broker_config_guide.md).

**data broker** (`data_broker_type`) — The broker whose tick archive a scenario or a mock session
reads, and the key the bar routes are addressed by. A backtest simulates its orders against the
same broker's configuration; a mock session may trade against another one (`broker_type`); a venue
session reads its own broker's feed. In a report it is each unit's `data_broker_type`; in the
configuration directory, a row's `data_broker_types`.

**data home** (`FINIEX_DATA_HOME`) — The directory the operator's state lives in, apart from the
code: the workspace (`user_configs/`), the strategy directories and every store root resolve under
it, so a deploy into a new code tree finds them again. Unset, it is the working directory. Not
the operating system's home directory (`~`), where the list of what runs on the machine lives. See
[Data Storage Layout](architecture/data_storage_layout.md).

**data origin** — Which kind of source produced an archived file: `origin_class` (`production`,
`development`, `unknown`, `external`) with its evidence grade, stamped at import and carried into
every run's consumption record. Not the *broker*. See [Data Provenance](architecture/data_provenance.md).

**data source** — One of the inputs a scenario reads, named by its key: its *data broker* for the
ticks, its `data_sentiment_type` for the signals. A stale-data stress event names the one it makes
stale in `stale_data_source`. See [Stress Test](stress_test.md).

**data window** — The market window one run unit was declared to cover (`data_windows` on the run
header): a start, and an end unless it is open. Not a span over several units. On this side a
*window* is always a span of TIME — the served `window_start`, `window_end`, `window_count` and
`windows` mean the same — never a docked panel: that is the FiniexViewer's *panel*, defined in its
own glossary.

**decision basis** — The fresh / stale / blind counts of the inputs a decision was made on. See
[Signal Data Source](data_pipeline/signal_data_source.md).

**decision logic** — The one class that turns worker outputs into decisions. See
[Quickstart](user_guides/quickstart_guide.md).

**declared continuation** (`carry_over_from` in an AutoTrader profile) — A later stage of one bot
that starts from what an earlier stage left: the later profile names the earlier one, and the two
share the bot's carry-over in both directions. Allowed only between a release study's stages or a
test's fixtures, and held at boot to the conditions that keep it from being mistaken for a copy.
See [AutoTrader Configuration](autotrader/autotrader_configuration.md).

**deficiency** (`DataDeficiency`; `deficiencies` in the tick index) — What an archived file LACKS:
`no_quote`, `no_arrival_time`, `arrival_time_reconstructed`, `missing_trades`, `synthetic`. Measured
from the data, never inferred from a format version, and counted once per run. Not a gap in time,
and not a property of the venue. See [Data Provenance](architecture/data_provenance.md).

**denied** (an order status and an order event) — Refused HERE, before anything was sent: a lot
size the venue would not take, funds already committed, the order guard. Never submitted, so a
denied order event carries no `submitted_seq`. A refusal the venue made is *rejected*.

**deployment** — The sessions of one bot joined into one history by a `deployment_id`, because its
profile declares `deployment.continuous: true`. Not itself a run. See
[Deployment Ledger](user_guides/deployment_ledger_guide.md).

**discovery** — An analysis the IDE derives from the archive, such as the coverage report or the
spread estimate: regenerable at any time and cached against its source. Not a store of its own —
every family lives in the `discovery_caches` store — and never something the IDE records about
itself: a run, a certificate or a carry-over is not a discovery, because deleting it loses what
happened. See [Discovery System](discovery_system.md).

**dry run** — An AutoTrader session on a live adapter whose `dry_run` resolves true: real ticks,
every order validated by the venue and never placed, fills simulated locally. A mock session is not
called a dry run, although the `dry_run` flag reads true for it too. To be renamed *paper* (#304).
See [AutoTrader Configuration](autotrader/autotrader_configuration.md).

**end reason** (`end_reason`) — Why an order ended without filling: the strategy's cancel, a
protective order released, the order timeout, the resolution ceiling, the end of the session or of
the backtest's data, or the venue's own cancel or expiry. Stands beside *initiator* on the same row
or event.

**entry member** — One file a store entry may hold, declared by the store: its path, its shape
(a *snapshot member*, a stream, a report output, a log), which pipeline writes it and when, and which
column of the run index lists it. A run's directory and a sweep's are declared this way, and the
writers build their paths from the same names. Never *member* alone — an archive's members and an
enum's are other things. See
[Data Storage Layout](architecture/data_storage_layout.md#the-levels).

**error count** (`error_count`) — The ERROR records in a run's error pot. A unit that failed
without logging an error shows in the *run outcome*, not here. See
[Warnings & Errors](architecture/warnings_errors_tiers.md).

**event time** (`event_time` on an order-history row and an order event) — When that row's event
happened, on the run's *canonical clock*: the submission on a `pending` row, the fill on an
`executed` one, and on every other row the moment the order ended without a fill — refused,
cancelled, expired, undelivered or unaccounted. On an order event, when that step happened; empty
only on a line written before the session's first market data — an order adopted at its start, and
the `session_start` *broker truth* line. A point in time — not the
*execution time*, which is a duration.

**execution time** — How long a run or one of its units took on the *wall clock*:
`execution_time_ms` for a scenario, `execution_time_s` for a whole backtest run. It says nothing
about how much market time was processed — that is the *tick timespan* — and it is never a point
in time: when an order's event happened is its *event time*.

**external** (`origin_class: external`) — Data no instance of ours recorded: a venue's published
history, brought in by a converter that stamps its own identity. A backtest reads it only where
`admitted_origin_classes` admits it, and it is never admissible for a parity measurement. Not
*unknown*, which means nobody could be named. See [Data Provenance](architecture/data_provenance.md).

**fee report** (`fee_report`) — Where a venue reports the fee it charged for a fill, as its adapter
declares it: `status_answer` — in the answer that reports the order filled, as Kraken does;
`executions` — only with the order's executions, after the fill; `not_reported` — no fee per fill,
as at a spread broker. A declaration, not a report of fees: it decides whether a live fill can carry
the venue's own figure (*fee source*). A field study's capture records it in its header. See
[Broker Trade Records](architecture/broker_trade_records.md).

**fee source** (`fee_source`, `fill_fees_by_source`) — Where the fee booked for a fill came from:
`venue` — the figure the venue reported for that fill, in the answer that reported it, which is
what the account was charged; `estimate` — our fee model's, the declared rate times the order's
value. A backtest is always `estimate`; a live fill is `venue` wherever its venue reports the fee
there. Where the venue does is declared on the adapter (`fee_report`). On the portfolio report,
`fill_fees_by_source` counts a unit's fills per fee source (`fill_count`) and what their fees came
to (`fill_fees`) — neither of the two fee totals: swap is not a fill's fee. Not a *source* in the
signal sense. See [Portfolio](consumer/portfolio.md).

**fees charged** · **total fees** (`fees_charged`, `total_fees`) — Two fee totals of one run.
*Total fees* are the fees of the trades it CLOSED — the population every trade row, booking period
and trade analytic sums. *Fees charged* are everything the run charged, entry and exit fees and
swap on every position, open ones included. They differ by the fees of what is still open.

**field study** — The real-money acceptance test of the live execution stack, whose record becomes
a release certificate. See [Field Study](tests/live_field_study/field_study_guide.md).

**first start** (`--first-start`, served as `origin.first_start`) — A start declared the bot's
first: the one way a session trading real money starts without a carry-over to continue. A
declared first start that finds a usable carry-over is refused. See
[Unattended Operation](autotrader/unattended_operation.md).

**fixture** — Never used alone. *Fixture run* (`run_purpose: fixture`): a run constructed to show
something — every run a test starts, and every run a consumer pins; its numbers are built, not
earned. *Test fixture*: the data a test reads from `tests/fixtures/`, or a pytest fixture in a
`conftest.py`. A consumer's *fixtures* are the answers it captured from this API to test against.
A *current fixture* is the run a catalog entry's newest verified production made — see
[Fixture Catalog](architecture/fixture_catalog.md); a *superseded* one (`fixture_superseded: true`)
was made by an older or a failed production. See *run purpose*.

**fragment** — One ledger file, written per run. Nothing else is called a fragment.

**frozen** — Kept by a run, at its start, as a copy in the run-config store and named by its
content: the configuration it was given (`config_id`), the one an AutoTrader session ran with
(`rendered_config_id`), the broker configuration (`broker_config_id`). A frozen copy is never
rewritten, so an old run keeps saying what it used. Not the fee seed's `_fee_structure_frozen`,
which records when a declared fee rate was set. See [Introduction](introduction_to_the_ide.md).

**in flight** — A pending order that was sent and not yet acknowledged: in a backtest the latency
simulator still holds it, in a session the venue has not yet answered. `in_flight_ms` on an order
event is how long that took, on the acceptance or refusal that answers the submission — the modelled
delay in a backtest, measured live, and empty where the answer was learned by asking. See
[Pending Orders](architecture/pending_order_architecture.md).

**inbox** — Three different places, so the bare word is not used alone:
- **import inbox** — the folder a producer's files wait in until an import takes them: `data/raw/`
  for tick exports, `data/raw/signals/` for signal JSONL. An import writes the archive from it and
  moves each file it imported to `data/finished/`; a file it refused, or skipped as a duplicate,
  stays where it is. The import screen's first step scans both. See [Data Import
  Pipeline](data_pipeline/data_import_pipeline.md#the-import-screen-its-log-and-its-estimate).
- **signal inbox** (`SignalInbox`) — the buffer between a signal stream's transport thread and the
  loop: it holds the snapshots that arrived until the loop takes them. See [Signal Data
  Source](data_pipeline/signal_data_source.md).
- **executor inbox** — where a session's broker answers (acknowledgements, fills, query results)
  wait until the executor drains them, on every pass of the loop, tick and heartbeat alike. See
  [Live Execution](architecture/live_execution_architecture.md).

**index** — A derived single file, `<store>_index.parquet`, at the root of its store and rebuilt
from the store's own entries. See [Data Storage Layout](architecture/data_storage_layout.md).

**initiator** (`initiator`) — Who ended an order that did not fill — `strategy`, `framework` or
`venue` — and, on a cancel request in the order-event stream, who asked for it. See *end reason*.

**journal · ledger · closing** — The three bookkeeping levels. The *journal* is every booking in
order (the trade records); the *ledger* holds the period summaries (one row per booking period and
currency — the results table across runs, one fragment per run); the *closing* is the figures over
many periods, such as a deployment's total or a month's drawdown. Sealing one booking period is
*closing a period*. See [Accounting Periods](architecture/accounting_periods.md).

**layer** — Never used alone. A configuration value is *layered* by its cascade (see *cascade*);
the *execution layer* is the code that turns an order into a fill — the executors, the portfolio and
the pending orders; and a *drawing layer* is one switchable set of drawings on a run's chart — the
framework's, the algo's (its decision logic's own drawings) or one worker instance's (#379) — the
sense of the viewer's Layers panel.

**live** — Never used alone for a kind of run. In code identifiers — `LiveTradeExecutor`,
`ExecutorMode.LIVE`, `live_types/` — it names the live execution stack that every AutoTrader
session runs, mock sessions included. For the kinds of run say *AutoTrader session*, *live-adapter
session* or *real-money session*; for the other senses see *compute basis*, *live transport*,
*stream state* and *live telemetry*. A signal's `data_origin` is `live` when the producer's output
was real and `synthetic` when it was generated — not the *data origin* entry, which names the
class of instance that produced an archived file.

**live-adapter session** — An AutoTrader session on a real venue's adapter: a dry run or a
real-money session.

**live telemetry** — The real-time view of a run while it runs, in both pipelines: the progress
display of a backtest batch and the dashboard of an AutoTrader session, fed by the stats a run
exports as it goes. Here *live* means "as it happens", which is why the code under this name
(`live_progress_display`, `live_stats_*`, `process_live_export`) belongs to every kind of run. See
[Live Telemetry](architecture/live_telemetry_architecture.md).

**live trading** — A real-money session. Nothing else is called live trading.

**live transport** — Signals arriving over the network from the producer, as opposed to a mounted
archive. See [Signal Data Source](data_pipeline/signal_data_source.md).

**liveness ping** — The ping an AutoTrader session sends a monitoring service every interval while
its tick loop is still passing; the monitor raises the alarm when pings stop. It vouches for the
LOOP, not the process, so a hung session goes silent too — and for one bot: each bot pings a check
of its own. Not the *heartbeat*: that is the tick
loop's idle pass when no tick arrives. See [Unattended Operation](autotrader/unattended_operation.md).

**log warning count** (`log_warning_count`) — The WARNING records in a run's log pot, Tier 2 —
ignorable by design. Not the *warning count*. See [Warnings & Errors](architecture/warnings_errors_tiers.md).

**lost request** (`lost_request` on an order event) — Which request's answer an `unresolved` or
`resolved` event is about: `submit`, `cancel`, `modify`, or `status_read` — the read with which an
order that waited too long for its fill is asked about. See [Order
events](consumer/order-events.md).

**mark** — Never used alone. A *valuation mark* is what an open position is worth at the price
it is marked at — an `unrealized_pnl` is a valuation mark, not a result (see *plane*: the
valuation plane). A deployment's *change marks* say what changed from one session to the next: the
strategy, the operation, or where the orders went. A *chart mark* is something drawn on a run's
chart: the framework's trades and levels, read from the record routes, or an algo's or a worker's
drawing (#379).

**market clock** — Not a term here: see *canonical clock* for the clock a decision reads, and
*tick timespan* for the market time a unit processed.

**mock session** — An AutoTrader session with `adapter_type: mock`: it replays its
`scenario_settings` window from the archive through the whole AutoTrader stack against the
`MockBrokerAdapter`, places nothing, and is reported like every AutoTrader session. See
[Mock Adapter](architecture/mock_adapter_guide.md).

**modelled spread** (`modelled_spread_cost`, `modelled_spread_factor`) — The spread a backtest charges
where the data recorded no quote: such a tick is given bid and ask half the spread estimated
for the previous day below and above its traded price, times the factor, and a fill against it
crosses that spread. Reported as its own figure beside `spread_cost`, which stays what was
measured against a recorded quote — the two add up. Not *synthetic*, which here means generated
data. See [Discovery System](discovery_system.md).

**modify · amend** — *Modify* is this project's word for changing a working order's price or
size: `modify_requested`, `modified`, `modify_rejected`, and `modify` as a lost request. *Amend* is
one venue's name for the same request and stays inside that venue's adapter.

**never confirmed** (`total_never_confirmed`, `never_confirmed_orders` on the pending-orders
report) — A submission the venue never confirmed: `undelivered` (it confirmed it never received the
order) or `unaccounted` (we stopped asking, and it may still hold the order — the case to check by
hand). Live only: a simulated venue answers every order.

**observation** — A dry run whose profile pins `dry_run: true`, kept in
`configs/autotrader_profiles/observation/`.

**one-off** — A session that belongs to no deployment.

**opening equity** (`opening_equity`) — The account value a booking period opened with: the
previous period's close, read at the same instant, or a unit's first observed value. Stamped, never
`final_equity − net_pnl`, which would drop what was still open. See
[Accounting Periods](architecture/accounting_periods.md).

**operator alert** — A message an AutoTrader session sends its operator out of band, over Telegram:
a start that failed or was refused, an abort, an emergency stop, the breaker, stale inputs, a
stalled loop, the session's end. Always *operator* alert — `AwarenessLevel.ALERT` is a display
severity, `alert_only` a reconciliation mode, and `on_give_up: alert` a connection's give-up rule
(log an error and go on — for the alert channel's own ladder, the one message is dropped); none of
them sends anything. A symptom is one *alert episode*. See
[Unattended Operation](autotrader/unattended_operation.md).

**order event** · **order-event stream** — One step in an order's life — submitted, accepted,
triggered, a cancel or modification asked for and how it was answered, an answer lost and the
asking that settled it, the fill or the ending — and the run's file that holds them,
`io/order_events.jsonl`: written as the steps happen in a live session, with the report in a
backtest. The *order history* keeps a row for each submission and for each way an order ended;
the stream keeps every step. See [Order-Event Stream](architecture/order_event_stream.md).

**order history** — The run's order-lifecycle records: one row per EVENT of an order, not one per
order. An order appears as `pending` when it enters the pipeline and once more for the way it ended
— `executed` when it fills, else `denied`, `rejected`, `cancelled`, `expired`, `undelivered` or
`unaccounted` — and a `close` row per close; a refused order states its side and symbol like any
other row. Rows are in the order they happened within their unit, and that position is their identity —
the order id repeats.

**order id** (`order_id` on an order-history row) — Not an order's own id: the id of the POSITION
the order belongs to, minted when the position opens and carried by every later order of it, so
it repeats across that position's rows. An open the run refused still consumed its number, so no
id is used twice within a run unit. A close refused before it was sent says `close_<position id>`,
a guard refusal `guard_…`. With its `scenario_name` it names the same position as a trade's
`position_id`.

**order type** (`order_type`) — How an order is to execute: `market`, `limit`, `stop`, `stop_limit`,
`trailing_stop`, `iceberg` — the type it was ASKED as, kept after a stop triggered. `unknown` is a
resting order the venue reports under a type this project cannot name: reported, with no prices,
and never acted on. What a venue offers of the types is its order capabilities.

**orders to** (`orders_to`) — Where a run's orders went: `simulated` or `venue` — and `venue` is
the only value that means real money. Recorded on every run header and every ledger row; a
deployment lists the values of its sessions, and both together mean it mixed a rehearsal with real
money. See [Introduction](introduction_to_the_ide.md#the-kinds-of-run).

**origin** (`origin`) — Who started a run or an activity: how (`cli`, `sweep`, `direct`, `api`),
which client, for which account and on which installation — the block every run header records at
its start, served on the run list and the activity list as `origin_channel`, `origin_client`,
`origin_principal` and `origin_host`. The word
keeps this one meaning: the configuration directory row's `origin` (where a file lives), a
feed-health episode's `origin` (real or stress-injected) and the `origin_classes` /
`origin_evidence_grades` of a run's data (see *data origin*) are being renamed (#581, #582). See
[Run Origin & Code Identity](architecture/run_origin_and_code_identity.md).

**paper** — The planned name for a dry run, not yet a configuration value (#304).

**partial** — Never used alone. *Partial close*: part of a position was closed. *Partial fill*: part
of an order was executed. *Partially failed batch*: some scenarios of a backtest failed. *Partial
envelope*: the signal producer answered for some symbols only.

**passage** — In the documentation search, one chunk of a served document: the text under one
heading, which is what a hit names and what the ranking scores. Deliberately not *section*, which
in this project means one section of a run report. See
[Reading the documentation from the API](consumer/docs.md).

**pending order** — Any order that is not yet finished. Its phases: *in flight*, then *resting* —
and while a modify or a cancel of a resting order is on its way, that operation is in flight too.
See [Pending Orders](architecture/pending_order_architecture.md).

**pending-order counters** (`total_submitted`, `total_accepted`, `total_rejected`,
`total_never_confirmed`, `total_expired` on the pending-orders report) — How a unit's submissions
left their *in-flight* phase, derived from its order-event stream: per submission, the first word
from the venue — it took the order, refused it, never confirmed it, or the data's end met it on
its way. The four add up to `total_submitted`. An order a previous session sent is *adopted*, not
submitted, and is not counted. Not a fill count: an accepted limit order may still rest, expire or
be cancelled. See [Pending orders](consumer/pending-orders.md).

**plane** — Never used alone. *Strategy plane* and *valuation plane*: which price a site reads on
a venue with a spread — the traded price for bars and decisions, the mid for equity and risk (see
[Market Model](architecture/market_model.md)). *Record plane* (`record_plane` on an order
event): whose account a record is — `bot`, what this process did and was told, or `broker_truth`,
what the venue reported when it was asked.

**pressure level** (`pressure_level`) — How close the machine's memory is to the limit that binds
it: `ok`, `warn` from one threshold, `critical` from the next. Memory alone is graded — a busy
processor is the machine doing its work, while memory that runs out is the moment the operating
system ends a process. Not a log level, and not the machine's *host* identity (see
[Asking the server about itself](consumer/server.md)).

**price · mid · last** — `tick.price` is what the market trades at: the traded price where the
venue prints one, else the mid. The *mid* is `(bid + ask) / 2`; *last* is the traded price, absent
(never zero) where the venue prints none. Strategies read the price, valuation reads the mid. See
[Market Model](architecture/market_model.md).

**price basis** — Which price a bar was rendered from, stamped on the bar file: the traded price
for an order-driven venue, the mid for a quote-driven one. See [Market Model](architecture/market_model.md).

**price formation** — How a venue forms its prices: `order_driven` (a central order book, every
trade prints) or `quote_driven` (a dealer quotes both sides, nothing prints). See
[Market Model](architecture/market_model.md).

**principal** (`origin.principal`, served as `origin_principal`) — On whose behalf a run was
started: `operator`, the console's own principal, or the account an API token acts for — which may
be a person or a service. Not *person*, which in the account vocabulary is a KIND of account, and
not *account* in this glossary's sense, a run unit's balance. See *origin*.

**profile** — Never used alone outside the AutoTrader context. *AutoTrader profile*: one bot's
configuration file. *Generator profile*: a set of generated scenario windows, run by a *profile
run*. *Volatility profile*: a discovery cache of volatility statistics. *Profiling*: measuring
where tick-processing time goes.

**public trade** (`public_trade_id`) — One print of the venue's market, as its public trade channel
reports it, numbered by the venue with ids rising per pair — not every id becomes a public trade
(Kraken's `trade_id`; the tick archive's
`trade_id` column, from collector format 1.7.0; the tick index's `trade_id_gaps`). Not a *trade*
(one close of our own position) and not the `trade_id` of one of OUR executions on an order event
or an execution row. A live session records the public trade ids around each outage of its tick
stream, never on a tick. See [AutoTrader Data Intake](autotrader/autotrader_data_intake.md).

**real-money session** — A live-adapter session with `dry_run` false: its orders are placed at the
venue and move the account.

**recording** (`recording` on the latest-frame route and on the run list) — Whether a session is
still recording its *state frames*, as the route judges it when it answers — never a yes or no:
`current` when the latest frame is younger than three of its cadences, `stopped` when it is older
and not the last one (the session hangs or its process died; the activity list tells which),
`ended` when the latest frame is the one written at the session's end. Null for a session replaying
recorded data before its end, whose frames come at replay speed. The run list carries the same
verdict on every row that records state frames. Not the staleness contract's `stale`, which judges
the session's INPUTS and travels inside the frame. See [state frames](consumer/state-frames.md).

**refused at the first tick** (`refused_at_first_tick`) — A live-adapter session that stopped at its
first tick because its *warmup history* could not be completed there: the venue had not yet
committed the period that ended since the boot read, or could not be read again. A refusal of the
SESSION, never of an order — no order is *denied* or *rejected* by it. Graded `failed` like any
emergency. The tick's workers and decision did not run, but the executor's stop and take-profit
check of that tick may have sent a close. Not a start refused before the loop — by the cold start
or the Field Study's preflight: that one is graded by what it logged, and its operator alert reads
STARTUP REFUSED. See [Warmup History](architecture/warmup_history.md).

**rendered configuration** (`rendered_config_id`) — What an AutoTrader session RAN with, as
opposed to the profile file it was GIVEN: the profile with the `app_config.autotrader` layer merged
in, every parameter's schema default filled, and the broker's `market_config.json` entry. Frozen
in the run-config store at the session's start and named by its header. A record of what ran, not
a schema — a later algo version may drop a parameter and the document stays true. See
[Data Storage Layout](architecture/data_storage_layout.md).

**replay** — Feeding archived data through a pipeline in recorded order: a backtest replays its
scenarios, a mock session its window. The producer's *replay window* — envelopes re-sent after a
reconnect — is a different thing.

**report contract** (`report_contract`) — The API contract version a run's reports were written
under, stamped at its start. Compared with the contract the server answers with, it tells an old
run from a wrong one: a figure a later contract added reads `0` or `null` on an older run. See
[Contract Log](consumer/contract-log.md).

**report section** (served as a route segment and in a run's `artifacts`) — One part of a run's
report: `trade-history`, `portfolio`, `booking-periods` and the rest. Which sections a run holds
depends on its pipeline, so `artifacts` is read before a section is asked for. Not to be confused
with a *passage*, the unit the documentation search returns. See
[Reporting Pipeline](architecture/reporting_pipeline.md).

**resolved · unresolved** — Two meanings, told apart by where they stand. On an order event,
`unresolved` is a request whose answer was lost or named nothing, and `resolved` the asking that
settled it — the request is in `lost_request`. On a broker answer, `UNRESOLVED` is a transport
fault: the venue could not be reached, so the answer says nothing about the order.

**resting** — A pending order the venue (or the trade simulator) has accepted and that waits for its
price: a resting limit, a resting stop. See [Pending Orders](architecture/pending_order_architecture.md).

**run** — One execution with one run id and one `header.json`: a backtest, or one AutoTrader
session. See [Batch Data Flow](architecture/batch_data_flow.md).

**run outcome** (`run_outcome`) — How a run ended, graded once by its own pipeline: `success`,
`finished_with_errors` (it completed, but errors were logged), `failed` (units failed, or the session
ended in an emergency) or `crashed` (the process did not complete). The exit code says the same. See
[Warnings & Errors](architecture/warnings_errors_tiers.md).

**run purpose** (`run_purpose`) — What a run is FOR: `regular`, `fixture` or `certificate`,
declared at the top of the configuration it started from — absent means `regular`, and a
configuration the user owns never declares one — and stamped on its header. It never says whether money moved; `orders_to` does. See *fixture*, *certificate run*
and [Run Kinds](consumer/run-kinds.md).

**run type** (`run_type`, served as `group`) — The pipeline that produced a run: `simulation` or
`autotrader`.

**run unit** · **unit name** — What every report section maps over: a scenario in a backtest, the
one session in the AutoTrader. The unit name is the scenario's `scenario_name`, or the profile's
`profile_name`; report rows carry it as `name`, `scenario_name` or `unit_name`, one identity under
three field names. See [API Server](architecture/api_server_architecture.md).

**scenario** · **scenario set** — A scenario is one symbol over one market window with its merged
configuration (`scenario_name` in the file). A scenario set is the file holding a `global` block and
the scenarios (`scenario_set_name`). See [Process Execution](process_execution_guide.md).

**seam** (of data origins) — Where one origin's tick files end and another's begin: Kraken's
published history and our first collected tick, for instance. A gap there takes the origin class of
the file before it. The coverage report words it as "where the external data ends and the production
data begins", and the gaps route serves two different classes in `origin_class` and
`origin_class_after`; "seam" is the documents' word for it. Not the seam of the code — a point where
a later change attaches, such as the hard stop's hook for its latch — which stays inside the code
and its documents.

**security study** — The *acceptance study* that provokes the account's protective rules with real
money — the order guard's refusals and cooldown, the circuit breaker's soft block and hard stop,
the risk baseline across a restart — in a sequence of sessions called *stages*, and certifies each
rule live, offline or as waiting for a margin account. See
[Security Study](tests/security_cert/security_cert_guide.md).

**segment** — In the drawing channel (#379), a price held from one time to another: `from`, `to`,
`price`. A span of time with no price is not a segment. Until contract 12 the API served a booking
period's fields as `segment_*`; they are `period_*` since, and parts of the code still call a period
a segment. Not a *route segment*, one part of a URL path, such as `trade-history` in
`/api/v1/docs/trade-history`.

**seq** · **submitted_seq** (on an order event) — `seq` numbers a run unit's order events and IS the
order of the stream; never sort by time instead, because several steps often carry the same instant.
`submitted_seq` is the `seq` of the submission an event belongs to — of the adoption, for an adopted
order — and is how the steps of one order are grouped, since the *order id* repeats across a
position's orders.

**server clock** (`server_clock`) — The clock a venue stamps its own data with, declared per broker
in `market_config.json` as an IANA zone and the whole hours the server runs ahead of it. The MT5
server is `America/New_York` + 7 — UTC+2 in US winter, UTC+3 in summer — so its times are converted
per tick by that rule, never by one fixed offset. Not the *canonical clock* and not a collector
machine's own clock. See [Data Import Pipeline](data_pipeline/data_import_pipeline.md).

**session** — Never used for a backtest. An *AutoTrader session* is one start of a profile; a
*market session* is Sydney, Tokyo, London or New York; a *console session* (`console_session` on
the activity list) is the terminal session an activity was started from, where an installation
names an environment variable that carries one.

**shared fill** (`shared_by`) — An execution that several trade rows of one unit carry: a partial
close copies the position's entry fills onto every record it produces. `shared_by` counts them
per unit, because two scenarios on one symbol mint the same synthetic ids.

**short** — A direction (a short position), or a data gap under half an hour (a *short gap*).

**signal** — A worker output that feeds a decision. The *SIGNAL worker* is the type that reads
external, pre-collected data such as sentiment. See [Signal Data Source](data_pipeline/signal_data_source.md).

**signal basis** — A signal row's quality grade from the producer (`llm`, `no_data`, `degraded`).

**snapshot** — Several meanings, each qualified. A *snapshot member* is an *entry member*'s shape:
a file that says what is true now and is replaced whole, never appended to — a run's header, a
session's warmup history. A *signal snapshot* is one archived line of a signal source: the
producer's envelope with the stamp of its receipt. A *broker-truth snapshot* is what one read of
the venue returned: its open orders and positions. The *config snapshot* names the configuration
file a run started from.

**state frame** (`io/state_frames.jsonl`, `state_frames` in a profile) — What an AutoTrader
session held and decided at one moment: its account, its open positions and resting orders, every
worker output and its decision. Written on a cadence of the canonical clock, when a bar closes,
when the decision sends an order, and once at the end (#579) — the live side of a comparison with
a backtest, which unlike the backtest cannot be run again. Its latest frame is served with its
age while the session runs (#611, see *recording*). Not the dashboard's *frame*, which is built only
while a display runs and keeps only the latest. See
[Live Telemetry](architecture/live_telemetry_architecture.md#the-recorded-state-frame--not-this-stream).

**stop trigger** (`trigger_price` on an order event, `stop_price` on a venue order) — The price at
which a stop order activates. A triggered stop fills at the market, not at its trigger — the
distance the price moved through it is real cost; a stop-limit becomes a limit at its limit price.
The `triggered` order event records the moment, in a backtest only: a live venue does not report
it.

**store** — One registered place where persistent bytes live, of exactly one kind: *record*,
*carry-over*, *archive*, *derived* or *special*. Where one entry is a directory of files — a run,
a sweep — the store also declares each file it may hold (*entry member*). `store_cli.py catalog`
lists them. See
[Data Storage Layout](architecture/data_storage_layout.md).

**strategy** — A decision logic with its workers and all their parameters: exactly what
`strategy_config` holds. A *bot* is a strategy bound to one AutoTrader profile.

**stream files** (`stream_files` on the run list) — The files a run keeps beside its report
artifacts and that the API serves while it runs — such as the order-event stream, which a live
session writes as it runs and a backtest with its report, or a session's state frames. Kept apart
from `artifacts`, whose emptiness marks a run that never reached its report. A session's other
records — its warmup history, its tick stream record — are neither: listed nowhere on the run list.
The run store declares which of its *entry members* are stream files. Not the producer's stream
(see *stream state*).

**stream state** — The producer's stream: `replay` while missed envelopes catch up, `live` once
they arrive as they happen.

**sweep** — A parameter search that runs one backtest per combination, each naming the sweep as
its parent. Not itself a run. See [Parameter Optimization](architecture/parameter_optimization_system.md).

**tick source** — The AutoTrader component that feeds ticks into the loop, chosen by
`tick_source.type`: the replaying `mock` source or a venue feed.

**tick timespan** — The market time a unit actually processed, from its first to its last processed
tick (`tick_timespan_seconds`). MEASURED, where the *data window* is DECLARED: a tick-limited
scenario or a quiet market ends earlier than its window. On a run it is the units' spans COVERED
together — a stretch two scenarios share counts once — and `tick_timespan_total_seconds` is their
sum, the work.

**ticks from** (`ticks_from`) — Where a run's ticks came from: `archive` or `venue`. Recorded on
every run header. See [Introduction](introduction_to_the_ide.md#the-kinds-of-run).

**time basis** — What a bar's time stamp marks: the open of its period.

**total final equity** (`total_final_equity`) — The closing equities of several *accounts* added
up, beside `total_initial_balance`: a total no single account held. On a run with one account it
equals that account's `final_equity`.

**trade** — One close of a position, as the trade history records it: a full close books one, each
partial close one more, so a trade is not its position. Its key is the unit, the position and the
tick it closed on (`scenario_name`, `position_id`, `exit_tick_index`). The order history's `close`
row and the trade are made in the same step; the row does not carry the trade's tick yet.

**trade window basis** — Whether a trade is placed in a window by its entry or its exit.

**ts_init** (on an order event) — When this process saw the step, on the *wall clock*. Live only;
empty in a backtest, which has to come out the same every time it runs. Kept beside the step's own
time on the *canonical clock*, never in its place.

**unaccounted** (an order status and an order event) — We stopped asking about an order and do not
know how it ended: its answer was lost and the venue was asked until the resolution ran out, or the
session ended with it unconfirmed. The venue may still hold it, filled or not. Never a refusal.

**undelivered** (an order status and an order event) — The venue confirms it never received the
order: its answer was lost, and asking by our own key after the venue's records had settled found
nothing. Never a refusal — the venue refused nothing.

**venue** — The real marketplace behind a broker entry: Kraken, an MT5 broker.

**venue account** — The account as its venue reported it to a live session: what it held at the
session's start and at its end — open orders and positions counted, its balances as they came — and
the reconciliation lines between, derived from the *broker truth* and served as the
`venue-account` section. Not the session's own books, which are its *account*. See
[Venue account](consumer/venue-account.md).

**wall clock** — The machine's clock. It stamps when WE did or saw something (`ts_init`, a run's
`start_time`) and measures durations — on its monotonic form, because the wall clock itself can
step backwards. Never the time a decision reads — that is the *canonical clock*. Where the one
enters the other is in
[Live Execution](architecture/live_execution_architecture.md#canonical-clock--idle-cadence-360).

**warmup history** — The bars of each timeframe a run hands its workers before their first tick:
the last N bars *complete at* that tick, N being what the most demanding worker reading the
timeframe requires. A backtest and a mock session select it from the archive, a live-adapter
session reads it from the venue. Not the *warmup requirement* a worker declares (that N), and not
the history a recursive average needs before it forgets its seed. See
[Warmup History](architecture/warmup_history.md).

**warning count** (`warning_count`) — A run's Tier-1 findings: advisories a validator decided.
Not the log's warnings, which are its *log warning count*. See
[Warnings & Errors](architecture/warnings_errors_tiers.md).

**winning trade** · **losing trade** — A trade whose realised net P&L is above zero, or below
it. A trade that realised exactly nothing is neither, so it counts toward `total_trades` and toward
no side: `win_rate` is winners over all trades, and `avg_loss` divides by the losers alone.

**worker** — A class, one per file, that computes named outputs every tick: an INDICATOR from bars
and ticks, a SIGNAL from pre-collected external data. See [Worker Naming](user_guides/worker_naming_doc.md).

**worker instance** — One named use of a worker in a strategy: `worker_instances` maps the
instance name (`rsi_fast`) to its type string (`CORE/rsi`), and the instance's parameters sit under
`workers.<instance name>`. One strategy may use the same worker type twice under two names. See
[Worker Naming](user_guides/worker_naming_doc.md).

**worker/decision tracking** (`worker_decision_tracked`; the switch is
`performance_tracking.worker_decision_tracking`) — Whether a run unit timed its workers and counted
its decisions. Off by default in a backtest, because the tracker sits on the hot path. Untracked,
those counters are null in every report — the logic still decided on every tick, nobody counted
it — while the logic's name and the processed ticks are known either way.
