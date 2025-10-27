"""
FiniexTestingIDE - Process Data Types (CORRECTED)
Type definitions for ProcessPool-based execution

CORRECTIONS:
- strategy_config: Complete strategy config for worker creation
- scenario_set_name: For logger initialization
- run_timestamp: Shared timestamp across all processes
- warmup_requirements: REMOVED (validation skipped)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dateutil import parser
from python.components.logger.scenario_logger import ScenarioLogger
from python.configuration.app_config_loader import AppConfigLoader
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.types.performance_stats_types import BatchPerformanceStats
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.tick_types import TickData
from python.framework.types.trading_env_types import CostBreakdown, ExecutionStats, PortfolioStats
from python.framework.workers.worker_coordinator import WorkerCoordinator


# ============================================================================
# REQUIREMENTS COLLECTION (Phase 0)
# ============================================================================

@dataclass
class TickRequirement:
    """
    Tick data requirement for a single scenario.

    Supports both tick-limited and timespan modes.
    """
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
    FUNCTIONAL data package for process handover.
    Contains ONLY REQUIRED data - NO unnecessary metadata!

    IMMUTABILITY: All collections are tuples for CoW optimization.
    Read-only access in subprocess = 0 memory copy overhead.

    METADATA: Minimal human-readable info for debugging/logging.
    NOT used in processing - only for monitoring.
    """
    # === CORE DATA (immutable, CoW-shared) ===
    # Ticks: Symbol → Tuple of Tick objects
    ticks: Dict[str, Tuple[Any, ...]]

    # Bars: (Symbol, Timeframe, StartTime) → Tuple of Bar objects
    # Key includes start_time for scenario-specific warmup filtering
    bars: Dict[Tuple[str, str, datetime], Tuple[Any, ...]]

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
    # CORRECTED: Complete strategy_config for worker creation
    strategy_config: Dict[str, Any] = field(default_factory=dict)

    # Decision logic type (for compatibility)
    decision_logic_type: str = ""
    decision_logic_config: Dict[str, Any] = field(default_factory=dict)

    # === EXECUTION SETTINGS ===
    parallel_workers: bool = False
    parallel_threshold: float = 1.0

    # === LOGGER METADATA ===
    # CORRECTED: For ScenarioLogger initialization
    scenario_set_name: str = ""
    run_timestamp: str = ""

    broker_config_path: str = '',
    initial_balance: float = 0,
    currency: str = ''

    @staticmethod
    def from_scenario(
        scenario: SingleScenario,
        app_config: AppConfigLoader,
        scenario_index: int,
        scenario_set_name: str,
        run_timestamp: str
    ) -> 'ProcessScenarioConfig':
        """
        Create ProcessScenarioConfig from SingleScenario + AppConfig.

        CORRECTED:
        - Uses complete strategy_config (for worker creation)
        - Adds scenario_set_name and run_timestamp

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
        start_time = parser.parse(scenario.start_date)
        end_time = parser.parse(
            scenario.end_date) if scenario.end_date else None

        # CORRECTED: Use complete strategy_config
        # This is already merged (global + scenario overrides) in config_loader
        strategy_config = scenario.strategy_config

        # Extract decision logic (for compatibility, already in strategy_config)
        decision_logic_type = strategy_config.get('decision_logic_type', '')
        decision_logic_config = strategy_config.get(
            'decision_logic_config', {})

        # Execution config
        exec_config = scenario.execution_config or app_config.get_execution_config()
        parallel_workers = exec_config.get('parallel_workers', False)
        parallel_threshold = exec_config.get(
            "worker_parallel_threshold_ms", 1.0
        )

        broker_config_path = scenario.trade_simulator_config.get(
            'broker_config_path')
        initial_balance = scenario.trade_simulator_config.get(
            'initial_balance')
        currency = scenario.trade_simulator_config.get('currency')

        return ProcessScenarioConfig(
            name=scenario.name,
            symbol=scenario.symbol,
            scenario_index=scenario_index,
            start_time=start_time,
            end_time=end_time,
            max_ticks=scenario.max_ticks,
            strategy_config=strategy_config,  # CORRECTED: Complete config
            decision_logic_type=decision_logic_type,
            decision_logic_config=decision_logic_config,
            parallel_workers=parallel_workers,
            parallel_threshold=parallel_threshold,
            scenario_set_name=scenario_set_name,  # CORRECTED: Added
            run_timestamp=run_timestamp,           # CORRECTED: Added
            broker_config_path=broker_config_path,
            initial_balance=initial_balance,
            currency=currency
        )


@dataclass
class ProcessPreparedDataObjects:
    """
        Prepared Objects from process_startup_preparation
        All those must be created in minimum time
    """
    coordinator: WorkerCoordinator = None
    trade_simulator: TradeSimulator = None
    bar_rendering_controller: BarRenderingController = None
    decision_logic: AbstractDecisionLogic = None
    scenario_logger: ScenarioLogger = None
    ticks: List[TickData] = None


@dataclass
class ProcessProfileData:
    """
        Profiling from Tick Loop, 
        various profiling points.
    """
    profile_times: Dict[Any, float] = None
    profile_counts: Dict[Any, int] = None


@dataclass
class ProcessTickLoopResult:
    """
        Result info from Tick Loop, after execution.
    """
    performance_stats: BatchPerformanceStats = None,
    portfolio_stats: PortfolioStats = None,
    execution_stats: ExecutionStats = None,
    cost_breakdown: CostBreakdown = None,
    profiling_data: ProcessProfileData = None


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
    symbol: str = ''
    scenario_index: int = ''

    # === EXECUTION TIME ===
    execution_time_ms: float = 0.0

    # Signals generated
    signal_count: int = 0

    # === ERROR INFORMATION (success=False) ===
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback: Optional[str] = None

    # Data from the tick loop
    tick_loop_results: ProcessTickLoopResult = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'success': self.success,
            'scenario_name': self.scenario_name,
            'scenario_index': self.scenario_index,
            'execution_time_ms': self.execution_time_ms,
            'error_type': self.error_type,
            'error_message': self.error_message,
            'traceback': self.traceback
        }


@dataclass
class BatchExecutionSummary:
    """Summary of batch execution results."""
    success: bool
    scenarios_count: int
    summary_execution_time: float
    scenario_list:  List[ProcessResult] = None
