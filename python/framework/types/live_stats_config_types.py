from dataclasses import dataclass
from enum import Enum
from multiprocessing import Queue
from typing import Any, Dict, Optional

from python.framework.types.market_data_types import TickData


@dataclass
class ProcessLiveSetup:
    """
    Config initialization for live view
    """
    live_queue: Optional[Queue] = None
    last_update_time: float = 0
    update_interval_sec: float = 0
    first_tick: Optional[TickData] = None
    live_enabled: bool = False
    tick_count: int = 0


class ScenarioStatus(Enum):
    """
    Live scenario execution states.

    Flow (BatchOrchestrator level):
        INITIALIZED → WARMUP_DATA_TICKS → WARMUP_DATA_BARS → WARMUP_TRADER → [Submit to Pool]

    Flow (Subprocess level):
        INIT_PROCESS → RUNNING → COMPLETED

    States:
        INITIALIZED: Scenario object created (BatchOrchestrator)
        WARMUP_COVERAGE: Loading Gap Report, Data Coverage for execution validation
        WARMUP_DATA_TICKS: Loading ticks (BatchOrchestrator, Phase 1)
        WARMUP_DATA_BARS: Loading bars (BatchOrchestrator, Phase 1)
        WARMUP_TRADER: Loading broker config (BatchOrchestrator, Phase 1)
        INIT_PROCESS: Process started, initializing objects (Subprocess)
        RUNNING: Tick loop running (Subprocess)
        COMPLETED: Tick loop finished successfully (Subprocess)
        FINISHED_WITH_ERROR: Error during init or run (Subprocess)
    """
    # BatchOrchestrator States (Main Process)
    INITIALIZED = "initialized"
    WARMUP_COVERAGE = "warmup_coverage"
    WARMUP_DATA_TICKS = "warmup_data_ticks"
    WARMUP_DATA_BARS = "warmup_data_bars"
    WARMUP_TRADER = "warmup_trader"

    # Subprocess States (ProcessPool)
    INIT_PROCESS = "init_process"
    RUNNING = "running"
    COMPLETED = "completed"
    FINISHED_WITH_ERROR = "finished_with_error"


@dataclass
class LiveStatsExportConfig:
    """
    Configuration for live stats exports.

    Serializable config passed to subprocesses.
    Controls which data is exported via queue.

    Args:
        enabled: Master switch for monitoring
        detailed_mode: Basic vs. Detailed mode
        export_portfolio_stats: Include full PortfolioStats
        export_performance_stats: Include BatchPerformanceStats
        export_current_bars: Include current M5, M30, etc.
        update_interval_sec: Time between updates (from tui_refresh_rate_ms)
    """
    enabled: bool = True
    detailed_mode: bool = False
    export_portfolio_stats: bool = False
    export_performance_stats: bool = False
    export_current_bars: bool = False
    update_interval_sec: float = 0.3

    @classmethod
    def from_app_config(
        cls,
        app_config: Dict[str, Any],
        scenario_count: int
    ) -> 'LiveStatsExportConfig':
        """
        Create config from app_config with threshold logic.

        Args:
            app_config: Application configuration
            scenario_count: Number of scenarios in batch

        Returns:
            LiveStatsExportConfig with resolved settings
        """
        monitoring = app_config.get('monitoring', {})

        # HARD DISABLE CHECK - Master switch
        enabled = monitoring.get('enabled', True)
        if not enabled:
            return cls(
                enabled=False,
                detailed_mode=False,
                export_portfolio_stats=False,
                export_performance_stats=False,
                export_current_bars=False,
                update_interval_sec=0.0
            )

        # Detailed mode with threshold
        detailed_enabled = monitoring.get('detailed_live_stats', True)
        detail_threshold = monitoring.get('detailed_live_stats_threshold', 3)
        use_detailed = detailed_enabled and (
            scenario_count <= detail_threshold)

        # Export settings (only if detailed mode)
        exports = monitoring.get('detailed_live_stats_exports', {})
        export_portfolio = exports.get(
            'export_portfolio_stats', True) if use_detailed else False
        export_performance = exports.get(
            'export_performance_stats', False) if use_detailed else False
        export_bars = exports.get(
            'export_current_bars', False) if use_detailed else False

        # Update interval
        update_interval_ms = monitoring.get('tui_refresh_rate_ms', 300)
        update_interval_sec = update_interval_ms / 1000.0

        return cls(
            enabled=True,
            detailed_mode=use_detailed,
            export_portfolio_stats=export_portfolio,
            export_performance_stats=export_performance,
            export_current_bars=export_bars,
            update_interval_sec=update_interval_sec
        )
