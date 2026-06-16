"""
Report API types — Pydantic models for the unified reporting pipeline (#391).

The canonical, fully-derived report model the postprocessor produces. The console,
file, and API renderers all consume it, so the data is identical across every
surface. Pydantic (not @dataclass) because the API serializes it directly — same
exception as api_types.py.
"""

from pydantic import BaseModel


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
    # Trade analytics (#389) — excursion + risk-normalized result (defaulted: additive columns)
    mae_price: float = 0.0      # most adverse price reached while open
    mfe_price: float = 0.0      # most favorable price reached while open
    mae_pnl: float = 0.0        # gross P&L at the worst excursion
    mfe_pnl: float = 0.0        # gross P&L at the best excursion
    mae_pips: float = 0.0       # MAE distance in pips (forex convention; #167 for exact pip_size)
    mfe_pips: float = 0.0       # MFE distance in pips (forex convention)
    r_multiple: float | None = None  # net_pnl / initial_risk; None when no stop loss


class TradeAnalytics(BaseModel):
    """Aggregate trade analytics (#389) — risk-normalized profitability + SL calibration."""
    expectancy: float       # mean R over trades with a defined R
    avg_win_r: float        # mean R of winners (R-defined)
    avg_loss_r: float       # mean R of losers (R-defined)
    r_trade_count: int      # trades with a defined R (had a stop loss)
    avg_mae_winners: float  # mean MAE P&L on winners — SL too tight if large vs win size
    avg_mae_losers: float   # mean MAE P&L on losers
    avg_mfe_losers: float   # mean MFE P&L on losers — "left on the table" read


class TradeHistoryReport(BaseModel):
    """The trade-history table + light metadata + aggregate analytics (#389)."""
    trades: list[TradeHistoryRow]
    count: int
    symbols: list[str]      # distinct symbols present (filter UX)
    analytics: TradeAnalytics


class OrderHistoryRow(BaseModel):
    """One order-lifecycle record (the resting/filled/rejected order list)."""
    order_id: str
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
