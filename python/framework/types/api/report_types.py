"""
Report API types — Pydantic models for the unified reporting pipeline (#391).

The canonical, fully-derived report model the postprocessor produces. The console,
file, and API renderers all consume it, so the data is identical across every
surface. Pydantic (not @dataclass) because the API serializes it directly — same
exception as api_types.py.
"""

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


class TradeHistoryReport(BaseModel):
    """The trade-history table + light metadata + per-currency analytics (#389/#393)."""
    trades: list[TradeHistoryRow]
    count: int
    symbols: list[str]      # distinct symbols present (filter UX)
    analytics: list[TradeAnalytics]  # one entry per account currency (no cross-currency mixing)


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
