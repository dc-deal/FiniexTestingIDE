"""
FiniexTestingIDE - Portfolio Aggregation Types
Types for currency-grouped portfolio aggregation
"""

from dataclasses import dataclass
from typing import List

from python.framework.types.trading_env_types import (
    PortfolioStats,
    ExecutionStats,
    CostBreakdown
)


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
