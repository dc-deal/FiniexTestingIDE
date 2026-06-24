"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.logging.system_info_writer import write_system_version_parameters
from python.configuration.app_config_manager import AppConfigManager
from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.types.validation_types import ValidationResult
from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.types.config_types.robustness_config_types import RobustnessConfig, RobustnessRole
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
    # DATA SOURCE (REQUIRED)
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

    # OrderGuard configuration (per scenario, cascaded from global)
    # None = OrderGuardDefaults (60s cooldown, 2 rejections)
    order_guard_config: Optional[Dict[str, Any]] = None

    account_currency: str = ''

    # === ROBUSTNESS (#367) ===
    # Per-window IS/OOS label (manual config or generator-assigned). Default unassigned.
    role: RobustnessRole = RobustnessRole.UNASSIGNED
    # Volatility regime + trading session of the window — only populated for Profile Runs
    # (copied from the source ProfileBlock); empty for manual / blocks scenarios.
    regime: str = ''
    session: str = ''

    # === VALIDATION TRACKING ===
    # never init validation_result — its only purpose is to be filled
    # by batch phases to exclude failed scenarios from execution
    validation_result: List[ValidationResult] = field(
        default_factory=list, init=False)

    # === DATA SOURCE METADATA (populated during data loading) ===
    data_format_versions: List[str] = field(default_factory=list)

    # === PROFILE RUN METADATA (populated from GeneratorProfile) ===
    is_profile_run: bool = False

    def __post_init__(self):
        if self.name is None:
            raise ValueError(
                "Property name of scenario array Objects must be filled.")

        # Smart defaults for execution config
        if self.execution_config is None:
            self.execution_config = {
                # ============================================
                # EXECUTION CONFIGURATION STANDARD
                # ============================================
                # Worker-Level Parallelization
                # True = workers run in parallel (good with 4+ workers)
                "parallel_workers": None,  # Auto-detect
                "worker_parallel_threshold_ms": 1.0,  # Only parallelize when worker takes >1ms
                # Performance Tuning
                "adaptive_parallelization": True,  # Auto-detect optimal mode
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

        # Check all ValidationResult objects
        return all(v.is_valid for v in self.validation_result)


@dataclass
class LoadedScenarioConfig:
    """Result of config loading - raw data before ScenarioSet creation"""
    scenario_set_name: str
    scenarios: List[SingleScenario]
    config_path: Path
    generator_profiles: Optional[List[GeneratorProfile]] = None
    generator_profile_paths: Optional[List[Path]] = None
    # Set-wide robustness mode (#367); None → disabled (treated as RobustnessConfig()).
    robustness: Optional[RobustnessConfig] = None


class ScenarioSet:
    """Self-contained scenario set with its own logging infrastructure"""

    def __init__(self, scenario_config: LoadedScenarioConfig, app_config: AppConfigManager):

        self.scenario_set_name = scenario_config.scenario_set_name
        self._scenarios = scenario_config.scenarios
        self.config_path = scenario_config.config_path
        self.app_config = app_config
        self._generator_profiles = scenario_config.generator_profiles
        self._generator_profile_paths = scenario_config.generator_profile_paths
        self._robustness = scenario_config.robustness or RobustnessConfig()

        # ScenarioSet creates its own loggers
        self._run_timestamp = datetime.now(
            timezone.utc)

        self.logger = ScenarioLogger(
            scenario_set_name=self.scenario_set_name,
            scenario_name='global_log',
            run_timestamp=self._run_timestamp,
            use_global_log_level_for_console=True
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

        # Copy generator profile files for Profile Runs
        if self._generator_profile_paths:
            self._copy_generator_profiles()

    def _copy_generator_profiles(self) -> None:
        """Copy generator profile JSON files to scenario_run_configs/ in log directory."""
        log_dir = self.logger.get_log_dir()
        run_configs_dir = log_dir / 'scenario_run_configs'
        run_configs_dir.mkdir(exist_ok=True)

        for profile_path in self._generator_profile_paths:
            try:
                shutil.copy2(profile_path, run_configs_dir / profile_path.name)
            except Exception as e:
                vLog = get_global_logger()
                vLog.warning(
                    f"⚠️ Failed to copy profile {profile_path.name}: {e}")

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

    def get_generator_profiles(self) -> Optional[List[GeneratorProfile]]:
        """
        Get generator profiles for Profile Runs.

        Returns:
            List of GeneratorProfile objects, or None for normal runs
        """
        return self._generator_profiles

    def get_robustness_config(self) -> RobustnessConfig:
        """
        Get the set-wide robustness config (#367).

        Returns:
            RobustnessConfig (a disabled default when the set has no robustness block)
        """
        return self._robustness


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
