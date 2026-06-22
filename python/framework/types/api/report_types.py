"""
Report API types — Pydantic models for the unified reporting pipeline (#391).

The canonical, fully-derived report model the postprocessor produces. The console,
file, and API renderers all consume it, so the data is identical across every
surface. Pydantic (not @dataclass) because the API serializes it directly — same
exception as api_types.py.
"""

from typing import Any

from pydantic import BaseModel


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
    # Trade analytics (#389) — excursion + risk-normalized result (defaulted: additive columns)
    mae_price: float = 0.0      # most adverse price reached while open
    mfe_price: float = 0.0      # most favorable price reached while open
    mae_pnl: float = 0.0        # gross P&L at the worst excursion
    mfe_pnl: float = 0.0        # gross P&L at the best excursion
    mae_pips: float = 0.0       # MAE distance in pips (forex convention; #167 for exact pip_size)
    mfe_pips: float = 0.0       # MFE distance in pips (forex convention)
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
    avg_win_r: float        # mean R of winners (R-defined)
    avg_loss_r: float       # mean R of losers (R-defined)
    r_trade_count: int      # trades with a defined R (had a stop loss)
    avg_mae_winners: float  # mean MAE P&L on winners — SL too tight if large vs win size
    avg_mae_losers: float   # mean MAE P&L on losers
    avg_mfe_losers: float   # mean MFE P&L on losers — "left on the table" read
    # Per-currency P&L totals (#393 — the trade-table TOTAL line, model-served for the API)
    gross_pnl: float = 0.0  # Σ gross P&L over the group
    net_pnl: float = 0.0    # Σ net P&L over the group
    total_fees: float = 0.0  # Σ fees over the group


class TradeScenarioTotals(BaseModel):
    """Per-scenario trade-table totals (the per-scenario footer) — model-served, no renderer math."""
    scenario_name: str
    currency: str
    trade_count: int
    gross_pnl: float
    net_pnl: float
    total_fees: float


class TradeHistoryReport(BaseModel):
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


class OrderHistoryReport(BaseModel):
    """The order-history table + light metadata (flat, like trade history)."""
    orders: list[OrderHistoryRow]
    count: int
    symbols: list[str]      # distinct symbols present (filter UX)


class PortfolioUnitRow(BaseModel):
    """Headline P&L of one run unit (sim: a scenario; live: the session)."""
    name: str               # scenario name (sim) / profile/session label (live)
    symbol: str
    currency: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    total_profit: float
    total_loss: float
    net_profit: float       # total_profit - total_loss
    max_drawdown: float
    total_fees: float
    # Full projection — the per-scenario linear block renders purely from these (defaulted:
    # additive columns; the per-currency aggregated section stays on PortfolioAggregator).
    data_source: str = ''       # the scenario's data broker type (box line "Data: …")
    broker_name: str = ''
    spot_mode: bool = False
    total_long_trades: int = 0
    total_short_trades: int = 0
    max_equity: float = 0.0
    current_balance: float = 0.0
    initial_balance: float = 0.0
    conversion_rate: float | None = None
    total_spread_cost: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0
    maker_fee: float = 0.0       # spot maker-side fee
    taker_fee: float = 0.0       # spot taker-side fee
    has_error: bool = False     # hybrid unit (partial data + error) → CRITICAL marker
    # Spot mode — dual-balance + estimated portfolio value
    balances: dict[str, float] = {}
    initial_balances: dict[str, float] = {}
    last_price: float = 0.0


class PortfolioAggregateRow(BaseModel):
    """Headline P&L rolled up per account currency (no cross-currency summing)."""
    currency: str
    unit_count: int         # scenarios in this currency group (live: 1)
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    total_profit: float
    total_loss: float
    net_profit: float
    max_drawdown: float
    total_fees: float


class PortfolioReport(BaseModel):
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


class ExecutionStatsReport(BaseModel):
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


class PendingOrdersReport(BaseModel):
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


class ScenarioDetailsReport(BaseModel):
    """
    Per-scenario execution/signal metadata (sim-only): one row per scenario, **including
    failed ones** (the section's job is the full scenario status grid).
    """
    units: list[ScenarioDetailsRow]


class RunSummaryCurrency(BaseModel):
    """
    Run-wide KPIs for ONE account currency (#390 prework). Composed once from the per-section
    aggregates (portfolio roll-up + trade analytics) — never re-derived. Per currency so the
    P&L-denominated fields never mix currencies.
    """
    currency: str
    net_pnl: float          # ← PortfolioAggregateRow.net_profit
    profit_factor: float    # ← PortfolioAggregateRow.profit_factor
    win_rate: float         # ← PortfolioAggregateRow.win_rate
    max_drawdown: float     # ← PortfolioAggregateRow.max_drawdown
    total_fees: float       # ← PortfolioAggregateRow.total_fees
    total_trades: int
    winning_trades: int
    losing_trades: int
    expectancy: float       # ← TradeAnalytics.expectancy (mean R) — the sweep objective
    avg_win_r: float        # ← TradeAnalytics.avg_win_r
    avg_loss_r: float       # ← TradeAnalytics.avg_loss_r
    r_trade_count: int      # ← TradeAnalytics.r_trade_count


class RunSummary(BaseModel):
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


class RunResultRow(BaseModel):
    """
    One run-results ledger row (#390), typed. The parsed projection of the parquet `LEDGER_COLUMNS`:
    the JSON columns (`worker_versions`, `symbols`, `sweep_params`) are parsed back to structured types
    so the optimization analysis + the (future) API read typed objects, not string-keyed DataFrame cells.
    """
    # Identity + provenance
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
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool = False
    decision_logic_type: str = ''
    decision_version: str = ''
    worker_versions: dict[str, str] = {}
    config_snapshot: str = ''                    # full resolved strategy_config (JSON string)
    symbols: list[str] = []
    data_broker_type: str = ''
    currency: str = ''
    # KPIs (the rankable objective fields)
    net_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    total_fees: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    r_trade_count: int = 0
    orders_sent: int = 0
    orders_executed: int = 0
    orders_rejected: int = 0
    sl_tp_triggered: int = 0


class RunMetaReport(BaseModel):
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
    P&L came from force-closes at block boundaries vs. natural closes — the distortion a split
    introduces. Facts are summed across the symbol's blocks; the ratios are derived in the builder.
    """
    symbol: str
    generator_mode: str
    block_count: int = 0
    force_closed_trades: int = 0
    force_closed_pnl: float = 0.0
    natural_closed_trades: int = 0
    natural_closed_pnl: float = 0.0
    discarded_pending_orders: int = 0
    # Derived (builder)
    total_trades: int = 0
    total_pnl: float = 0.0
    force_close_ratio: float = 0.0      # % of trades that were force-closed
    disposition_pct: float = 0.0        # |force-close P&L| / |total P&L| * 100


class BlockSplittingReport(BaseModel):
    """
    Block-splitting disposition (Profile Runs, sim-only): per-symbol rows + the cross-symbol
    aggregate (rendered only when more than one symbol). The GOOD/MODERATE/HIGH/UNRELIABLE label
    is a display class applied by the presenter — only the facts + ratios live here.
    """
    symbols: list[BlockSplittingSymbolRow] = []
    agg_force_closed_trades: int = 0
    agg_total_trades: int = 0
    agg_force_close_ratio: float = 0.0
    agg_disposition_pct: float = 0.0


class WorkerStatRow(BaseModel):
    """Per-worker timing within a unit (#398)."""
    worker_type: str
    worker_name: str
    call_count: int = 0
    total_time_ms: float = 0.0
    avg_time_ms: float = 0.0
    min_time_ms: float = 0.0
    max_time_ms: float = 0.0


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
    # per-worker timing
    workers: list[WorkerStatRow] = []


class WorkerDecisionReport(BaseModel):
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
    # Clipping roll-up (only meaningful when budget_active)
    clipping_total_ticks: int = 0
    clipping_total_kept: int = 0
    clipping_total_clipped: int = 0
    clipping_budgets: list[float] = []          # distinct budget values across scenarios
    avg_operation_times: list[ProfilingOperationRow] = []   # per op, cross-scenario avg (avg_time_ms)
    bottlenecks: list[ProfilingBottleneckRow] = []


class ProfilingReport(BaseModel):
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


class BrokerReport(BaseModel):
    """
    Broker configuration view: one unit per broker, each with its scenario list and
    per-symbol specs. **Sim-only** for now — the live session's broker_config is loaded
    at AutoTrader startup but not yet carried into the session report (live follow-up).
    """
    units: list[BrokerInfoRow]


class WarningRow(BaseModel):
    """One warning notice (#395). See docs/architecture/warnings_errors_tiers.md."""
    tier: str = 'major'             # 'major' (Tier 1, validator-produced) | 'minor' (Tier 2, log pot)
    scope: str = 'run'              # 'run' (batch-global) | unit name (per-scenario / session)
    message: str = ''


class UnitErrorRow(BaseModel):
    """Per-unit error record (#395): the villain + validation errors + the logged ERROR pot."""
    name: str
    symbol: str = ''
    error_type: str = ''            # ProcessResult villain (uncaught exception)
    error_message: str = ''
    validation_errors: list[str] = []   # ValidationResult.errors (is_valid=False)
    logged_errors: list[str] = []       # scenario/session logger ERROR pot (§35)
    traceback: str = ''


class WarningsErrorsOutcome(BaseModel):
    """Run-level outcome (#395) — the Executive headline reads this, it does not re-scan."""
    failed_count: int = 0
    total_units: int = 0
    failed_unit_names: list[str] = []
    first_failure_name: str = ''
    first_failure_error: str = ''
    emergency_reason: str = ''      # live villain
    shutdown_mode: str = ''         # live outcome ('normal' | 'emergency')


class WarningsErrorsReport(BaseModel):
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
    max_dd_pct: float = 0.0
    max_drawdown_scenario: str = ''
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


class AggregatedPortfolioReport(BaseModel):
    """
    Aggregated per-currency portfolio — the rich detail view (#397, retires `PortfolioAggregator`).
    `RunSummary` stays the lean KPI headline; this is the comprehensive single-concern object
    (LEAN's Portfolio-vs-Trade split). **Sim batch** (multi-currency aggregation is a sim concern;
    live keeps the lean `PortfolioReport.aggregates`).
    """
    currencies: list[AggregatedPortfolioCurrency] = []
