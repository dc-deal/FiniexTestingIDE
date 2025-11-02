"""
FiniexTestingIDE - Trading Environment Types
Type definitions for trading environment statistics and account information

Contains:
- AccountInfo: Account state snapshot
- PortfolioStats: Complete portfolio performance statistics
- ExecutionStats: Order execution statistics
- CostBreakdown: Detailed cost breakdown (spread/commission/swap)

All Dict[str, Any] types replaced with strongly-typed dataclasses.
"""

from dataclasses import dataclass


@dataclass
class AccountInfo:
    """
    Account information snapshot.

    Provides real-time account state including balance, equity,
    margin usage, and position statistics.

    Attributes:
        balance: Account balance (realized P&L)
        equity: Current equity (balance + unrealized P&L)
        margin_used: Total margin used by open positions
        free_margin: Available margin for new positions
        margin_level: Margin level percentage
        open_positions: Number of open positions
        total_lots: Total lot size across all positions
        currency: Account currency
        leverage: Account leverage
    """
    balance: float
    equity: float
    margin_used: float
    free_margin: float
    margin_level: float
    open_positions: int
    total_lots: float
    currency: str
    leverage: int


@dataclass
class PortfolioStats:
    """
    Complete portfolio performance statistics.

    Aggregates trading performance, P&L metrics, and cost breakdown.
    Includes both raw statistics and calculated metrics (win rate, profit factor).

    Attributes:
        total_trades: Total number of closed trades
        winning_trades: Number of winning trades
        losing_trades: Number of losing trades
        total_profit: Total profit from winning trades
        total_loss: Total loss from losing trades (absolute value)
        max_drawdown: Maximum equity drawdown
        max_equity: Maximum equity reached
        win_rate: Winning percentage (0.0-1.0)
        profit_factor: Ratio of total profit to total loss
        total_spread_cost: Total spread cost paid
        total_commission: Total commission paid
        total_swap: Total swap paid/received
        total_fees: Sum of all trading costs
        currency: Account currency for all monetary values
    """
    total_trades: int
    total_long_trades: int
    total_short_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    total_loss: float
    max_drawdown: float
    max_equity: float
    win_rate: float
    profit_factor: float
    total_spread_cost: float
    total_commission: float
    total_swap: float
    total_fees: float
    currency: str  # Account currency (e.g., "USD", "EUR", "JPY")


@dataclass
class ExecutionStats:
    """
    Order execution statistics.

    Tracks order submission, execution, and rejection rates.
    Useful for analyzing broker simulation behavior and strategy performance.

    Attributes:
        orders_sent: Total orders submitted
        orders_executed: Orders successfully executed
        orders_rejected: Orders rejected (margin, validation, etc.)
        total_commission: Total commission paid on executions
        total_spread_cost: Total spread cost from order fills
    """
    orders_sent: int
    orders_executed: int
    orders_rejected: int
    total_commission: float
    total_spread_cost: float


@dataclass
class CostBreakdown:
    """
    Detailed trading cost breakdown.

    Separates trading costs by type for cost analysis and optimization.
    All costs are in account currency.

    Attributes:
        total_spread_cost: Bid-ask spread cost (implicit)
        total_commission: Broker commission (explicit)
        total_swap: Overnight interest (can be negative/positive)
        total_fees: Sum of all costs
    """
    total_spread_cost: float = 0
    total_commission: float = 0
    total_swap: float = 0
    total_fees: float = 0
    currency: str = ''  # Account currency (e.g., "USD", "EUR", "JPY")
