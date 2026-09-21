"""
Report API types — Pydantic models for the unified reporting pipeline (#391).

The canonical, fully-derived report model the postprocessor produces. The console,
file, and API renderers all consume it, so the data is identical across every
surface. Pydantic (not @dataclass) because the API serializes it directly — same
exception as api_types.py.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field

# The risk baseline is REUSED, not projected. It is already a Pydantic record that describes
# itself — kind, stamp, origin, and on spot the price and quantities its value can be
# re-derived from — and the safety report's job is to surface exactly that record. A parallel
# row type would be a hand-maintained copy of it, and a copy is what silently drops a field.
from python.framework.types.persistence_types import RiskBaseline


class ExecutionRow(BaseModel):
    """One broker execution / fill (#330) — the projection of a BrokerTrade (#393)."""
    trade_id: str
    side: str               # 'buy' | 'sell'
    volume: float
    price: float
    fee: float
    fee_currency: str
    liquidity: str          # 'maker' | 'taker'
    timestamp: str          # ISO-8601 UTC, '' if absent


class TradeHistoryRow(BaseModel):
    """One closed trade, rendered identically to CSV / console / API."""
    position_id: str
    symbol: str
    direction: str          # 'long' | 'short'
    lots: float
    entry_price: float
    entry_time: str         # ISO-8601 UTC
    exit_price: float
    exit_time: str          # ISO-8601 UTC
    duration_s: float
    close_reason: str       # '' (manual) | 'sl_triggered' | 'tp_triggered' | 'scenario_end'
    gross_pnl: float
    total_fees: float
    net_pnl: float
    currency: str = ''      # account currency (#393 — for console P&L formatting)
    swap_cost: float = 0.0  # signed overnight swap for this trade (+ debit, − credit; #365)
    # The two cost columns that never reached a report surface (#244). `commission_cost`
    # now receives maker/taker too, so on an order-driven venue a trade finally shows its
    # charge instead of three zeros beside a real total. `spread_cost` is MEASURED, not
    # charged: it is the effective spread the fills crossed and it is already inside
    # gross_pnl, so it is reported beside total_fees and never inside it. Signed like
    # swap_cost above — a negative value is a price improvement, which is what a resting
    # limit order earns by filling inside the spread.
    commission_cost: float = 0.0
    spread_cost: float = 0.0
    # Trade analytics (#389) — excursion + risk-normalized result (defaulted: additive columns)
    mae_price: float = 0.0      # most adverse price reached while open
    mfe_price: float = 0.0      # most favorable price reached while open
    mae_pnl: float = 0.0        # gross P&L at the worst excursion
    mfe_pnl: float = 0.0        # gross P&L at the best excursion
    # MAE/MFE distance in the symbol's price unit (#167) — exact per-symbol pip_size
    # stamped at the source; price_unit labels it ('pip' on Forex, 'tick' on crypto).
    mae_distance: float = 0.0
    mfe_distance: float = 0.0
    price_unit: str = ''
    r_multiple: float | None = None  # net_pnl / initial_risk; None when no stop loss
    # Full projection (#393) — lets the console audit table render purely from the model
    scenario_name: str = ''     # owning run unit (sim: scenario; live: session) → grouping
    entry_tick_index: int = 0   # chronological sort key within a unit
    exit_tick_index: int = 0
    entry_type: str = ''        # 'market' | 'limit' | 'stop' | 'stop_limit'
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_side: str = ''        # 'buy' | 'sell'
    exit_side: str = ''
    entry_executions: list[ExecutionRow] = []   # #330 per-fill sub-lines (entry)
    exit_executions: list[ExecutionRow] = []    # #330 per-fill sub-lines (exit)
    # Submission-vs-fill slippage (#340) — adverse (>0 = paid worse than submission mid);
    # None when no submission tick was captured (legacy / cleanup-only close).
    entry_slippage: float | None = None
    exit_slippage: float | None = None
    entry_slippage_pct: float | None = None
    exit_slippage_pct: float | None = None


class TradeAnalytics(BaseModel):
    """
    Aggregate trade analytics (#389) for ONE account currency — risk-normalized
    profitability + SL calibration. Per-currency so the P&L-denominated fields
    (MAE/MFE) never mix currencies (#393); R fields are dimensionless anyway.
    """
    currency: str = ''      # account currency this aggregate is over
    trade_count: int = 0    # trades in this currency group
    expectancy: float       # mean R over trades with a defined R
    # None = not measured (no R-defined winner / loser) — never 0.0, which a reader cannot
    # tell from a measured zero. Gate on the counts, not on r_trade_count: a run can have
    # R-defined trades and still no winner among them.
    avg_win_r: float | None     # mean R of winners (R-defined)
    avg_loss_r: float | None    # mean R of losers (R-defined)
    r_trade_count: int      # trades with a defined R (had a stop loss)
    r_win_count: int = 0    # R-defined trades that won
    r_loss_count: int = 0   # R-defined trades that lost
    avg_mae_winners: float  # mean MAE P&L on winners — SL too tight if large vs win size
    avg_mae_losers: float   # mean MAE P&L on losers
    avg_mfe_losers: float   # mean MFE P&L on losers — "left on the table" read
    # Per-currency P&L totals (#393 — the trade-table TOTAL line, model-served for the API)
    gross_pnl: float = 0.0  # Σ gross P&L over the group
    net_pnl: float = 0.0    # Σ net P&L over the group
    total_fees: float = 0.0  # Σ fees over the group
    # The WORST single excursion, beside the means above (#537). A mean says how much heat the
    # average trade took; this says how much the worst one did, which is the figure a stop level
    # is actually judged against. Stored as the P&L magnitude, like its means.
    largest_mae: float = 0.0
    largest_mfe: float = 0.0
    # Mean holding time in seconds. Cheap here, and it is the axis a booking period makes
    # readable at all: a period whose trades average four hours is a different strategy from one
    # whose trades average four days, and net P&L cannot tell them apart.
    avg_trade_duration_s: float = 0.0
    # The longest unbroken run of winners / losers, in REALISATION order.
    #
    # It carries a warning with it: this figure is NOT combinable across groups, and it is the
    # one KPI here where the obvious reduction is wrong. A streak can cross a period boundary, so
    # `max()` of two periods understates the truth — 2 and 3 adjacent segments can be a run of 5.
    # Its ledger column is therefore `DERIVE`, never `MAX`.
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0


class TradeScenarioTotals(BaseModel):
    """Per-scenario trade-table totals (the per-scenario footer) — model-served, no renderer math."""
    scenario_name: str
    currency: str
    trade_count: int
    gross_pnl: float
    net_pnl: float
    total_fees: float
    total_swap: float = 0.0  # Σ signed swap over the scenario (#365)


class RunScopedReport(BaseModel):
    """
    The base of every report a run persists as its own artifact.

    It carries ONE field, and the reason is the whole point: a report body that does not name
    its run cannot be checked against the run that was asked for. Two different sweep
    combinations produce byte-identical portfolio bodies — measured — so a consumer receiving
    the wrong one has nothing to notice it by. The route is not proof; the payload is.

    Inheriting rather than repeating also puts `run_id` FIRST in every serialized artifact,
    where a reader looks for it.
    """
    run_id: str


class TradeHistoryReport(RunScopedReport):
    """The trade-history table + light metadata + per-currency analytics (#389/#393)."""
    trades: list[TradeHistoryRow]
    count: int
    symbols: list[str]      # distinct symbols present (filter UX)
    analytics: list[TradeAnalytics]  # one entry per account currency (no cross-currency mixing)
    scenario_totals: list[TradeScenarioTotals] = []  # per-scenario footer totals (no re-sum)


class OrderHistoryRow(BaseModel):
    """One order-lifecycle record (the resting/filled/rejected order list)."""
    order_id: str
    scenario_name: str = '' # owning run unit (sim: scenario; live: session) — #393 grouping
    position_id: str        # '' if not yet/never tied to a position
    symbol: str
    direction: str          # 'long' | 'short' | '' (unknown)
    action: str             # 'open' | 'close' | '' (unknown)
    status: str             # 'executed' | 'rejected' | 'cancelled' | ...
    requested_lots: float
    executed_lots: float
    executed_price: float
    execution_time: str     # ISO-8601 UTC, '' if never executed
    commission: float
    swap: float
    slippage_points: float
    rejection_reason: str   # '' if not rejected
    rejection_message: str


class OrderHistoryReport(RunScopedReport):
    """The order-history table + light metadata (flat, like trade history)."""
    orders: list[OrderHistoryRow]
    count: int
    symbols: list[str]      # distinct symbols present (filter UX)


class OpenPositionRow(BaseModel):
    """
    One position still OPEN at run end (#492) — the counterpart to ActiveOrderRow.

    The model has always reported the orders a run left standing; it never had to report
    the POSITIONS, because the run end used to flatten them. In live that flatten never
    reached the venue, and in simulation it invented an exit the strategy never chose, so
    both were removed and the position is reported as what it is.

    `unrealized_pnl` is a MARK, not a result: it is what the position was worth at the last
    tick. `valued` says whether there was a tick to value it at all — a boot that aborted
    before the first one carries the entry price and no valuation, never an invented number.

    There is deliberately no `adopted` flag. Whether a position was inherited at boot is
    answered by the cold-start section of the same report, keyed by the same `position_id` —
    and the one derivation available here (an empty `entry_trades`) is a constant False,
    because every position carries its executions, whether it was opened by this run or
    restored from the carry-over.
    """
    position_id: str
    direction: str          # 'long' | 'short'
    lots: float
    entry_price: float
    entry_time: str         # ISO-8601 UTC
    last_price: float = 0.0
    unrealized_pnl: float = 0.0
    valued: bool = False
    stop_loss: float | None = None
    take_profit: float | None = None
    # Who enforces the STOP (#500). 'local' — our own process watches the tick stream and
    # closes on a breach, so a process that dies leaves the position unprotected. 'venue' —
    # the level rests at the broker and outlives us. Empty where no level is set. A level
    # was reported for a long time with nobody behind it; this is what makes the difference
    # readable instead of assumed.
    protective_level_enforcement: str = ''
    # And who enforces the TARGET, which is not the same answer (#503). Only ONE order can
    # rest at the venue — Kraken has neither OCO nor a bracket — and it holds the stop,
    # because a bounded loss is worth more than a captured opportunity. So a position whose
    # stop the venue holds still has a take profit watched by this process alone. One answer
    # across both levels told an operator the target survives a restart, and it does not.
    take_profit_enforcement: str = ''


class PortfolioUnitRow(BaseModel):
    """Headline P&L of one run unit (sim: a scenario; live: the session)."""
    name: str               # scenario name (sim) / profile/session label (live)
    symbol: str
    currency: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    # Ratio 0..1. A 0.0 with total_trades == 0 means NOT MEASURED — the discriminator is
    # total_trades on this same row. Deliberately not widened to None: unlike the R fields,
    # that discriminator is complete, and win_rate is a rankable sweep objective (#390).
    win_rate: float
    # None = undefined: either the run had no losing trade, or it never traded at all.
    # 0.0 therefore always means MEASURED and zero.
    profit_factor: float | None
    total_profit: float
    total_loss: float
    net_profit: float       # total_profit - total_loss
    # ACCOUNT drawdown — the whole account from its high to its low. A TRADE's own
    # excursion is `TradeRecord.mae_pnl` (#389) and is a different number.
    account_max_drawdown: float
    account_max_dd_pct: float = 0.0   # worst decline over the peak it fell FROM, per tick
    # WHICH PERIOD the two figures above describe (#497). A live session inherits its
    # predecessor's curve through the cold-start carry-over, so the drawdown may span a month
    # of restarts — and nothing in the number itself says so. Empty stamp = this session began
    # its own curve, which is always the case in the simulation.
    drawdown_carried_from: str = ''
    drawdown_restarts: int = 0
    drawdown_started_at: str = ''   # when the curve began, not when it was last handed over
    total_fees: float
    # Full projection — the per-scenario linear block renders purely from these (defaulted:
    # additive columns; the per-currency aggregated section stays on PortfolioAggregator).
    data_source: str = ''       # the scenario's data broker type (box line "Data: …")
    sentiment_source: str = ''  # the scenario's data_sentiment_type, if any (#429; box line "· Sentiment: …")
    broker_name: str = ''
    spot_mode: bool = False
    total_long_trades: int = 0
    total_short_trades: int = 0
    max_equity: float = 0.0
    current_balance: float = 0.0
    initial_balance: float = 0.0
    conversion_rate: float | None = None
    # Currency split from the broker config (#265), stamped at capture — a renderer must
    # never split the symbol string itself.
    base_currency: str = ''
    quote_currency: str = ''
    # Spot dual-balance estimate — derived here, never in a renderer
    spot_est_current: float = 0.0
    spot_est_initial: float = 0.0
    spot_est_pnl: float = 0.0
    spot_est_pnl_pct: float = 0.0
    total_spread_cost: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0
    maker_fee: float = 0.0       # spot maker-side fee
    taker_fee: float = 0.0       # spot taker-side fee
    has_error: bool = False     # hybrid unit (partial data + error) → CRITICAL marker
    # Spot mode — dual-balance + estimated portfolio value
    balances: dict[str, float] = {}
    initial_balances: dict[str, float] = {}
    # What unfilled orders still claim, and what is left of the balance after them (#489).
    # `usable` is derived here in the builder, never in a renderer.
    committed_funds: dict[str, float] = {}
    usable_funds: dict[str, float] = {}
    last_price: float = 0.0
    # #492 — what the unit still HELD when it ended. `net_profit` above stays realised;
    # these two are the wealth view and are never summed into it silently.
    open_positions: list[OpenPositionRow] = []
    unrealized_pnl: float = 0.0     # marked at the last tick; 0.0 when nothing was open
    final_equity: float = 0.0       # realised balance + what the open positions are worth
    # Whether `final_equity` is a real mark-to-market. False when something was still open
    # that no tick could price: the figure is then the balance alone, with the holding
    # counted at ZERO — which is the very understatement this section exists to remove, so
    # it must not be presented as a valuation.
    final_equity_valued: bool = True
    # The session-end policy this unit ran under (#492), 'orders/positions'. Empty on sim:
    # a scenario has no policy, its data simply ends.
    session_end_policy: str = ''


class PortfolioAggregateRow(BaseModel):
    """Headline P&L rolled up per account currency (no cross-currency summing)."""
    currency: str
    unit_count: int         # scenarios in this currency group (live: 1)
    total_trades: int
    winning_trades: int
    losing_trades: int
    # Ratio 0..1. A 0.0 with total_trades == 0 means NOT MEASURED — the discriminator is
    # total_trades on this same row. Deliberately not widened to None: unlike the R fields,
    # that discriminator is complete, and win_rate is a rankable sweep objective (#390).
    win_rate: float
    # None = undefined: either the run had no losing trade, or it never traded at all.
    # 0.0 therefore always means MEASURED and zero.
    profit_factor: float | None
    total_profit: float
    total_loss: float
    net_profit: float
    account_max_drawdown: float
    # The peak it fell from and the share it was — taken from the SAME unit as the amount
    # above, never the deepest decline of one scenario over the highest peak of another. That
    # pairing was a real defect (#497) and the rule is the same here as in the console
    # aggregate: amount, percentage and the unit they describe travel together.
    max_equity: float = 0.0
    account_max_dd_pct: float = 0.0
    total_fees: float
    # #492 — the wealth view beside the realised one. Summed across the currency's units,
    # never folded into net_profit.
    unrealized_pnl: float = 0.0
    final_equity: float = 0.0
    open_position_count: int = 0


class PortfolioReport(RunScopedReport):
    """
    Portfolio headline as the unified array model: per-unit rows + per-currency
    roll-up. sim = N units + M currency aggregates; live = 1 unit + 1 aggregate.
    """
    units: list[PortfolioUnitRow]
    aggregates: list[PortfolioAggregateRow]


class ExecutionStatsRow(BaseModel):
    """Order-execution counts of one run unit (sim: a scenario; live: the session)."""
    name: str               # scenario name (sim) / profile/session label (live)
    symbol: str
    orders_sent: int
    orders_executed: int
    orders_rejected: int
    sl_tp_triggered: int    # closes triggered by stop-loss / take-profit


class ExecutionStatsTotals(BaseModel):
    """
    Order counts summed across all units. Counts are currency-agnostic, so this is
    ONE object (no per-currency split, unlike the portfolio roll-up).
    """
    orders_sent: int = 0
    orders_executed: int = 0
    orders_rejected: int = 0
    sl_tp_triggered: int = 0


class ExecutionStatsReport(RunScopedReport):
    """Order-execution counts as the unified array model: per-unit rows + a summed total."""
    units: list[ExecutionStatsRow]
    totals: ExecutionStatsTotals


class ActiveOrderRow(BaseModel):
    """One active (untriggered) limit/stop order at run end."""
    order_id: str
    order_type: str         # 'limit' | 'stop' | 'stop_limit'
    direction: str          # 'long' | 'short'
    lots: float
    entry_price: float      # limit price (LIMIT) / trigger price (STOP/STOP_LIMIT)
    limit_price: float | None = None    # STOP_LIMIT only
    stop_loss: float | None = None
    take_profit: float | None = None


class PendingOrdersUnitRow(BaseModel):
    """Pending-order lifecycle + latency + active orders of one run unit (sim scenario)."""
    name: str               # owning run unit (scenario)
    symbol: str
    total_resolved: int = 0
    total_filled: int = 0
    total_rejected: int = 0
    total_timed_out: int = 0
    total_force_closed: int = 0
    avg_latency_ms: float | None = None
    min_latency_ms: float | None = None
    max_latency_ms: float | None = None
    latency_count: int = 0      # latency samples → weighted avg on aggregation (#397)
    active_limit_orders: list[ActiveOrderRow] = []
    active_stop_orders: list[ActiveOrderRow] = []


class PendingOrdersReport(RunScopedReport):
    """
    Pending-order lifecycle as the unified array model: per-unit rows. Sim-populated
    (the live AutoTraderResult carries no pending stats → empty units live).
    """
    units: list[PendingOrdersUnitRow]


class ScenarioDetailsRow(BaseModel):
    """Per-scenario execution + signal metadata (sim batch — the SCENARIO DETAILS section)."""
    name: str
    symbol: str
    data_source: str = ''           # data broker type ("Symbol: <data_source>/<symbol>")
    # WHICH DATA this one scenario read (#518), beside the broker key it read it from. The
    # ledger records the same answer per RUN; this is the grain that says WHICH scenario, and
    # a set mixing brokers or eras is exactly where the run-level roll-up stops being enough.
    # Distinct, sorted, comma-joined by the shared encoding, so the two can be compared.
    data_format_versions: str = ''
    origin_classes: str = ''
    origin_evidence_grades: str = ''
    # Empty where the scenario mounted no BAR file — the only archive that stamps a basis.
    # Not filled from the broker declaration: that is the borrowing the stamp prevents (§31c).
    price_bases: str = ''
    account_currency: str = ''      # resolved P&L denomination currency
    account_currency_explicit: bool = False  # True when set in config (not auto-derived)
    status: str = 'success'         # 'success' | 'failed' | 'hybrid' (partial + error)
    execution_time_ms: float = 0.0
    ticks_processed: int = 0
    first_tick_time: str = ''       # ISO-8601 UTC, '' if none
    last_tick_time: str = ''
    tick_timespan_seconds: float = 0.0
    buy_signals: int = 0
    sell_signals: int = 0
    flat_signals: int = 0
    trades_requested: int = 0
    worker_count: int = 0
    error_type: str = ''
    error_message: str = ''


class DataSourceRow(BaseModel):
    """
    One data source a run read from, with what that source IS and what was read over it.

    An AGGREGATE and therefore its own stage: it is derived once and serves every surface,
    rather than being rebuilt by whichever renderer happens to want it. The console used to
    group the scenario rows itself AND resolve the market type from a config manager it
    instantiated — two things a PRESENT layer may not do, and the second one silently: config
    answers what a broker is TODAY, so a re-render or a config edit would make the console
    disagree with the artifact beside it.

    `market_type` is resolved ONCE here from its authoritative owner. `price_bases` is NOT —
    it comes from the scenarios' stamps (§31c), because what a file was rendered from and what
    a render would produce today are two questions that disagree for as long as a re-render is
    unfinished.
    """
    broker_type: str
    market_type: str        # resolved once in DERIVE, never in a renderer
    scenario_count: int
    symbols: list[str]      # sorted, distinct
    price_bases: str = ''   # distinct, sorted, comma-joined across this source's scenarios


class ScenarioDetailsReport(RunScopedReport):
    """
    Per-scenario execution/signal metadata (sim-only): one row per scenario, **including
    failed ones** (the section's job is the full scenario status grid).
    """
    units: list[ScenarioDetailsRow]
    # The per-source roll-up over those rows. On the model so the console, the artifact and
    # the API read one derivation instead of three.
    data_sources: list[DataSourceRow] = []


class RunReporting(StrEnum):
    """
    Whether a run was commissioned to produce report artifacts.

    EXPECTED is the default because it is what every real run is; NONE is declared by a caller
    that deliberately runs without a report coordinator — today the simulation test path.
    """
    EXPECTED = 'expected'
    NONE = 'none'


class RunHeader(BaseModel):
    """
    What a run IS — written once, at the run's START, into its own directory.

    The single source of truth for a run's identity. Everything else that answers "which run is
    this" is derived from it: the index, and every consumer that used to reconstruct the answer
    from the directory path. Directories stay human-navigable (§36) but stop carrying meaning.

    Written at the START, not the end, for one reason that decides the design: a run that
    crashes is exactly the run somebody needs to identify. An artifact produced on the way out
    is missing whenever it matters most.

    Args:
        run_id: The run's identity — also its directory name
        start_time: When the run began (UTC, tz-aware)
        run_type: Its category, the same value the API serves as `RunInfo.group`
        run_name: The owning scenario set (sim) or profile (live)
        parent_id: What this run belongs to, or None when it stands alone. Today a sweep's id
            for one of its combinations. Named `parent_id` and not `parent_run_id` on purpose:
            a sweep is NOT itself a run (it has no header — it is defined by the runs naming
            it), while the daily fragments of #476 will point at a parent that IS one. One
            field, two kinds of parent, and the name has to stay true for both
        config_snapshot: File name of the config this run was commissioned with
        app_version: The app version that produced it
        git_commit: The commit it ran from, when the working tree exposes one
        reporting: Whether this run was COMMISSIONED to write report artifacts. Declared at
            the start, by the caller that decides it — not read from config, because there is
            no config for it: the sim test path simply never builds a report coordinator.
            Without this field an empty artifact list means three different things — the run is
            still going, it CRASHED before reporting, or it was never meant to report — and a
            crashed run is then indistinguishable from an intentionally silent one. It also
            makes cleanup decidable: `none` is the machine-checkable statement "there is
            nothing here anyone wants to look at"
    """
    run_id: str
    start_time: datetime
    run_type: str
    run_name: str
    parent_id: Optional[str] = None
    config_snapshot: str = ''
    app_version: str = ''
    git_commit: Optional[str] = None
    reporting: RunReporting = RunReporting.EXPECTED


class RunInfo(BaseModel):
    """One discoverable run in the report store — identity only, no report content."""
    run_id: str
    # The run's TYPE, which is also where its logs live (file_logging.run_logs):
    # 'simulation' (a backtest — standalone, or one combination of a sweep) | 'live' (an
    # AutoTrader session). Nesting is NOT part of the type: `parent_id` carries it.
    group: str
    name: str               # scenario-set name (sim) | profile name (live)
    # Every report artifact this run persisted, by file name — 'portfolio.json',
    # 'trade_history.csv', … The two pipelines produce DIFFERENT sets (a live session has no
    # scenario_details / profiling / run_meta / aggregated_portfolio), so a consumer that
    # guessed would get a 404 for the difference. Empty = the run exists as logs alone, which
    # a test session legitimately does; such a run is listed rather than hidden, because an
    # index that silently omits runs is its own surprise.
    artifacts: list[str] = Field(default_factory=list)
    # Straight from the run's header (#475) — the list answers "what was this run" on its own,
    # instead of making a consumer open each run to find out.
    start_time: str = ''
    # The run this one belongs to: a sweep for one of its combinations, and — once the daily
    # cycle lands (#476) — the session a day fragment was cut from. None means it stands alone.
    parent_id: Optional[str] = None
    app_version: str = ''
    git_commit: Optional[str] = None
    config_snapshot: str = ''
    # Whether the run was COMMISSIONED to report. Read it together with `artifacts`: empty +
    # 'expected' means the run is still going or died before reporting, empty + 'none' means
    # it was never meant to. Without the pair a crashed run looks like a deliberately silent
    # one, and a consumer cannot tell an incomplete run from an intentional one.
    reporting: RunReporting = RunReporting.EXPECTED
    # Bytes on disk, stamped once when the run finished rather than measured when asked.
    # 0 on a row written before the column existed AND on a run still in flight — the two are
    # indistinguishable here on purpose, because both mean "no figure was recorded", and a
    # consumer showing it says UNKNOWN rather than inventing an empty run.
    size_bytes: int = 0

    @computed_field
    @property
    def has_reports(self) -> bool:
        """
        Whether this run persisted any report artifact.

        Derived rather than stored, so it cannot drift from the list it summarises.

        Returns:
            True when the run carries at least one artifact
        """
        return bool(self.artifacts)


class RunListResponse(BaseModel):
    """The run index every other report route is addressed by (#391)."""
    runs: list[RunInfo]
    count: int


class RunSummaryCurrency(BaseModel):
    """
    Run-wide KPIs for ONE account currency (#390 prework). Composed once from the per-section
    aggregates (portfolio roll-up + trade analytics) — never re-derived. Per currency so the
    P&L-denominated fields never mix currencies.
    """
    currency: str
    net_pnl: float          # ← PortfolioAggregateRow.net_profit
    profit_factor: float | None  # ← PortfolioAggregateRow (None = no losing trade)
    win_rate: float         # ← PortfolioAggregateRow.win_rate
    account_max_drawdown: float     # ← PortfolioAggregateRow.account_max_drawdown
    # The peak it fell from and the share it was, from the SAME unit as the amount. The
    # percentage is not derivable from the other two — it was measured against the peak
    # standing at the time — and the ledger is the one record that has to carry it, because
    # a live row is cumulative over its deployment and a reader comparing rows needs both.
    max_equity: float = 0.0
    account_max_dd_pct: float = 0.0
    total_fees: float       # ← PortfolioAggregateRow.total_fees
    # The two halves `profit_factor` is the quotient OF. Carried because a rate cannot be
    # folded out of two rows while its COMPONENTS can be summed on any level: without these,
    # a session's profit factor is not recoverable from its booking segments and a
    # deployment's is not recoverable from its sessions (#537, CLAUDE.md §48). `win_rate`
    # already had this property through winning_trades / total_trades; this gives it to the
    # second rate. Appended, so older fragments read back as 0.0.
    gross_profit: float = 0.0   # ← PortfolioAggregateRow.total_profit (Σ of winning trades)
    gross_loss: float = 0.0     # ← PortfolioAggregateRow.total_loss  (Σ of losing trades, positive)
    total_trades: int
    winning_trades: int
    losing_trades: int
    # #492 — realised and valued, kept apart. A sweep that ranks on net_pnl alone puts a
    # variant still HOLDING a winner below one that closed it, which is the same distortion
    # the force-close used to cause in the other direction.
    unrealized_pnl: float = 0.0
    final_equity: float = 0.0
    open_position_count: int = 0
    expectancy: float       # ← TradeAnalytics.expectancy (mean R) — the sweep objective
    avg_win_r: float | None     # ← TradeAnalytics (None = no R-defined winner)
    avg_loss_r: float | None    # ← TradeAnalytics (None = no R-defined loser)
    r_trade_count: int      # ← TradeAnalytics.r_trade_count
    r_win_count: int = 0
    r_loss_count: int = 0
    # The excursion, duration and streak figures (#537) — the same shape TradeAnalytics computes,
    # carried here so a booking period and a whole run describe themselves with one vocabulary.
    # They are cheap: one pass over the records the analytics already walked.
    avg_mae_winners: float = 0.0
    avg_mae_losers: float = 0.0
    avg_mfe_losers: float = 0.0
    largest_mae: float = 0.0
    largest_mfe: float = 0.0
    avg_trade_duration_s: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0


class BookingPeriodRow(BaseModel):
    """
    One booking period as the report renders it — the Hauptbuch, one line per entry.

    Deliberately NOT the ledger row: this carries what a reader compares down a column, not what
    a ranking needs. The ledger keeps the full figure set; this keeps the ones that make a period
    legible beside its neighbours.
    """
    unit_name: str
    segment_no: int
    opened_at: str
    closed_at: str
    reason: str
    currency: str
    trade_count: int
    net_pnl: float
    total_fees: float
    win_rate: float
    profit_factor: float | None
    final_equity: float
    # The period's OWN band and decline, not the cumulative ones: on this table the question is
    # what each period did, and the running figure would repeat the same number down the column.
    min_equity: float
    max_equity: float
    max_drawdown: float


class BookingPeriodsReport(RunScopedReport):
    """
    A run's booking periods, and whether they add up to the run (#537).

    The last line is the point of the table. A period summary is trusted because it can be
    recomputed from its records, and a column of them is trusted because it RECONCILES against
    the figure the run reports by its own path — so the reconciliation is computed here and
    stated, rather than left to a reader adding up a column by eye.

    `reconciles` false is not an error to raise; it is the finding the table exists to surface
    (§12: reports calculate and render, they do not judge).
    """
    periods: list[BookingPeriodRow] = Field(default_factory=list)
    currency: str = ''
    # The sums over the periods, and what the run reports independently of them.
    total_net_pnl: float = 0.0
    total_fees: float = 0.0
    total_trades: int = 0
    run_net_pnl: float = 0.0
    run_total_trades: int = 0
    reconciles: bool = True
    # The deepest single-period decline and the band across all of them — the column's own
    # extremes, which is what a reader scanning the table is comparing against.
    deepest_period_drawdown: float = 0.0
    final_equity: float = 0.0


class RunSummary(RunScopedReport):
    """
    Cross-section run KPI model (#390 prework): per-currency KPIs (P&L-denominated) + global
    order counts (currency-agnostic). The single object every consumer reads — sweep objective,
    console headline, API, live snapshot, dashboard — composed once off the section aggregates.
    """
    currencies: list[RunSummaryCurrency]
    orders_sent: int = 0
    orders_executed: int = 0
    orders_rejected: int = 0
    sl_tp_triggered: int = 0
    unit_count: int = 0     # sim: N scenarios | live: 1
    # Weakest SIGNAL channel of the run (#433): min fresh ratio over all usages. None = no
    # SIGNAL worker was involved — deliberately NOT 1.0, which would claim a perfect feed.
    # Rides into the run-results ledger so a sweep/robustness ranking carries the data
    # quality its rows were produced under.
    signal_fresh_ratio: float | None = None
    # Feed disturbance totals (#451) — the executive line reads these, it does not re-scan
    # the episodes. Zero everywhere = the run saw no outage in either staleness domain.
    disturbance_episode_count: int = 0
    disturbance_stale_seconds: float = 0.0
    disturbance_source_count: int = 0
    disturbance_stress_injected: int = 0


class RunResultRow(BaseModel):
    """
    One run-results ledger row (#390), typed. The parsed projection of the parquet `LEDGER_COLUMNS`:
    the JSON columns (`worker_versions`, `symbols`, `sweep_params`) are parsed back to structured types
    so the optimization analysis + the (future) API read typed objects, not string-keyed DataFrame cells.
    """
    # Identity + provenance
    # None = written before row versioning existed, i.e. the producing logic is UNKNOWN —
    # never a substituted version, for the same reason `profit_factor` is None rather than
    # 0.0 when undefined. A ranking that mixes versions compares measures that changed (#497).
    logic_version: int | None = None
    param_hash: str
    status: str = 'ok'                           # 'ok' | 'error' (error = no usable data, excluded from ranking)
    error: str | None = None                     # failure reason when status == 'error'
    run_id: str
    run_timestamp: str                          # ISO-8601 UTC (stored verbatim)
    sweep_id: str | None = None
    sweep_params: dict[str, Any] | None = None   # the combination's concrete grid point
    sweep_objective: str | None = None           # the sweep spec's objective (report defaults to it)
    sweep_maximize: bool | None = None           # the sweep spec's rank direction
    scenario_set_name: str = ''
    app_version: str = ''                        # program version; '' = fragment predates the field
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool = False
    decision_logic_type: str = ''
    decision_version: str = ''
    worker_versions: dict[str, str] = {}
    config_snapshot: str = ''                    # full resolved strategy_config (JSON string)
    symbols: list[str] = []
    data_broker_type: str = ''
    # WHICH DATA the row was produced over (#518) and WHICH PRICE its bars were rendered from
    # (§31c). Carried here and not only in the parquet: this model IS the typed read of a
    # ledger row and `_write_csv` builds the optimizer's export from its field list, so a
    # column the model does not declare is written to disk and reaches no reader — Pydantic
    # drops the unknown key without a word. Defaults are '' / 0 so a fragment written before
    # the columns existed still parses.
    input_plane: str = ''
    data_format_versions: str = ''
    origin_classes: str = ''
    origin_evidence_grades: str = ''
    input_files: int | None = None
    unstamped_input_files: int | None = None
    price_bases: str = ''
    deployment_id: str = ''
    profile_hash: str = ''
    # 'simulation' | 'live'; '' on a fragment written before the column existed, which means
    # UNKNOWN and never a guess.
    run_type: str = ''
    # When this row was written — within seconds of the run's end. '' on an older fragment,
    # which is what makes a gap measured from it fall back to start-to-start and SAY so.
    recorded_at_utc: str = ''
    # How many candidates this run was selected FROM (#32). None on a fragment written before the
    # column existed — UNKNOWN — while 1 is a statement: this run was the only candidate. The
    # distinction is the whole point of the field, because a result picked as the best of 500 and
    # a result nobody compared cannot be discounted alike.
    trial_count: int | None = None
    # WHEN this row's run directory was deleted by a prune, empty while it was not. The row
    # outlives its evidence on purpose; this is what stops it from silently claiming its figures
    # can still be checked against the records they came from (§48).
    records_pruned_at: str = ''
    # === THE BOOKING PERIOD (#537) ======================================================
    # Which run unit booked it, its running number inside that unit, its two instants and
    # what closed it. `None` / '' on a row that books no period, which is what every row
    # written before this version is — absent, never a made-up period zero.
    unit_name: str = ''
    segment_no: int | None = None
    segment_opened_at: str = ''
    segment_closed_at: str = ''
    segment_close_reason: str = ''
    # The control total (§48): how many records the figures were derived from.
    segment_trade_count: int | None = None
    # The period's own equity band and its own decline, beside the cumulative trio above.
    segment_max_equity: float | None = None
    segment_min_equity: float | None = None
    segment_max_drawdown: float | None = None
    # What a period looks like beyond its net result.
    avg_mae_winners: float | None = None
    avg_mae_losers: float | None = None
    avg_mfe_losers: float | None = None
    largest_mae: float | None = None
    largest_mfe: float | None = None
    avg_trade_duration_s: float | None = None
    max_consecutive_wins: int | None = None
    max_consecutive_losses: int | None = None
    currency: str = ''
    # KPIs (the rankable objective fields)
    net_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float | None = None  # None = undefined (no losing trade)
    win_rate: float = 0.0
    account_max_drawdown: float = 0.0
    # None, never a zero. These arrive on a fragment only if the column existed when it was
    # written, and a fabricated 0.0 asserts a measurement nobody took — an equity of zero, a
    # drawdown of zero — where the truth is that the run predates the column. The same
    # convention `logic_version` and `signal_fresh_ratio` already follow. A reader that needs a
    # number then has to decide what an unknown means, which is the decision this default used
    # to make for it, silently and wrongly (CLAUDE.md §48).
    max_equity: float | None = None
    account_max_drawdown_pct: float | None = None
    unrealized_pnl: float | None = None
    final_equity: float | None = None
    open_position_count: int | None = None
    total_fees: float = 0.0
    # None, never 0.0: 499 fragments on disk predate these two columns, and a fabricated zero
    # would assert "this run won nothing and lost nothing" where the truth is "nobody wrote it
    # down". Same convention as `logic_version` and `signal_fresh_ratio` above.
    gross_profit: float | None = None
    gross_loss: float | None = None
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win_r: float | None = None
    avg_loss_r: float | None = None
    r_trade_count: int = 0
    # In LEDGER_COLUMNS since #389 and missing here for the same reason as the block above —
    # declared on disk, dropped on the way in.
    r_win_count: int | None = None
    r_loss_count: int | None = None
    orders_sent: int = 0
    orders_executed: int = 0
    orders_rejected: int = 0
    sl_tp_triggered: int = 0
    # Weakest SIGNAL channel of the run (#433); None = no SIGNAL worker was involved
    signal_fresh_ratio: float | None = None

    @computed_field
    @property
    def run_kind(self) -> str:
        """
        What KIND of run this row belongs to, within its pipeline.

        Derived rather than stored, and for the same reason `has_reports` above is: the facts
        are already in the row, so a stored subtype would be a second encoding of them — and
        the copy nobody maintains is the one that eventually disagrees (§19). A sweep
        combination names its sweep; a session of a deployment names its deployment; anything
        else stands alone.

        Returns:
            'sweep' | 'continuous' | 'single_run', or '' when the pipeline itself is unknown
        """
        if not self.run_type:
            return ''
        if self.sweep_id:
            return 'sweep'
        if self.deployment_id:
            return 'continuous'
        return 'single_run'


class SweepSummary(BaseModel):
    """
    One sweep's at-a-glance line, derived from its ledger rows (#390).

    A sweep is not a run — it is a family of them. The run index lists standalone runs; this is
    the entry point for the other kind, and a consumer drills from here into the combinations.
    """
    sweep_id: str
    started: Optional[datetime] = None   # earliest run start in the sweep (UTC)
    duration_s: float = 0.0              # last - first run start (no per-run end in the ledger)
    run_count: int = 0                   # distinct combinations (run_ids)
    ok_count: int = 0
    error_count: int = 0
    decision_logic_type: str = ''
    decision_version: str = ''
    base_config: str = ''                # the swept scenario set (sweep tag stripped)
    symbols: list[str] = []
    objective: str = ''
    maximize: bool = True


class SweepListResponse(BaseModel):
    """Every recorded sweep, newest first."""
    sweeps: list[SweepSummary]
    count: int


class SweepDetailResponse(BaseModel):
    """
    One sweep's combinations, ranked by its own objective.

    Ranked, not alphabetical: the question a sweep answers is which combination won, and each
    row carries its `run_id` so a consumer can open that run through the report routes.
    """
    sweep_id: str
    objective: str
    maximize: bool
    combinations: list[RunResultRow]
    count: int


class RunMetaReport(RunScopedReport):
    """
    Run-level execution facts the orchestrator measures primarily (sim): scenario identity +
    the wall-clock timing split. These are the run-level values the executive / basic-stats read
    straight from `BatchExecutionSummary` today — projected once at DERIVE so PRESENT stays
    model-fed. NOT re-derived facts (status comes from the warnings/errors outcome, ticks from
    the profiling aggregate) — only the primary measurements live here.
    """
    scenario_count: int = 0     # all scenarios in the run (incl. failed / disabled)
    disabled_count: int = 0
    symbols: list[str] = []
    is_profile_run: bool = False
    debug_execution: bool = False
    # Timing split (wall-clock, primary orchestrator measurements)
    execution_time_s: float = 0.0
    warmup_time_s: float = 0.0
    tickrun_time_s: float = 0.0
    pickle_time_s: float = 0.0
    pickle_sample_mb: float = 0.0
    # In-time (simulated market time) — derived from the scenario config date windows
    total_hours: float = 0.0
    total_days: float = 0.0
    avg_hours: float = 0.0
    # #137 performance-tracking layer presence (any scenario): A = worker stats, B = tick-loop profiling
    worker_tracking_on: bool = False
    profiling_tracking_on: bool = False


class BlockSplittingSymbolRow(BaseModel):
    """
    Per-symbol block-splitting disposition (Profile Runs, sim-only): how much of the symbol's
    result hangs on positions the block edge left open, against trades the strategy itself
    closed — the distortion a split introduces. Facts are summed across the symbol's blocks;
    the ratios are derived in the builder.

    The quantity changed with #492: the edge used to force-close every position, so its
    impact was realised P&L on `scenario_end` trades. Positions now stay open and the impact
    is UNREALISED — the question is the same, the measure moved.
    """
    symbol: str
    generator_mode: str
    block_count: int = 0
    open_at_boundary_trades: int = 0
    open_at_boundary_pnl: float = 0.0   # unrealised
    natural_closed_trades: int = 0
    natural_closed_pnl: float = 0.0
    discarded_pending_orders: int = 0
    # Derived (builder)
    total_trades: int = 0
    total_pnl: float = 0.0
    open_at_boundary_ratio: float = 0.0   # % of trades left open by the edge
    disposition_pct: float = 0.0          # |unrealised at edge| / |total P&L| * 100


class BlockSplittingReport(RunScopedReport):
    """
    Block-splitting disposition (Profile Runs, sim-only): per-symbol rows + the cross-symbol
    aggregate (rendered only when more than one symbol). The GOOD/MODERATE/HIGH/SEVERE label
    is a display class applied by the presenter — only the facts + ratios live here.
    """
    symbols: list[BlockSplittingSymbolRow] = []
    agg_open_at_boundary_trades: int = 0
    agg_total_trades: int = 0
    agg_open_at_boundary_ratio: float = 0.0
    agg_disposition_pct: float = 0.0


class WorkerStatRow(BaseModel):
    """Per-worker timing within a unit (#398). call_count = actual computes (#420)."""
    worker_type: str
    worker_name: str
    call_count: int = 0
    total_time_ms: float = 0.0
    avg_time_ms: float = 0.0
    min_time_ms: float = 0.0
    max_time_ms: float = 0.0
    compute_basis: str = 'live'     # #420 cadence basis (live / bar_close)
    last_compute_tick: int = -1     # #420 tick index of the last real compute (idle telemetry)
    # Cadence, derived here so every surface reads the same figure
    compute_ratio_pct: float = 0.0  # call_count / the unit's ticks_processed
    ticks_idle: int = 0             # ticks since the last real compute


class WorkerDecisionUnitRow(BaseModel):
    """
    Per-unit worker + decision performance (#398, **unified** — sim scenario / live session).
    Coordination fields are sim-only (the live session has no worker coordinator) and stay at
    their defaults on live.
    """
    name: str
    symbol: str
    # decision logic
    decision_logic_type: str = ''
    decision_logic_name: str = ''
    decision_count: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    flat_signals: int = 0
    trades_requested: int = 0
    decision_total_time_ms: float = 0.0
    decision_avg_time_ms: float = 0.0
    decision_min_time_ms: float = 0.0
    decision_max_time_ms: float = 0.0
    # coordination (sim-only)
    ticks_processed: int = 0
    parallel_workers: bool = False
    parallel_time_saved_ms: float = 0.0
    parallel_avg_saved_per_tick_ms: float = 0.0     # parallel_time_saved_ms / ticks_processed
    # per-worker timing
    workers: list[WorkerStatRow] = []


class WorkerDecisionReport(RunScopedReport):
    """
    Per-unit worker + decision stats (#398, unified): one row per scenario/session, plus the
    per-worker timing totals rolled up across units. The coordination-overhead % breakdown
    (worker / decision / coordination split) is profiling-derived and stays with the Profiling
    section — it is NOT part of this report.
    """
    units: list[WorkerDecisionUnitRow]
    worker_totals: list[WorkerStatRow] = []     # per-worker timing summed across units


class ProfilingOperationRow(BaseModel):
    """One tick-loop operation's timing within a unit (#399)."""
    operation: str
    total_time_ms: float = 0.0
    avg_time_ms: float = 0.0
    call_count: int = 0
    pct: float = 0.0            # share of the unit's total_per_tick time


class InterTickStatsRow(BaseModel):
    """Inter-tick interval distribution for a unit (#399) — market-side time between ticks."""
    min_ms: float = 0.0
    p5_ms: float = 0.0
    median_ms: float = 0.0
    mean_ms: float = 0.0
    p95_ms: float = 0.0
    max_ms: float = 0.0
    interval_count: int = 0
    gaps_removed: int = 0
    threshold_s: float = 0.0


class ClippingRow(BaseModel):
    """Tick-clipping (budget filter) stats for a unit (#399) — sim-only."""
    ticks_total: int = 0
    ticks_kept: int = 0
    ticks_clipped: int = 0
    clipping_rate_pct: float = 0.0
    budget_ms: float = 0.0


class ProfilingUnitRow(BaseModel):
    """Per-unit tick-loop profiling (#399, sim-only): operation timing + inter-tick + clipping."""
    name: str
    symbol: str
    total_ticks: int = 0
    avg_per_tick_ms: float = 0.0
    total_ms: float = 0.0                   # total_per_tick across all operations
    bottleneck_operation: str = ''          # the highest-share operation
    bottleneck_pct: float = 0.0
    operations: list[ProfilingOperationRow] = []
    inter_tick: InterTickStatsRow | None = None
    clipping: ClippingRow | None = None


class WarmupPhaseRow(BaseModel):
    """One warmup phase (#399, run-level)."""
    name: str
    duration_s: float = 0.0


class ProfilingBottleneckRow(BaseModel):
    """Cross-scenario bottleneck frequency for one operation (#399)."""
    operation: str
    scenario_count: int = 0     # in how many scenarios this op was the bottleneck
    total_scenarios: int = 0
    pct: float = 0.0
    status: str = ''            # display class only: 'expected' (hot path) | 'infra' | 'none'


class ProfilingAggregate(BaseModel):
    """Run-level profiling roll-up (#399) — composed from the unit rows by the aggregator."""
    scenarios: int = 0
    total_ticks: int = 0
    total_time_s: float = 0.0
    avg_per_tick_ms: float = 0.0
    most_common_bottleneck: str = ''
    most_common_bottleneck_pct: float = 0.0
    p5_min_ms: float = 0.0      # P5 range across scenarios
    p5_max_ms: float = 0.0
    p95_processing_ms: float = 0.0
    suggested_budget_ms: float = 0.0    # P95 + 10% margin
    budget_active: bool = False
    # How many scenarios processed a tick slower than their own fastest 5% of tick intervals.
    # A COUNT, not a verdict — whether that warrants a warning is decided by PostRunValidator
    # (_check_budget). The console reads it only to know whether a budget hint is worth showing.
    scenarios_over_p5: int = 0
    # Clipping roll-up (only meaningful when budget_active)
    clipping_total_ticks: int = 0
    clipping_total_kept: int = 0
    clipping_total_clipped: int = 0
    clipping_budgets: list[float] = []          # distinct budget values across scenarios
    avg_operation_times: list[ProfilingOperationRow] = []   # per op, cross-scenario avg (avg_time_ms)
    bottlenecks: list[ProfilingBottleneckRow] = []


class ProfilingReport(RunScopedReport):
    """
    Per-unit tick-loop profiling + run-level roll-up + warmup (#399, **sim-only**). Closes the
    #398 residual: the `worker_decision` operation Total now lives here, so the worker/decision
    breakdown reads it from the model instead of the profiling map.
    """
    units: list[ProfilingUnitRow]
    aggregate: ProfilingAggregate = ProfilingAggregate()
    warmup_phases: list[WarmupPhaseRow] = []


class BrokerSymbolRow(BaseModel):
    """Static symbol specification (one row of the TRADED SYMBOLS table)."""
    symbol: str
    volume_min: float = 0.0
    volume_max: float = 0.0
    volume_step: float = 0.0
    contract_size: int = 0
    tick_size: float = 0.0
    base_currency: str = ''
    quote_currency: str = ''
    swap_long: float = 0.0
    swap_short: float = 0.0


class BrokerInfoRow(BaseModel):
    """One broker's static configuration plus its scenario list and traded symbols."""
    broker_type: str
    market_type: str = ''
    company: str = ''
    server: str = ''
    trade_mode: str = ''
    leverage: int = 0
    margin_mode: str = ''
    margin_call_level: float = 0.0
    stopout_level: float = 0.0
    hedging_allowed: bool = False
    config_hash: str = ''
    scenarios: list[str] = []
    symbols: list[BrokerSymbolRow] = []


class BrokerReport(RunScopedReport):
    """
    Broker configuration view: one unit per broker, each with its scenario list and
    per-symbol specs. Unified — sim builds one unit per broker from the batch, the live
    session one unit for its own broker + traded symbol (no scenario grid).
    """
    units: list[BrokerInfoRow]



class ColdStartOrderRow(BaseModel):
    """
    One resting order the boot step rebuilt into the session's shadow (#355 / #493).

    Args:
        order_id: Internal id, recovered from the client key's counter
        client_order_id: The wire key the venue echoed back
        broker_ref: The venue's own reference
        direction: LONG / SHORT
        order_type: LIMIT / STOP / STOP_LIMIT
        lots: The size the order was placed with
        filled_lots: How much the venue had already executed at boot
        price: The resting price
    """
    order_id: str
    client_order_id: str = ''
    broker_ref: str = ''
    direction: str = ''
    order_type: str = ''
    lots: float = 0.0
    filled_lots: float = 0.0
    price: Optional[float] = None


class ColdStartSkippedRow(BaseModel):
    """
    One resting order at the venue the boot step left alone, and why (#493).

    Args:
        reason: foreign_key / unknown_session / in_flight / other_symbol
        client_order_id: The key it carried, when it carried one
        broker_ref: The venue's own reference
        symbol: The instrument — may differ from the session's
        order_type: What the venue reported
        lots: Order size
        price: The resting price
        key_is_ours: Whether its key names a session this bot has used; None when it carries
            no key of our shape. Recorded because `reason` cannot answer it — an
            `other_symbol` order may be our own or a stranger's
    """
    reason: str
    client_order_id: str = ''
    broker_ref: str = ''
    symbol: str = ''
    order_type: str = ''
    lots: float = 0.0
    price: Optional[float] = None
    key_is_ours: Optional[bool] = None


class ColdStartPositionRow(BaseModel):
    """
    One position the session read back from its own carry-over (#355).

    The entry price is REMEMBERED, not synthesised — which is why this row exists at all: a
    reader has to be able to tell a position this session opened from one it inherited, since
    the entry fee was charged to the run before it.

    Args:
        position_id: Internal position id, as minted when it was opened
        direction: LONG / SHORT
        lots: Currently open size
        entry_price: The remembered entry price
        entry_time: Entry time, ISO-8601 UTC
        status: OPEN / PARTIALLY_CLOSED
    """
    position_id: str
    direction: str = ''
    lots: float = 0.0
    entry_price: float = 0.0
    entry_time: str = ''
    status: str = ''


class ColdStartReport(RunScopedReport):
    """
    What the session found at boot, and what was decided about it (#355 / #493).

    Live-only. Present whenever the boot step ran and the venue reported anything; absent for
    a simulation, a dry run (which cannot query the venue) and a Field Study (excluded).

    The record exists because of the rule that no case may disappear through a yes: the
    situation is filed whether the algo accounted for it or not, so "the algo said it was
    fine" can never look the same as "nothing was found" thirty restarts later.

    Args:
        symbol: The instrument this session traded
        applied: Whether the boot went on to APPLY what this report lists. False for a boot
            that refused to start — the rows then describe what WOULD have been adopted and
            restored, and a reader who is not told that reads a session that never traded as
            one that inherited a book
        skipped_reasons: The distinct reasons in `skipped`, derived once here so no renderer
            has to aggregate (a renderer formats; it does not compute)
        adopted: Resting orders rebuilt into the shadow
        skipped: Resting orders left alone, each with its reason
        restored_positions: The book read back from the carry-over
        book_shortfall: How much the restored book claimed beyond what the account held
        adoption_mode: The resolved policy ('auto' / 'operator_confirm')
        attended: Whether a human declared they were watching the start
        carry_over_present: Whether a carry-over document was found
        carry_over_saved_at: When it was written, ISO-8601 UTC (provenance)
        algo_name: The decision logic that was asked, empty when none was
        algo_accounted_for: What it answered; None when it was not asked or answered wrongly
        algo_note: Its reason, in its own words
    """
    symbol: str = ''
    applied: bool = False
    skipped_reasons: list[str] = []
    adopted: list[ColdStartOrderRow] = []
    skipped: list[ColdStartSkippedRow] = []
    restored_positions: list[ColdStartPositionRow] = []
    book_shortfall: float = 0.0
    adoption_mode: str = ''
    attended: bool = False
    carry_over_present: bool = False
    carry_over_saved_at: str = ''
    algo_name: str = ''
    algo_accounted_for: Optional[bool] = None
    algo_note: str = ''


class SafetyLimits(BaseModel):
    """
    What the circuit breaker was ARMED with for this session (#356 / #314).

    Carried so a drawdown figure can be read against the threshold it was measured for. A
    report showing "worst drawdown 8 %" without saying whether the limit was 5 % or 30 %
    describes an incident or a quiet session and the reader cannot tell which.

    Args:
        min_floor: The account-value floor below which new entries are blocked, 0 = off
        min_floor_key: Which config key the profile wrote it under — `min_equity` on spot,
            `min_balance` on margin. Since #356 both denominate the SAME quantity, the
            account value, and the account model only decides the spelling; naming the key
            here is what keeps that from reading as two different limits
        max_drawdown_pct: Soft block threshold as a share of the baseline, 0 = off
        max_drawdown_abs: Soft block threshold as an amount, 0 = off
        max_daily_loss_abs: Daily loss limit as an amount, 0 = off
        max_daily_loss_pct: Daily loss limit as a share of the day's baseline, 0 = off
        baseline_mode: The CONFIGURED denominator — 'fixed' or 'high_water_mark'. May
            differ from the baseline record's own `kind`: a restored record keeps its kind
            and governs the session, and the two fields side by side are what show it
        persist_baseline: Whether the baseline was allowed to survive a restart
        emergency_flatten_enabled: Whether the HARD stop was armed at all
        max_drawdown_pct_hard: Hard stop threshold as a share of the baseline, 0 = off
        max_drawdown_abs_hard: Hard stop threshold as an amount, 0 = off
        spot_liquidate_to_quote: Whether the hard stop was allowed to SELL a spot holding
    """
    min_floor: float = 0.0
    min_floor_key: str = ''
    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    max_daily_loss_abs: float = 0.0
    max_daily_loss_pct: float = 0.0
    baseline_mode: str = ''
    persist_baseline: bool = True
    emergency_flatten_enabled: bool = False
    max_drawdown_pct_hard: float = 0.0
    max_drawdown_abs_hard: float = 0.0
    spot_liquidate_to_quote: bool = False


class SafetyDayRow(BaseModel):
    """
    One UTC trading day, its own denominator, and the worst loss measured against it.

    Args:
        day: The UTC date, YYYY-MM-DD
        baseline: The DAY_START record, so this row's percentage names its denominator
        baseline_value: That record's value, 0.0 when the day never took one
        worst_loss_abs: The deepest drop below it during the day
        worst_loss_pct: The same drop as a share of it. One instant, not two — a day-start
            baseline does not move inside its own day
        worst_loss_at: When that low was seen, ISO-8601 UTC
        limit_hit: Whether a DAILY limit fired on this day. A day can trip its own limit
            while the session drawdown stays well inside its threshold, which is the entire
            reason a daily limit exists beside a session one
    """
    day: str
    baseline: Optional[RiskBaseline] = None
    baseline_value: float = 0.0
    worst_loss_abs: float = 0.0
    worst_loss_pct: float = 0.0
    worst_loss_at: str = ''
    limit_hit: bool = False


class SafetyReport(RunScopedReport):
    """
    The risk denominator this session ran against, and how far it moved (#356 / #314).

    Live-only, and present whenever a baseline was taken — including a session that ran with
    the limits switched OFF. That case is deliberately not skipped: a session measuring its
    drawdown without acting on it is what says what WOULD have fired, which is the record a
    parity proof wants before anything is armed.

    Every figure here names its denominator. Four quantities in this codebase are called
    "initial" and none of them records when or at what price it was taken, so a bare "−12 %"
    could not be traced to the number that produced it. The baseline record travels whole —
    its kind, its stamp, its origin, and on spot the price and holdings it can be re-derived
    from — rather than as a copied float.

    The session extremes are RUNNING MAXIMA, not the value at the end. A session that
    touched 18 % at hour three and recovered would otherwise be indistinguishable from one
    that never moved, and over thirty unattended days that is the difference that matters.

    Args:
        symbol: The instrument this session traded
        enabled: Whether the circuit breaker was switched on
        baseline: The session denominator as the record that describes itself. None when
            none was ever taken — a session that saw no valuable tick
        baseline_value: That record's value, 0.0 when there is none. Repeated flat so a
            consumer reading only the top level has the denominator
        baseline_restored: Whether it came back from the previous session rather than being
            struck by this one. The whole point of #356, and one boolean away from invisible
        final_value: The last account value the breaker checked
        worst_drawdown_abs: The deepest drop below the baseline, in account currency
        worst_drawdown_abs_at: When that low was seen, ISO-8601 UTC
        worst_drawdown_pct: The deepest drop as a SHARE of the baseline — a separate
            instant, because a high-water-mark baseline moves and then the largest amount
            and the largest share are two different moments. With a fixed baseline they
            coincide
        worst_drawdown_pct_at: When THAT low was seen, ISO-8601 UTC
        soft_limit_used_pct: How much of the configured soft drawdown limit the worst
            excursion consumed, as a percentage — 100 means it fired. None when no soft
            drawdown limit was configured, which is not the same as 0
        hard_limit_used_pct: The same measure against the HARD threshold. None when the
            hard stop was not armed
        block_count: How often the soft block ENGAGED — transitions, not ticks spent blocked
        blocked_at_end: Whether new entries were still blocked when the session ended
        reason_at_end: The breaker's reason at that moment, empty when it was not blocked
        limits: What was armed
        days: One row per UTC day the session ran through
        days_limit_hit: How many of those days tripped a daily limit
        worst_day: The UTC date of the deepest daily loss, empty when no day took a
            baseline. Derived here rather than in a renderer: thirty rows do not belong on
            a console, and a renderer that picks the maximum out of them has built its own
            aggregate, which is how two surfaces come to disagree about the same figure
        worst_day_loss_abs: That day's loss, in account currency
        worst_day_loss_pct: That day's loss as a share of ITS OWN day-start baseline
        flatten_fired: Whether the HARD stop tripped
        flatten_reason: What tripped it, with its numbers
        flatten_completed: Whether the book was confirmed flat before the session ended.
            None when the hard stop never fired — which a reader must be able to tell apart
            from "fired and did not finish"
        flatten_unconfirmed: Positions still open when the session ended anyway. These are
            at the venue and nobody has confirmed otherwise
    """
    symbol: str = ''
    enabled: bool = False
    baseline: Optional[RiskBaseline] = None
    baseline_value: float = 0.0
    baseline_restored: bool = False
    final_value: float = 0.0
    worst_drawdown_abs: float = 0.0
    worst_drawdown_abs_at: str = ''
    worst_drawdown_pct: float = 0.0
    worst_drawdown_pct_at: str = ''
    soft_limit_used_pct: Optional[float] = None
    hard_limit_used_pct: Optional[float] = None
    block_count: int = 0
    blocked_at_end: bool = False
    reason_at_end: str = ''
    limits: SafetyLimits = SafetyLimits()
    days: list[SafetyDayRow] = []
    days_limit_hit: int = 0
    worst_day: str = ''
    worst_day_loss_abs: float = 0.0
    worst_day_loss_pct: float = 0.0
    flatten_fired: bool = False
    flatten_reason: str = ''
    flatten_completed: Optional[bool] = None
    flatten_unconfirmed: list[str] = []


class SignalUsageRow(BaseModel):
    """
    One scenario's use of a signal source: its window (archive plane) plus what the
    strategy actually decided on in that window (runtime plane, #433 Part C).

    The two planes answer different questions and can disagree legitimately — an archive
    gap shorter than max_staleness_minutes produces zero stale ticks.
    """
    scenario: str
    symbol: str = ''
    window_start: str = ''          # ISO-8601 UTC — the scenario window, or the session span
    window_end: str = ''            # ISO-8601 UTC ('' when the scenario ends on max_ticks)
    coverage_ratio: Optional[float] = None   # share of the window not swallowed by an
    #                                          archive gap; None = no archive to measure
    #                                          against (a live feed). NOT 1.0 — a default
    #                                          of "fully covered" would assert coverage for
    #                                          something that was never measurable.
    fresh_ticks: int = 0
    stale_ticks: int = 0
    blind_ticks: int = 0            # nothing resolvable — NOT an archive gap (that is stale)
    fresh_ratio: float = 0.0        # fresh / total ticks; 0.0 when the run processed none
    # How often this unit's resolution gate held a snapshot's visibility instant back
    # because the producer's availability stamp stepped BACKWARDS, and the largest step it
    # absorbed. A property of the SERIES this unit ordered, which is why it sits here and
    # not on the source row: in simulation every scenario builds its own provider over its
    # own window-trimmed slice, so a source-level number could not say WHICH window met the
    # correction. Derived from our own gate rather than read from the producer's counter
    # (§41), so simulation and live report the same number over the same archive.
    availability_clamps: int = 0
    max_clamp_correction_ms: float = 0.0


class SignalSourceRow(BaseModel):
    """
    One signal source: its archive provenance + measured cadence, plus one usage row per
    scenario bound to it. Empty provenance means UNKNOWN, never a positive assertion; a
    source carrying several values renders as 'mixed'.
    """
    source: str
    series_kind: str = 'archive'            # 'archive' (analysable for continuity) or
    #                                         'feed' (received live — no archive plane at
    #                                         all). Deliberately not named *_origin: the
    #                                         row already carries data_origin, which means
    #                                         the producer's provenance, not the reader's.
    data_origin: str = ''                   # 'live' / 'synthetic' / 'mixed' / '' = unknown
    config_fingerprint: str = ''            # producer input-config hash; '' = pre-contract
    prompt_version: str = ''                # producer prompt generation; 'mixed' means the
    #                                         run spans a prompt change — more than one series,
    #                                         and scores are not comparable across the boundary.
    #                                         The archive spans v1/v2/v3/v4 from 2026-08-23 on.
    #                                         '' = not recorded (archive projection omits it)
    cadence_seconds: float = 0.0            # measured median snapshot distance
    snapshot_count: int = 0
    archive_start: str = ''                 # ISO-8601 UTC
    archive_end: str = ''                   # ISO-8601 UTC
    gap_counts: dict[str, int] = {}         # category → count; EMPTY means not measured,
    #                                         which for a feed means not measurable. A
    #                                         renderer must not print it as 'no gaps'.
    sequence: str = ''                      # stream position verdict, '' when unstated
    trigger_reasons: dict[str, int] = {}    # scheduled / boot / breaking / manual / external
    trigger_unknown: int = 0                # envelopes with no reason (producer pre-contract)
    usages: list[SignalUsageRow] = []


class SignalReport(RunScopedReport):
    """
    Signal configuration view (#433): one unit per signal source, each with its archive
    provenance and the scenarios consuming it. Unified — both pipelines build it from the
    same shared data preparation, so sim batch and AutoTrader-mock session render identically.
    """
    units: list[SignalSourceRow]


class FeedStabilityEpisodeRow(BaseModel):
    """
    One observed outage span of a data source (#451).

    The span is what the run experienced, never what a stress window planned. An empty
    `stale_to` means the source never recovered — the run ended inside the outage.
    """
    unit: str = ''
    symbol: str = ''
    stale_from: str = ''            # ISO-8601 UTC
    stale_to: str = ''              # ISO-8601 UTC ('' = never recovered)
    duration_seconds: float = 0.0   # measured (live: wall axis, sim: canonical axis)
    origin: str = 'live-real'       # 'live-real' | 'stress-injected'
    label: str = ''                 # the stress event's label ('' for a real outage)


class FeedStabilitySourceRow(BaseModel):
    """
    One data source's stability over the run: its episodes plus the per-tick counters
    of its domain (#451).

    The counters answer "how much of the run was decided on degraded data", the episodes
    answer "when and how often" — a ratio alone cannot separate one long outage from
    forty short ones.
    """
    source: str
    domain: str = 'tick'            # 'tick' (market data) | 'signal' (a SIGNAL source)
    stale_seconds: float = 0.0      # summed episode duration
    episode_count: int = 0
    origins: list[str] = []         # distinct origins across the episodes
    fresh_ticks: int = 0
    stale_ticks: int = 0
    blind_ticks: int = 0            # signal domain only (nothing resolvable)
    episodes: list[FeedStabilityEpisodeRow] = []


class FeedStabilityReport(RunScopedReport):
    """
    Feed stability view (#451): one row per source across BOTH staleness domains —
    the tick stream (#436) and every SIGNAL source (#434) — in both pipelines.

    Live-real and stress-injected disturbances travel the same path and differ only by
    the origin column; the report states the facts, any verdict stays in a validator.
    """
    units: list[FeedStabilitySourceRow]
    episode_count: int = 0
    stale_seconds: float = 0.0
    stress_injected_count: int = 0
    source_count: int = 0


class WarningTier(StrEnum):
    """
    Which CHANNEL produced a warning row — the tier IS the origin question, answered once.

    Named for what the value means rather than for its severity: 'major' / 'minor' read like
    a severity and are not one. The VALUES stay as they are because they are the wire contract
    the API and FiniexViewer already consume; only the names are ours to make honest.
    """
    VALIDATOR_PRODUCED = 'major'    # Tier 1 — a check decided it, so check/domain are filled
    LOGGER_PRODUCED = 'minor'       # Tier 2 — the log pot: an observation nobody adjudicated


class WarningRow(BaseModel):
    """One warning notice (#395). See docs/architecture/warnings_errors_tiers.md."""
    tier: WarningTier = WarningTier.VALIDATOR_PRODUCED
    scope: str = 'run'              # 'run' (batch-global) | unit name (per-scenario / session)
    message: str = ''
    # Origin — WHICH ASSERTION decided this. `check` is its stable id, `domain` its area. Both
    # are empty on a LOGGER_PRODUCED row: no assertion decided it, and the channel is already
    # named by `tier` — putting it here too would be the same value in two fields.
    check: str = ''
    domain: str = ''


class LogEntryRow(BaseModel):
    """
    One entry from a logger's pot, with the fields the record carries.

    The buffered `LogRecord` reaches DERIVE intact; reducing it to its message here would make
    level, time and scope unreachable for every surface behind this model — console, artifact
    and API alike (#391). The two times are §9's pair: `observed_at` is when we recorded it,
    `event_time` is the run's own clock, absent when none was attached.
    """
    level: str
    observed_at: datetime
    scope: str = ''
    message: str = ''
    event_time: Optional[datetime] = None


class UnitErrorRow(BaseModel):
    """Per-unit error record (#395): the villain + validation errors + the logged ERROR pot."""
    name: str
    symbol: str = ''
    error_type: str = ''            # ProcessResult villain (uncaught exception)
    error_message: str = ''
    validation_errors: list[str] = []   # ValidationResult.errors (is_valid=False)
    logged_errors: list[LogEntryRow] = []   # scenario/session logger ERROR pot (§35)
    traceback: str = ''


class WarningsErrorsOutcome(BaseModel):
    """Run-level outcome (#395) — the Executive headline reads this, it does not re-scan."""
    # The canonical grading (#372), stamped once at DERIVE from the pipeline's own result object
    # (RunOutcome value). Every surface — console, artifact, API — reads this instead of
    # re-deriving a verdict from the counts below.
    run_outcome: str = ''
    failed_count: int = 0
    total_units: int = 0
    failed_unit_names: list[str] = []
    first_failure_name: str = ''
    first_failure_error: str = ''
    emergency_reason: str = ''      # live villain
    shutdown_mode: str = ''         # live outcome ('normal' | 'emergency'); '' on sim (not applicable)
    # An operator Ctrl+C also arrives as shutdown_mode='emergency', so the mode alone cannot
    # separate a deliberate stop from a crash — this flag is the discriminator get_outcome() uses.
    operator_interrupted: bool = False


class WarningsErrorsReport(RunScopedReport):
    """
    Unified warnings & errors section (#395, both pipelines). Tiered: errors (always) +
    Tier-1 major warnings (validator-produced) + Tier-2 minor warnings (log pot). The
    reporting pipeline only reads — every verdict is decided by a validator upstream.
    See docs/architecture/warnings_errors_tiers.md.
    """
    warnings: list[WarningRow] = []
    errors: list[UnitErrorRow] = []
    outcome: WarningsErrorsOutcome = WarningsErrorsOutcome()


class AggregatedPortfolioSpotScenarioRow(BaseModel):
    """Per-scenario spot dual-balance view (the executive spot block, #397)."""
    scenario_name: str
    quote_currency: str = ''
    base_currency: str = ''
    quote_balance: float = 0.0
    base_balance: float = 0.0
    quote_initial: float = 0.0
    base_initial: float = 0.0
    last_price: float = 0.0
    est_current: float = 0.0        # quote + base*last_price (0 if no base holdings)
    est_initial: float = 0.0
    has_base_holdings: bool = False


class AggregatedPortfolioRow(BaseModel):
    """
    Full per-currency (or per-mode, for mixed batches) aggregate — the rich detail view that
    `PortfolioAggregator` used to feed inline (#397). Composes the lean `PortfolioAggregateRow`
    (the headline / `RunSummary` source) and adds the cross-domain extras: balances, cost split,
    per-currency execution, pending, and the spot dual-balance. Derived values (avg win/loss,
    recovery factor, %s) are computed in the builder; presenters only format.
    """
    headline: PortfolioAggregateRow
    is_spot: bool = False
    label: str = ''                 # '' | 'Margin' | 'Spot' (mixed-batch tag)
    # Trade extras
    total_long_trades: int = 0
    total_short_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    # Balances (per-currency sums of per-scenario portfolio stats)
    initial_balance: float = 0.0
    final_balance: float = 0.0
    avg_initial: float = 0.0
    balance_pnl: float = 0.0        # final_balance - initial_balance (executive "Total P&L")
    balance_pnl_pct: float = 0.0
    # Risk
    recovery_factor: float = 0.0
    account_max_dd_pct: float = 0.0
    account_max_drawdown_scenario: str = ''
    max_equity: float = 0.0
    max_equity_scenario: str = ''
    # Cost split
    total_spread_cost: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0
    maker_fee: float = 0.0
    taker_fee: float = 0.0
    avg_spread: float = 0.0
    # Execution (per currency)
    orders_sent: int = 0
    orders_executed: int = 0
    orders_rejected: int = 0
    sl_tp_triggered: int = 0
    execution_rate_pct: float = 0.0     # orders_executed / orders_sent
    # Pending
    pending_total_resolved: int = 0
    pending_total_filled: int = 0
    pending_total_rejected: int = 0
    pending_total_timed_out: int = 0
    pending_total_force_closed: int = 0
    pending_avg_latency_ms: float | None = None
    pending_min_latency_ms: float | None = None
    pending_max_latency_ms: float | None = None
    pending_active_limit_count: int = 0
    pending_active_stop_count: int = 0
    # Spot dual-balance (only populated for spot rows)
    spot_scenarios: list[AggregatedPortfolioSpotScenarioRow] = []
    spot_total_est_current: float = 0.0
    spot_total_est_initial: float = 0.0
    spot_has_base_holdings: bool = False


class AggregatedPortfolioCurrency(BaseModel):
    """
    One currency group (#397). `combined` feeds the "AGGREGATED PORTFOLIO" section (margin+spot
    together, as `PortfolioAggregator` grouped by currency only); `margin`/`spot` feed the executive
    block which splits a mixed batch. For pure margin / pure spot, only `combined` is used.
    """
    currency: str
    scenario_count: int = 0
    scenario_names: list[str] = []
    is_spot: bool = False           # pure-spot currency (executive renders the spot path)
    is_mixed: bool = False          # both margin + spot present → use margin/spot sub-rows
    combined: AggregatedPortfolioRow
    margin: AggregatedPortfolioRow | None = None   # only when is_mixed
    spot: AggregatedPortfolioRow | None = None     # only when is_mixed


class AggregatedPortfolioReport(RunScopedReport):
    """
    Aggregated per-currency portfolio — the rich detail view (#397, retires `PortfolioAggregator`).
    `RunSummary` stays the lean KPI headline; this is the comprehensive single-concern object
    (LEAN's Portfolio-vs-Trade split). **Sim batch** (multi-currency aggregation is a sim concern;
    live keeps the lean `PortfolioReport.aggregates`).
    """
    currencies: list[AggregatedPortfolioCurrency] = []


# ============================================================================
# Robustness — Multi-Window + IS/OOS validation (#367, sim-only)
# ============================================================================

class RobustnessWindowRow(BaseModel):
    """One window's robustness metric + its IS/OOS role and (Profile Run) regime/session."""
    name: str
    role: str                   # 'in_sample' | 'out_of_sample' | 'unassigned'
    regime: str = ''            # Profile Runs only (from the source block)
    session: str = ''           # Profile Runs only
    currency: str = ''
    metric_value: float = 0.0   # the configured primary metric (expectancy or net_pnl)
    net_pnl: float = 0.0        # secondary, non-aggregated reference
    expectancy: float = 0.0
    total_trades: int = 0
    profitable: bool = False


class RobustnessDistribution(BaseModel):
    """Distribution of the primary metric across all windows (the Stage-2 powerful form)."""
    window_count: int = 0
    pct_profitable: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    std: float = 0.0
    best_value: float = 0.0
    best_window: str = ''
    worst_value: float = 0.0
    worst_window: str = ''
    # std / |mean| — stability across windows (lower = more consistent); 0 when mean is 0.
    coefficient_of_variation: float = 0.0


class RobustnessRoleAggregate(BaseModel):
    """Aggregate of the primary metric over one role's windows (IS or OOS)."""
    role: str
    window_count: int = 0
    mean_metric: float = 0.0
    median_metric: float = 0.0
    pct_profitable: float = 0.0


class RobustnessRegimeRow(BaseModel):
    """Per-regime breakdown of the primary metric (Profile Runs only)."""
    regime: str
    window_count: int = 0
    mean_metric: float = 0.0
    pct_profitable: float = 0.0


class RobustnessReport(RunScopedReport):
    """
    Multi-window robustness + IS/OOS validation (#367, sim-only). PURE FACTS — the
    ROBUST/OVERFIT verdict is a decision and lives in `PostRunValidator`, not in this model.
    The renderer applies only a display class; the verdict warning fires from the validator.
    """
    enabled: bool = False
    metric: str = 'expectancy'              # the primary metric name
    windows: list[RobustnessWindowRow] = []
    distribution: RobustnessDistribution | None = None
    in_sample: RobustnessRoleAggregate | None = None
    out_of_sample: RobustnessRoleAggregate | None = None
    # OOS mean / IS mean — degradation measure; None when IS mean ≤ 0 (degradation undefined).
    walk_forward_efficiency: float | None = None
    params_constant: bool = True            # all windows share the resolved strategy_config
    drifting_windows: list[str] = []        # windows whose params differ from the first
    disposition_pct: float = 0.0            # block-splitting distortion (trust-gate input)
    regime_breakdown: list[RobustnessRegimeRow] = []
    # Config thresholds carried for a self-describing render (display class) + API consistency.
    overfit_wfe_threshold: float = 0.5
    robust_wfe_threshold: float = 0.8
    disposition_trust_pct: float = 25.0
    min_windows: int = 3
