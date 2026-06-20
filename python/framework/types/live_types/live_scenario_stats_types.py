"""
FiniexTestingIDE - Live Statistics Types
Type definitions for real-time scenario execution tracking

Contains:
- LiveScenarioStats: Real-time statistics for a running scenario (full progress frame)
- LiveStatusFrame: Lightweight status-only update (warmup / lifecycle transitions)
"""

from dataclasses import dataclass
from typing import Dict, Optional

from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats


@dataclass
class LiveScenarioStats:
    """
    Real-time statistics for a running scenario (the full progress frame).

    The shared identity + portfolio basics live on `core` (LiveCoreSnapshot);
    the fields here are simulation-batch specific (progress, in-time tracking,
    optional detailed exports).

    Two-tier system:
    - Basic Mode: core + essential progress
    - Detailed Mode: adds the full PortfolioStats / current bars

    Attributes:
        core: Shared live-telemetry core (symbol, balances, trades, awareness)
        scenario_name: Name of the scenario
        scenario_index: Index in scenario list
        total_ticks: Total number of ticks to process
        progress_percent: Completion percentage (0-100)
        status: Execution status (ScenarioStatus enum)
        first_tick_time: ISO timestamp of first tick
        current_tick_time: ISO timestamp of current tick
        tick_timespan_seconds: Elapsed simulation time
        portfolio_dirty_flag: Dirty flag for lazy evaluation transparency
        portfolio_stats: Full PortfolioStats (optional, detailed mode only)
        current_bars: Serialized current bars per timeframe (optional, detailed mode only)
    """
    core: LiveCoreSnapshot
    scenario_name: str
    scenario_index: int

    # Progress
    total_ticks: int = 0
    progress_percent: float = 0.0
    status: ScenarioStatus = ScenarioStatus.INITIALIZED

    # In-Time tracking
    first_tick_time: Optional[str] = None
    current_tick_time: Optional[str] = None
    tick_timespan_seconds: float = 0.0

    # Basic Portfolio Info (batch-specific)
    portfolio_dirty_flag: bool = False

    # Detailed exports (optional - two-tier!). Not rendered by the console today —
    # carried typed for a future visual-channel consumer (#379).
    portfolio_stats: Optional[PortfolioStats] = None
    current_bars: Optional[Dict[str, Dict]] = None


@dataclass
class LiveStatusFrame:
    """
    Lightweight status-only update (warmup / lifecycle transitions).

    Carried on the same live queue as LiveScenarioStats; the consumer dispatches
    by type and touches only the cached scenario's status — it must NOT overwrite
    the progress fields (a status update can arrive before any ticks).

    Attributes:
        scenario_index: Index in scenario list
        scenario_name: Name of the scenario
        status: New execution status (ScenarioStatus enum)
    """
    scenario_index: int
    scenario_name: str
    status: ScenarioStatus
