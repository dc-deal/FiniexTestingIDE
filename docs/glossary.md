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

**adapter** (broker adapter) — The code that speaks one venue's API behind one interface the rest
of the framework uses. Chosen by `adapter_type`: `mock` for the broker-neutral `MockBrokerAdapter`,
`live` for a real venue's adapter. See [Adapter Development](user_guides/adapter/adapter_development_guide.md).

**algo** — The user-authored code of a strategy, kept in `user_algos/`. See
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
trades with, assembled from the broker's files at its start: for a live Kraken session the
runtime cache's specs, the seed's fees and the detected fee tier. `config_hash` digests it; an
AutoTrader session also freezes the content in the run-config store and names it as
`broker_config_id` in its broker section. See [Data Storage Layout](architecture/data_storage_layout.md).

**cache** — A derived store of computed results. Deleting it loses nothing; a "cache" whose
deletion loses data is misfiled. See [Data Storage Layout](architecture/data_storage_layout.md).

**canonical clock** — The one clock a decision logic, a worker and the execution layer read:
`get_current_time()`. In a backtest and a mock session it is the time of the replayed tick; in a
live-adapter session the time of the event being processed. It is market time, never the machine's
clock — see *wall clock*.

**carry-over** — What one session hands the next session of the same bot: the algo's memory, and
the framework's record of keys, positions and the risk baseline. Keyed by the bot, overwritten, and
never kept per run. See [Data Storage Layout](architecture/data_storage_layout.md).

**cascade** — How a configuration value is layered. For a scenario: application defaults, then the
set's `global` block, then the scenario. For an AutoTrader profile: `app_config.autotrader`
defaults under the profile. The `user_configs/` override is a separate mechanism, not a level.
See [Config Cascade](config_cascade_guide.md).

**combination** — One point of a sweep's parameter grid, run as one ordinary backtest. See
[Parameter Optimization](architecture/parameter_optimization_system.md).

**compute basis** — When a worker recomputes: `live` on every tick (the forming bar included) or
`bar_close` only when a bar closes. Not a kind of run. See [Worker Naming](user_guides/worker_naming_doc.md).

**config snapshot** — The FILE NAME of the configuration a run started from, recorded in its
header (`config_snapshot`). The full strategy configuration a ledger row carries is a different
field, `strategy_config_json`; the stored copy served at `/reports/runs/{run_id}/config` is the
*stored configuration*. See [Data Storage Layout](architecture/data_storage_layout.md).

**continuous** — Never used alone. *Continuous mode*: a generator strategy that makes one window
per continuous data region. *Continuous data region*: a stretch of ticks with no interrupting gap.
*Continuous deployment*: the sessions of a bot declared `deployment.continuous: true` — in prose
simply a *deployment*. `sources.<id>.continuous` in `sentiment_config.json`: a signal source that
runs around the clock. See [Scenario Generator](generator/generator_block_splitting_architecture.md).

**CORE / type string** — How a configuration names a worker or a decision logic: `CORE/<name>`
resolves against the framework's registry, anything else is a file path. The former `USER/`
namespace no longer exists. See [Worker Naming](user_guides/worker_naming_doc.md).

**data broker** (`data_broker_type`) — The broker whose tick archive a scenario or a mock session
reads, and the key the bar routes are addressed by. A backtest simulates its orders against the
same broker's configuration; a mock session may trade against another one (`broker_type`); a venue
session reads its own broker's feed. In a report it is each unit's `data_broker_type`; in the
configuration directory, a row's `data_broker_types`.

**data origin** — Which kind of source produced an archived file: `origin_class` (`production`,
`development`, `unknown`) with its evidence grade, stamped at import and carried into every run's
consumption record. Not the *broker*. See [Data Provenance](architecture/data_provenance.md).

**data source** — One of the inputs a scenario reads, named by its key: its *data broker* for the
ticks, its `data_sentiment_type` for the signals. A stale-data stress event names the one it makes
stale in `stale_data_source`. See [Stress Test](stress_test.md).

**data window** — The market window one run unit was declared to cover (`data_windows` on the run
header): a start, and an end unless it is open. Not a span over several units.

**decision basis** — The fresh / stale / blind counts of the inputs a decision was made on. See
[Signal Data Source](data_pipeline/signal_data_source.md).

**decision logic** — The one class that turns worker outputs into decisions. See
[Quickstart](user_guides/quickstart_guide.md).

**deployment** — The sessions of one bot joined into one history by a `deployment_id`, because its
profile declares `deployment.continuous: true`. Not itself a run. See
[Deployment Ledger](user_guides/deployment_ledger_guide.md).

**dry run** — An AutoTrader session on a live adapter whose `dry_run` resolves true: real ticks,
every order validated by the venue and never placed, fills simulated locally. A mock session is not
called a dry run, although the `dry_run` flag reads true for it too. To be renamed *paper* (#304).
See [AutoTrader Configuration](autotrader/autotrader_configuration.md).

**error count** (`error_count`) — The ERROR records in a run's error pot. A unit that failed
without logging an error shows in the *run outcome*, not here. See
[Warnings & Errors](architecture/warnings_errors_tiers.md).

**event time** (`event_time` on an order-history row) — When that row's event happened, on the
run's *canonical clock*: the fill on an `executed` row, the refusal on a `rejected` one, the expiry
on an `expired` one; null on a `pending` row. A point in time — not the *execution time*, which is
a duration.

**execution time** — How long a run or one of its units took on the *wall clock*:
`execution_time_ms` for a scenario, `execution_time_s` for a whole backtest run. It says nothing
about how much market time was processed — that is the *tick timespan* — and it is never a point
in time: when an order's event happened is its *event time*.

**fees charged** · **total fees** (`fees_charged`, `total_fees`) — Two fee totals of one run.
*Total fees* are the fees of the trades it CLOSED — the population every trade row, booking period
and trade analytic sums. *Fees charged* are everything the run charged, entry and exit fees and
swap on every position, open ones included. They differ by the fees of what is still open.

**field study** — The real-money acceptance test of the live execution stack, whose record becomes
a release certificate. See [Field Study](tests/live_field_study/field_study_guide.md).

**fragment** — One ledger file, written per run. Nothing else is called a fragment.

**in flight** — A pending order that was sent and not yet acknowledged: in a backtest the latency
simulator still holds it, in a session the venue has not yet answered. See
[Pending Orders](architecture/pending_order_architecture.md).

**index** — A derived single file, `<store>_index.parquet`, at the root of its store and rebuilt
from the store's own entries. See [Data Storage Layout](architecture/data_storage_layout.md).

**journal · ledger · closing** — The three bookkeeping levels. The *journal* is every booking in
order (the trade records); the *ledger* holds the period summaries (one row per booking period and
currency — the results table across runs, one fragment per run); the *closing* is the figures over
many periods, such as a deployment's total or a month's drawdown. Sealing one booking period is
*closing a period*. See [Accounting Periods](architecture/accounting_periods.md).

**live** — Never used alone for a kind of run. In code identifiers — `LiveTradeExecutor`,
`ExecutorMode.LIVE`, `live_types/` — it names the live execution stack that every AutoTrader
session runs, mock sessions included. For the kinds of run say *AutoTrader session*, *live-adapter
session* or *real-money session*; for the other senses see *compute basis*, *live transport*,
*stream state*, *data origin* and *live telemetry*.

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

**log warning count** (`log_warning_count`) — The WARNING records in a run's log pot, Tier 2 —
ignorable by design. Not the *warning count*. See [Warnings & Errors](architecture/warnings_errors_tiers.md).

**market clock** — Not a term here: see *canonical clock* for the clock a decision reads, and
*tick timespan* for the market time a unit processed.

**mock session** — An AutoTrader session with `adapter_type: mock`: it replays its
`scenario_settings` window from the archive through the whole AutoTrader stack against the
`MockBrokerAdapter`, places nothing, and is reported like every AutoTrader session. See
[Mock Adapter](architecture/mock_adapter_guide.md).

**observation** — A dry run whose profile pins `dry_run: true`, kept in
`configs/autotrader_profiles/observation/`.

**one-off** — A session that belongs to no deployment.

**opening equity** (`opening_equity`) — The account value a booking period opened with: the
previous period's close, read at the same instant, or a unit's first observed value. Stamped, never
`final_equity − net_pnl`, which would drop what was still open. See
[Accounting Periods](architecture/accounting_periods.md).

**order history** — The run's order-lifecycle records: one row per EVENT of an order, not one per
order. An order appears as `pending` when it enters the pipeline, `executed` when it fills, and a
`close` row per close; a refused order as `rejected`, stating its side and symbol like any other
row. Rows are in the order they happened within their unit, and that position is their identity —
the order id repeats.

**orders to** (`orders_to`) — Where a run's orders went: `simulated` or `venue`. Recorded on every
run header. See [Introduction](introduction_to_the_ide.md#the-kinds-of-run).

**paper** — The planned name for a dry run, not yet a configuration value (#304).

**partial** — Never used alone. *Partial close*: part of a position was closed. *Partial fill*: part
of an order was executed. *Partially failed batch*: some scenarios of a backtest failed. *Partial
envelope*: the signal producer answered for some symbols only.

**pending order** — Any order that is not yet finished. Its phases: *in flight*, then *resting* —
and while a modify or a cancel of a resting order is on its way, that operation is in flight too.
See [Pending Orders](architecture/pending_order_architecture.md).

**price · mid · last** — `tick.price` is what the market trades at: the traded price where the
venue prints one, else the mid. The *mid* is `(bid + ask) / 2`; *last* is the traded price, absent
(never zero) where the venue prints none. Strategies read the price, valuation reads the mid. See
[Market Model](architecture/market_model.md).

**price basis** — Which price a bar was rendered from, stamped on the bar file: the traded price
for an order-driven venue, the mid for a quote-driven one. See [Market Model](architecture/market_model.md).

**price formation** — How a venue forms its prices: `order_driven` (a central order book, every
trade prints) or `quote_driven` (a dealer quotes both sides, nothing prints). See
[Market Model](architecture/market_model.md).

**profile** — Never used alone outside the AutoTrader context. *AutoTrader profile*: one bot's
configuration file. *Generator profile*: a set of generated scenario windows, run by a *profile
run*. *Volatility profile*: a discovery cache of volatility statistics. *Profiling*: measuring
where tick-processing time goes.

**real-money session** — A live-adapter session with `dry_run` false: its orders are placed at the
venue and move the account.

**rendered configuration** (`rendered_config_id`) — What an AutoTrader session RAN with, as
opposed to the profile file it was GIVEN: the profile with the `app_config.autotrader` layer merged
in, every parameter's schema default filled, and the broker's `market_config.json` entry. Frozen
in the run-config store at the session's start and named by its header. A record of what ran, not
a schema — a later algo version may drop a parameter and the document stays true. See
[Data Storage Layout](architecture/data_storage_layout.md).

**replay** — Feeding archived data through a pipeline in recorded order: a backtest replays its
scenarios, a mock session its window. The producer's *replay window* — envelopes re-sent after a
reconnect — is a different thing.

**resting** — A pending order the venue (or the trade simulator) has accepted and that waits for its
price: a resting limit, a resting stop. See [Pending Orders](architecture/pending_order_architecture.md).

**run** — One execution with one run id and one `header.json`: a backtest, or one AutoTrader
session. See [Batch Data Flow](architecture/batch_data_flow.md).

**run outcome** (`run_outcome`) — How a run ended, graded once by its own pipeline: `success`,
`finished_with_errors` (it completed, but errors were logged), `failed` (units failed, or the session
ended in an emergency) or `crashed` (the process did not complete). The exit code says the same. See
[Warnings & Errors](architecture/warnings_errors_tiers.md).

**run type** (`run_type`, served as `group`) — The pipeline that produced a run: `simulation` or
`autotrader`.

**run unit** · **unit name** — What every report section maps over: a scenario in a backtest, the
one session in the AutoTrader. The unit name is the scenario's `scenario_name`, or the profile's
`profile_name`; report rows carry it as `name`, `scenario_name` or `unit_name`, one identity under
three field names. See [API Server](architecture/api_server_architecture.md).

**scenario** · **scenario set** — A scenario is one symbol over one market window with its merged
configuration (`scenario_name` in the file). A scenario set is the file holding a `global` block and
the scenarios (`scenario_set_name`). See [Process Execution](process_execution_guide.md).

**session** — Never used for a backtest. An *AutoTrader session* is one start of a profile; a
*market session* is Sydney, Tokyo, London or New York.

**shared fill** (`shared_by`) — An execution that several trade rows of one unit carry: a partial
close copies the position's entry fills onto every record it produces. `shared_by` counts them
per unit, because two scenarios on one symbol mint the same synthetic ids.

**short** — A direction (a short position), or a data gap under half an hour (a *short gap*).

**signal** — A worker output that feeds a decision. The *SIGNAL worker* is the type that reads
external, pre-collected data such as sentiment. See [Signal Data Source](data_pipeline/signal_data_source.md).

**signal basis** — A signal row's quality grade from the producer (`llm`, `no_data`, `degraded`).

**store** — One registered place where persistent bytes live, of exactly one kind: *record*,
*carry-over*, *archive*, *derived* or *special*. `store_cli.py catalog` lists them. See
[Data Storage Layout](architecture/data_storage_layout.md).

**strategy** — A decision logic with its workers and all their parameters: exactly what
`strategy_config` holds. A *bot* is a strategy bound to one AutoTrader profile.

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

**trade window basis** — Whether a trade is placed in a window by its entry or its exit.

**venue** — The real marketplace behind a broker entry: Kraken, an MT5 broker.

**wall clock** — The machine's clock. It stamps when WE did or saw something (`ts_init`, a run's
`start_time`) and measures durations — on its monotonic form, because the wall clock itself can
step backwards. Never the time a decision reads — that is the *canonical clock*.

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
