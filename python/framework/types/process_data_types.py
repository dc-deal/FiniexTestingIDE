"""
FiniexTestingIDE - Process Data Types (CORRECTED)
Type definitions for ProcessPool-based execution

CORRECTIONS:
- strategy_config: Complete strategy config for worker creation
- scenario_set_name: For logger initialization
- run_timestamp: Shared timestamp across all processes
- warmup_requirements: REMOVED (validation skipped)
- account_currency: Changed from 'currency' for clarity (auto-detection support)
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dateutil import parser
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.configuration.app_config_manager import AppConfigManager
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.broker_types import BrokerType
from python.framework.types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_config_types import MarketType
from python.framework.types.performance_stats_types import DecisionLogicStats, WorkerCoordinatorPerformanceStats, WorkerPerformanceStats
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_trade_record_types import TradeRecord
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.market_data_types import TickData
from python.framework.types.order_types import OrderResult
from python.framework.types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_stats_types import CostBreakdown, ExecutionStats
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


# ============================================================================
# REQUIREMENTS COLLECTION (Phase 0)
# ============================================================================

@dataclass
class TickRequirement:
    """
    Tick data requirement for a single scenario.

    Supports both tick-limited and timespan modes.
    """
    scenario_name: str
    # Broker type identifier (e.g., 'mt5', 'kraken_spot')
    broker_type: str
    symbol: str
    start_time: datetime
    end_time: Optional[datetime] = None  # None = tick-limited mode
    max_ticks: Optional[int] = None      # None = timespan mode
    start_readable: str = ''
    end_readable: str = ''


@dataclass
class BarRequirement:
    """
    Bar data requirement for warmup.

    Extracted from worker configurations.
    """
    scenario_name: str
    # Broker type identifier (e.g., 'mt5', 'kraken_spot')
    broker_type: str
    symbol: str
    timeframe: str
    warmup_count: int
    start_time: datetime  # For filtering specific warmup range
    start_readable: str = ''


@dataclass
class RequirementsMap:
    """
    Aggregated requirements from all scenarios.

    Used by SharedDataPreparator to load data efficiently.
    Deduplicates overlapping requirements across scenarios.
    """
    tick_requirements: List[TickRequirement] = field(default_factory=list)
    bar_requirements: List[BarRequirement] = field(default_factory=list)

    def add_tick_requirement(self, req: TickRequirement) -> None:
        """Add tick requirement (deduplication handled in finalize)."""
        self.tick_requirements.append(req)

    def add_bar_requirement(self, req: BarRequirement) -> None:
        """Add bar requirement (deduplication handled in finalize)."""
        self.bar_requirements.append(req)


# ============================================================================
# SHARED DATA PACKAGE (Phase 1 → Process)
# ============================================================================

@dataclass
class ProcessDataPackage:
    """
    Scenario-specific data package for process handover.

    OPTIMIZATION (Phase 1): Each scenario receives only its required data.
    Previous: 1 global package (61 MB) → All processes
    Current: N scenario packages (3-5 MB each) → Individual processes
    Result: 5x reduction in pickle overhead (30s → 6s for 20 scenarios)

    IMMUTABILITY: All collections are tuples for CoW optimization.
    Read-only access in subprocess = 0 memory copy overhead.

    DATA STRUCTURE:
    - ticks: Dict with single key (scenario symbol) → Tuple of Ticks
    - bars: Dict with keys matching this scenario's symbol + start_time

    METADATA: Minimal human-readable info for debugging/logging.
    NOT used in processing - only for monitoring.
    """
    # === CORE DATA (immutable, CoW-shared) ===
    # Ticks: Symbol → Tuple of Tick objects
    # NOTE: Scenario-specific package has only 1 symbol key
    ticks: Dict[str, Tuple[Any, ...]]

    # Bars: (Symbol, Timeframe, StartTime) → Tuple of Bar objects
    # NOTE: All keys match this scenario's symbol + start_time
    bars: Dict[Tuple[str, str, datetime], Tuple[Any, ...]]

    # === Broker config for subprocess re-hydration ===
    # Broker config: Serialized dict for subprocess re-hydration (CoW-safe)
    # Loaded once in main process, shared via CoW to all subprocesses
    # Each subprocess re-hydrates BrokerConfig from this dict (no file I/O)
    broker_configs: Tuple[str, Tuple[Tuple[str, Any], ...]]

    # === METADATA (human-readable, minimal overhead) ===
    # Tick counts per symbol (for progress logging)
    tick_counts: Dict[str, int] = field(default_factory=dict)

    # Tick time ranges per symbol (for validation logging)
    tick_ranges: Dict[str, Tuple[datetime, datetime]
                      ] = field(default_factory=dict)

    # Bar counts per (symbol, timeframe, start_time)
    bar_counts: Dict[Tuple[str, str, datetime],
                     int] = field(default_factory=dict)


# ============================================================================
# SCENARIO CONFIG (Phase 1 → Process)
# ============================================================================

@dataclass
class ProcessScenarioConfig:
    """
    Serializable scenario configuration for process execution.

    Pure data structure - no complex objects, no locks, no file handles.
    Can be pickled for ProcessPoolExecutor.

    Created from SingleScenario + AppConfig in main process.

    CORRECTED:
    - strategy_config: Complete config (for create_workers_from_config)
    - scenario_set_name: For logger initialization
    - run_timestamp: Shared across all processes
    - warmup_requirements: REMOVED (validation skipped)
    - account_currency: Changed from 'currency' (supports "auto" detection)
    """
    # === IDENTITY ===
    name: str
    symbol: str
    scenario_index: int

    # === TIME RANGE ===
    start_time: datetime
    end_time: Optional[datetime] = None
    max_ticks: Optional[int] = None

    # === STRATEGY CONFIGURATION ===
    # Complete strategy_config for worker creation
    strategy_config: Dict[str, Any] = field(default_factory=dict)

    # Decision logic type (for compatibility)
    decision_logic_type: str = ""
    decision_logic_config: Dict[str, Any] = field(default_factory=dict)

    # === EXECUTION SETTINGS ===
    parallel_workers: bool = False
    parallel_threshold: float = 1.0
    strict_parameter_validation: bool = True

    # === LOGGER METADATA ===
    # For ScenarioLogger initialization
    scenario_set_name: str = ""
    run_timestamp: str = ""

    # Live Stats Config
    live_stats_config: LiveStatsExportConfig = None

    # === TRADING SIMULATOR CONFIG ===
    broker_type: BrokerType = None
    market_type: MarketType = None
    initial_balance: float = 0
    account_currency: str = ''  # Changed from 'currency' - supports "auto"
    seeds: Dict[str, Any] = field(default_factory=dict)
    executor_mode: str = 'simulation'  # "simulation" | "live_dry_run"

    # === HISTORY LIMITS ===
    bar_max_history: int = 1000
    order_history_max: int = 10000
    trade_history_max: int = 5000

    @staticmethod
    def from_scenario(
        scenario: SingleScenario,
        app_config_loader: AppConfigManager,
        scenario_index: int,
        scenario_set_name: str,
        run_timestamp: str,
        live_stats_config: LiveStatsExportConfig
    ) -> 'ProcessScenarioConfig':
        """
        Create ProcessScenarioConfig from SingleScenario + AppConfig.

        CORRECTED:
        - Uses complete strategy_config (for worker creation)
        - Adds scenario_set_name and run_timestamp
        - Reads 'account_currency' from trade_simulator_config

        Args:
            scenario: SingleScenario object
            app_config: Application configuration
            scenario_index: Index in scenario list
            scenario_set_name: Name of scenario set (for logger)
            run_timestamp: Shared timestamp (for logger)

        Returns:
            Serializable ProcessScenarioConfig
        """
        # Parse datetime
        start_time = scenario.start_date
        end_time = scenario.end_date if scenario.end_date else None

        # Use complete strategy_config
        # This is already merged (global + scenario overrides) in scenario_config_loader
        strategy_config = scenario.strategy_config

        # Extract decision logic (for compatibility, already in strategy_config)
        decision_logic_type = strategy_config.get('decision_logic_type', '')
        decision_logic_config = strategy_config.get(
            'decision_logic_config', {})

        # Execution config
        exec_config = scenario.execution_config or app_config_loader.get_execution_config()
        parallel_workers = exec_config.get('parallel_workers', False)
        parallel_threshold = exec_config.get(
            "worker_parallel_threshold_ms", 1.0
        )
        strict_parameter_validation = exec_config.get(
            "strict_parameter_validation", True
        )

        # accountt currency is set in scenario_validator after detecting the correct value (see "auto"-mode)
        account_currency = scenario.account_currency
        initial_balance = scenario.trade_simulator_config.get(
            'initial_balance')
        seeds = scenario.trade_simulator_config.get(
            'seeds')
        executor_mode = scenario.trade_simulator_config.get(
            'executor_mode', 'simulation')

        # Derive market_type from broker_type
        market_config_manager = MarketConfigManager()
        market_type = market_config_manager.get_market_type(
            scenario.broker_type.value)

        # Default live stats config if not provided
        if live_stats_config is None:
            live_stats_config = LiveStatsExportConfig(enabled=False)

        return ProcessScenarioConfig(
            name=scenario.name,
            symbol=scenario.symbol,
            scenario_index=scenario_index,
            start_time=start_time,
            end_time=end_time,
            max_ticks=scenario.max_ticks,
            strategy_config=strategy_config,  # Complete config
            decision_logic_type=decision_logic_type,
            decision_logic_config=decision_logic_config,
            parallel_workers=parallel_workers,
            parallel_threshold=parallel_threshold,
            strict_parameter_validation=strict_parameter_validation,
            scenario_set_name=scenario_set_name,
            run_timestamp=run_timestamp,  # extracted from json, put into type.
            live_stats_config=live_stats_config,
            broker_type=scenario.broker_type,
            market_type=market_type,
            initial_balance=initial_balance,
            account_currency=account_currency,
            seeds=seeds,
            executor_mode=executor_mode,
            bar_max_history=app_config_loader.get_bar_max_history(),
            order_history_max=app_config_loader.get_order_history_max(),
            trade_history_max=app_config_loader.get_trade_history_max()
        )


@dataclass
class ProcessPreparedDataObjects:
    """
        Prepared Objects from process_startup_preparation
        All those must be created in minimum time
    """
    worker_coordinator: WorkerOrchestrator = None
    trade_simulator: AbstractTradeExecutor = None
    bar_rendering_controller: BarRenderingController = None
    decision_logic: AbstractDecisionLogic = None
    scenario_logger: ScenarioLogger = None
    ticks: Tuple[TickData, ...] = None


@dataclass
class ProcessProfileData:
    """
        Profiling from Tick Loop, 
        various profiling points.
    """
    profile_times: Dict[Any, float] = None
    profile_counts: Dict[Any, int] = None


@dataclass
class TickRangeStats:
    """
        Tick time range (internal tick timestamps)
    """
    tick_count: int = 0,
    first_tick_time: Optional[datetime] = None,
    last_tick_time: Optional[datetime] = None,
    tick_timespan_seconds: Optional[float] = None


@dataclass
class ProcessTickLoopResult:
    """
    Result info from Tick Loop, after execution.

    Contains three separate performance statistic sources:
    - decision_statistics: From DecisionLogicPerformanceTracker
    - worker_statistics: From WorkerPerformanceTracker (per worker)
    - coordination_statistics: From WorkerOrchestrator
    """
    # Decision logic statistics (signals + performance)
    decision_statistics: DecisionLogicStats = None

    # Worker statistics (list of per-worker stats)
    worker_statistics: List[WorkerPerformanceStats] = None

    # Coordination statistics (parallel execution, ticks processed)
    coordination_statistics: WorkerCoordinatorPerformanceStats = None

    # Trading results
    portfolio_stats: PortfolioStats = None
    execution_stats: ExecutionStats = None
    cost_breakdown: CostBreakdown = None

    # Trade-by-trade history for P&L verification
    trade_history: List[TradeRecord] = None

    # Order history (all orders including rejections)
    order_history: List[OrderResult] = None

    # Pending order statistics (latency, outcomes, anomalies)
    pending_stats: PendingOrderStats = None

    # Profiling data
    profiling_data: ProcessProfileData = None
    tick_range_stats: TickRangeStats = None

    # Error handling
    tick_loop_error: Optional[Exception] = None


# ============================================================================
# PROCESS RESULT (Process → Phase 2)
# ============================================================================

@dataclass
class ProcessResult:
    """
    Result from scenario execution in subprocess.

    SERIALIZABLE: All fields are JSON-compatible for later API export.
    Contains everything needed for reports and aggregation.

    SUCCESS PATH: Contains complete execution results.
    ERROR PATH: Contains detailed error information.
    """
    # === STATUS ===
    success: bool = False
    scenario_name: str = ''
    scenario_index: int = ''

    # === EXECUTION TIME ===
    execution_time_ms: float = 0.0

    # === ERROR INFORMATION (success=False) ===
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback: Optional[str] = None

    # Data from the tick loop
    tick_loop_results: ProcessTickLoopResult = None

    # logger lines to print after scenario run.
    scenario_logger_buffer: list[tuple[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'success': self.success,
            'scenario_name': self.scenario_name,
            'scenario_index': self.scenario_index,
            'execution_time_ms': self.execution_time_ms,
            'error_type': self.error_type,
            'error_message': self.error_message,
            'traceback': self.traceback,
        }
