"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.logging.system_info_writer import write_system_version_parameters
from python.configuration.app_config_manager import AppConfigManager
from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.types.validation_types import ValidationResult
from python.framework.utils.scenario_set_utils import ScenarioSetUtils


@dataclass
class SingleScenario:
    """Test scenario configuration for batch testing"""
    # identification for Scenario, must be unique
    name: str
    # internal index, the only source of truth for scenario picking.
    scenario_index: int
    symbol: str

    # ============================================
    # DATA SOURCE (NEW - REQUIRED)
    # ============================================
    # Determines which tick/bar data collection to load from
    # Examples: "mt5", "kraken_spot"
    # This is SEPARATE from broker_type (trading simulation config)
    data_broker_type: str  # REQUIRED - no default!

    start_date: datetime
    end_date: Optional[datetime] = None
    max_ticks: Optional[int] = None
    data_mode: str = "realistic"
    enabled: bool = True  # Default: enabled
    # ============================================
    # STRATEGY PARAMETERS
    # ============================================
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    execution_config: Optional[Dict[str, Any]] = None

    # TradeSimulator configuration (per scenario)
    # broker_type here is for TRADING simulation, not data source!
    broker_type: BrokerType = None
    trade_simulator_config: Optional[Dict[str, Any]] = None

    # Stress test configuration (per scenario, cascaded from global)
    stress_test_config: Optional[Dict[str, Any]] = None

    account_currency: str = ''
    configured_account_currency: str = ''

    # === VALIDATION TRACKING ===
    validation_result: List[ValidationResult] = field(default_factory=list)

    def __post_init__(self):
        if self.name is None:
            raise ValueError(
                "Property name of scenario array Objects must be filled.")

        # Smart Defaults für Execution Config
        if self.execution_config is None:
            self.execution_config = {
                # ============================================
                # EXECUTION CONFIGURATION STANDARD
                # ============================================
                # Worker-Level Parallelization
                # True = Workers parallel (gut bei 4+ workers)
                "parallel_workers": None,  # Auto-detect
                "worker_parallel_threshold_ms": 1.0,  # Nur parallel wenn Worker >1ms
                # Künstliche Last - NUR für Heavy workers
                # Ist eher für self-testing szenarios und stress tests gedacht.
                "artificial_load_ms": 5.0,  # 5ms pro Worker
                # Performance Tuning
                "adaptive_parallelization": True,  # Auto-detect optimal mode
                "log_performance_stats": True,  # Log timing statistics
                # Parameter Validation
                # True = abort on boundary violations, False = warn only
                "strict_parameter_validation": True,
            }

    def to_config_string_for_display(self) -> str:
        """
        Display string for scenario configuration.

        Returns:
            Human-readable config summary
        """
        return (
            f"Scenario: {self.name}\n"
            f"  Data Source: {self.data_broker_type}\n"
            f"  Symbol: {self.symbol}\n"
            f"  Period: {self.start_date} → {self.end_date}\n"
            f"  Max Ticks: {self.max_ticks or 'unlimited'}\n"
            f"  Enabled: {self.enabled}"
        )

    def is_valid(self) -> bool:
        """
        Check if scenario passed validation.

        Returns:
            True if no validation result or validation passed
        """
        if not self.validation_result:
            return True

        # Prüfe alle ValidationResult-Objekte
        return all(v.is_valid for v in self.validation_result)


@dataclass
class LoadedScenarioConfig:
    """Result of config loading - raw data before ScenarioSet creation"""
    scenario_set_name: str
    scenarios: List[SingleScenario]
    config_path: Path


class ScenarioSet:
    """Self-contained scenario set with its own logging infrastructure"""

    def __init__(self, scenario_config: LoadedScenarioConfig, app_config: AppConfigManager):

        self.scenario_set_name = scenario_config.scenario_set_name
        self._scenarios = scenario_config.scenarios
        self.config_path = scenario_config.config_path
        self.app_config = app_config

        # ScenarioSet erstellt SEINE EIGENEN Logger
        self._run_timestamp = datetime.now(
            timezone.utc)

        self.logger = ScenarioLogger(
            scenario_set_name=self.scenario_set_name,
            scenario_name='global_log',
            run_timestamp=self._run_timestamp
        )
        self.printed_summary_logger = ScenarioLogger(
            scenario_set_name=self.scenario_set_name,
            scenario_name='summary',
            run_timestamp=self._run_timestamp
        )

    @property
    def run_timestamp(self) -> datetime:
        """Expose run_timestamp for easy access"""
        return self._run_timestamp

    def copy_config_snapshot(self) -> None:
        """
        Copy config snapshot to log directory.
        Call explicitly before execution starts.
        """
        # copy file snapshot to log folder
        scenario_set_utils = ScenarioSetUtils(
            config_snapshot_path=self.config_path,
            scenario_log_path=self.logger.get_log_dir(),
        )
        scenario_set_utils.copy_config_snapshot()

    def write_scenario_system_info_log(self):
        """
        Write System Information for Performance Tracking
        """
        if self.app_config.get_logging_write_system_info():
            system_info_logger = ScenarioLogger(
                scenario_set_name=self.scenario_set_name,
                scenario_name='system_info',
                run_timestamp=self.logger.get_run_timestamp()
            )

            write_system_version_parameters(system_info_logger)
            system_info_logger.close(flush_buffer=True)

    def get_valid_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return [scenario for scenario in self._scenarios if scenario.is_valid()]

    def get_failed_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return [scenario for scenario in self._scenarios if not scenario.is_valid()]

    def get_all_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return self._scenarios


@dataclass
class BrokerScenarioInfo:
    """Internal mapping of broker to scenarios (used for logging)."""
    config_path: str
    scenarios: List[str]
    symbols: Set[str]
    broker_config: BrokerConfig


@dataclass
class ScenarioSetMetadata:
    """
    Metadata about a scenario set config file

    Used for discovery and listing of available scenario sets
    """
    # Basic info
    filename: str
    scenario_set_name: str
    total_count: int
    enabled_count: int
    disabled_count: int
    symbols: list[str]
    config_path: Path

    # Time analysis
    timespan_scenario_count: int
    total_timespan_seconds: float
    tick_scenario_count: int
    total_ticks: int

    # Strategy info
    decision_logic_type: str | None
    is_mixed_decision_logic: bool
    worker_count: int | None
    is_mixed_workers: bool
