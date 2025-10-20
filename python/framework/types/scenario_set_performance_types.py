"""
FiniexTestingIDE - Scenario Performance Types
Type definitions for scenario execution statistics

FULLY TYPED: Uses dataclasses from trading_env_types instead of generic dicts.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from python.framework.types.trading_env_types import PortfolioStats, ExecutionStats, CostBreakdown


@dataclass
class ScenarioPerformanceStats:
    """
    Performance statistics for a single scenario.

    Contains all performance data that was previously in batch_results dict.

    FULLY TYPED: portfolio_stats, execution_stats, cost_breakdown are now
    strongly-typed dataclasses instead of Dict[str, Any].
    """
    # Scenario metadata
    scenario_index: int  # Original position in scenario array
    scenario_name: str
    symbol: str

    # Execution stats
    ticks_processed: int
    signals_generated: int
    signals_gen_buy: int
    signals_gen_sell: int
    signal_rate: float
    success: bool
    portfolio_value: float
    initial_balance: float
    elapsed_time: float

    # Worker statistics
    worker_statistics: Dict[str, Any]

    # Decision logic
    decision_logic_name: str

    # Scenario requirement
    scenario_requirement: Dict[str, Any]

    # Optional: First 10 signals for inspection
    sample_signals: List[Dict] = field(default_factory=list)

    # Portfolio & Trading Stats (per scenario) - TYPED!
    # Each scenario gets its own TradeSimulator, stats stored here
    portfolio_stats: PortfolioStats = field(default_factory=lambda: PortfolioStats(
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        total_profit=0.0,
        total_loss=0.0,
        max_drawdown=0.0,
        max_equity=0.0,
        win_rate=0.0,
        profit_factor=0.0,
        total_spread_cost=0.0,
        total_commission=0.0,
        total_swap=0.0,
        total_fees=0.0
    ))

    execution_stats: ExecutionStats = field(default_factory=lambda: ExecutionStats(
        orders_sent=0,
        orders_executed=0,
        orders_rejected=0,
        total_commission=0.0,
        total_spread_cost=0.0
    ))

    cost_breakdown: CostBreakdown = field(default_factory=lambda: CostBreakdown(
        total_spread_cost=0.0,
        total_commission=0.0,
        total_swap=0.0,
        total_fees=0.0
    ))

    # NEW: Profiling data from tick loop
    # Structure:
    # {
    #     'profile_times': {'trade_simulator': 123.45, 'bar_rendering': 67.89, ...},
    #     'profile_counts': {'trade_simulator': 100, 'bar_rendering': 100, ...},
    #     'total_per_tick': 456.78
    # }
    profiling_data: Dict[str, Any] = field(default_factory=dict)
