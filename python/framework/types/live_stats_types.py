"""
FiniexTestingIDE - Live Statistics Types
Type definitions for real-time scenario execution tracking

Contains:
- ScenarioStatus: Enum for scenario execution states
- LiveScenarioStats: Real-time statistics for running scenarios
"""

from dataclasses import dataclass
from enum import Enum


class ScenarioStatus(Enum):
    """
    Live scenario execution states.

    Flow:
        INITIALIZED → WARMUP → WARMUP_COMPLETE → RUNNING → COMPLETED

    States:
        INITIALIZED: Scenario created, waiting to start
        WARMUP: Warming up (loading data, preparing environment)
        WARMUP_COMPLETE: Warmup finished, waiting for batch sync
        RUNNING: Active trading execution
        COMPLETED: Scenario finished successfully
    """
    INITIALIZED = "initialized"
    WARMUP = "warmup"
    WARMUP_COMPLETE = "warmup_complete"
    RUNNING = "running"
    COMPLETED = "completed"
    FINISHED_WITH_ERROR = "finished_with_error"


@dataclass
class LiveScenarioStats:
    """
    Real-time statistics for a running scenario.

    Used by ScenarioSetPerformanceManager to track live execution progress.
    Replaces the previous Dict[str, Any] approach with a strongly-typed class.

    Attributes:
        scenario_name: Name of the scenario
        symbol: Trading symbol (e.g., "EURUSD")
        total_ticks: Total number of ticks to process
        ticks_processed: Number of ticks processed so far
        progress_percent: Completion percentage (0-100)
        total_trades: Total number of trades executed
        winning_trades: Number of winning trades
        losing_trades: Number of losing trades
        portfolio_value: Current portfolio value
        initial_balance: Starting portfolio balance
        status: Execution status (ScenarioStatus enum)
    """
    scenario_name: str
    symbol: str
    total_ticks: int = 0
    ticks_processed: int = 0
    progress_percent: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    portfolio_value: float = 0.0
    initial_balance: float = 0.0
    status: ScenarioStatus = ScenarioStatus.INITIALIZED
