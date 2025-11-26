"""
FiniexTestingIDE - Portfolio Aggregation Types
Types for currency-grouped portfolio aggregation
"""

from typing import Optional
from dataclasses import dataclass
from typing import List


from python.framework.types.trading_env_stats_types import (
    ExecutionStats,
    CostBreakdown
)


@dataclass
class BasePortfolioStats:
    """
    Base portfolio statistics shared across single and aggregated views.

    Contains all common trading metrics, P&L, costs, and metadata.
    """
    # Trading statistics
    total_trades: int
    total_long_trades: int
    total_short_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    total_loss: float

    # Risk metrics
    max_drawdown: float
    max_equity: float

    # Calculated metrics
    win_rate: float
    profit_factor: float

    # Cost breakdown
    total_spread_cost: float
    total_commission: float
    total_swap: float
    total_fees: float

    # Metadata
    currency: str
    broker_name: str
    current_conversion_rate: float


@dataclass
class PortfolioStats(BasePortfolioStats):
    """
    Single scenario portfolio performance statistics.

    Adds balance tracking on top of base statistics.
    """
    current_balance: float
    initial_balance: float


@dataclass
class AggregatedPortfolioStats(BasePortfolioStats):
    """
    Aggregated portfolio statistics across multiple scenarios.

    Adds scenario tracking for extremes (worst drawdown, best equity).
    Used exclusively for currency-grouped aggregations.
    """
    max_drawdown_scenario: str
    max_equity_scenario: str


@dataclass
class AggregatedPortfolio:
    """
    Aggregated portfolio statistics for scenarios with same currency.

    Groups scenarios by account currency to avoid cross-currency conversion issues.
    Includes time divergence warning for scenarios spanning significant time periods.

    Args:
        currency: Account currency (USD, EUR, JPY, etc.)
        scenario_names: List of scenario names in this group
        scenario_count: Number of scenarios aggregated
        portfolio_stats: Aggregated portfolio statistics
        execution_stats: Aggregated order execution statistics
        cost_breakdown: Aggregated cost breakdown
        time_span_days: Days between earliest and latest tick in group
        has_time_divergence_warning: True if time span exceeds threshold
    """
    currency: str
    scenario_names: List[str]
    scenario_count: int
    portfolio_stats: PortfolioStats
    execution_stats: ExecutionStats
    cost_breakdown: CostBreakdown
    time_span_days: int
    has_time_divergence_warning: bool
