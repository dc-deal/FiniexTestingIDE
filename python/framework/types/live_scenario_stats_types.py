"""
FiniexTestingIDE - Live Statistics Types
Type definitions for real-time scenario execution tracking

Contains:
- ScenarioStatus: Enum for scenario execution states
- LiveStatsExportConfig: Configuration for live stats exports
- LiveScenarioStats: Real-time statistics for running scenarios
"""

from dataclasses import dataclass
from typing import Optional

from python.framework.types.live_stats_config_types import ScenarioStatus
from python.framework.types.trading_env_types import PortfolioStats


@dataclass
class LiveScenarioStats:
    """
    Real-time statistics for a running scenario.

    Two-tier system:
    - Basic Mode: Essential progress + portfolio basics
    - Detailed Mode: Includes full PortfolioStats object

    Attributes:
        scenario_name: Name of the scenario
        symbol: Trading symbol (e.g., "EURUSD")
        scenario_index: Index in scenario list
        total_ticks: Total number of ticks to process
        ticks_processed: Number of ticks processed so far
        progress_percent: Completion percentage (0-100)
        status: Execution status (ScenarioStatus enum)
        first_tick_time: ISO timestamp of first tick
        current_tick_time: ISO timestamp of current tick
        tick_timespan_seconds: Elapsed simulation time
        current_balance: Current portfolio balance
        initial_balance: Starting portfolio balance
        total_trades: Total number of trades executed
        winning_trades: Number of winning trades
        losing_trades: Number of losing trades
        portfolio_dirty: Dirty flag for lazy evaluation transparency
        portfolio_stats: Full PortfolioStats (optional, detailed mode only)
    """
    scenario_name: str
    symbol: str
    scenario_index: int

    # Progress
    total_ticks: int = 0
    ticks_processed: int = 0
    progress_percent: float = 0.0
    status: ScenarioStatus = ScenarioStatus.INITIALIZED

    # In-Time tracking
    first_tick_time: Optional[str] = None
    current_tick_time: Optional[str] = None
    tick_timespan_seconds: float = 0.0

    # Basic Portfolio Info (always included)
    current_balance: float = 0.0
    initial_balance: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    portfolio_dirty: bool = False

    # Detailed Portfolio Info (optional - two-tier!)
    portfolio_stats: Optional[PortfolioStats] = None
