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
from typing import Any, Dict, List, Optional
from python.components.logger.scenario_logger import ScenarioLogger
from python.components.logger.system_info_writer import write_system_version_parameters
from python.configuration.app_config_loader import AppConfigLoader
from python.framework.utils.scenario_set_utils import ScenarioSetUtils


@dataclass
class SingleScenario:
    """Test scenario configuration for batch testing"""

    symbol: str
    start_date: str
    end_date: str
    max_ticks: Optional[int] = None
    data_mode: str = "realistic"
    enabled: bool = True  # Default: enabled

    # ============================================
    # STRATEGY PARAMETERS
    # ============================================
    # Strategy-Logic (→ WorkerCoordinator sammelt Requirements & dessen Parameter)
    strategy_config: Dict[str, Any] = field(default_factory=dict)

    # Execution-Optimization (→ Framework)
    execution_config: Optional[Dict[str, Any]] = None

    # TradeSimulator configuration (per scenario)
    # Allows each scenario to have different balance/currency/leverage
    trade_simulator_config: Optional[Dict[str, Any]] = None

    name: Optional[str] = None

    def __post_init__(self):
        if self.name is None:
            self.name = f"{self.symbol}_{self.start_date}_{self.end_date}"

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
                # ← NEU: Künstliche Last - NUR für Heavy workers
                # Ist eher für self-testing szenarios und stress tests gedacht.
                "artificial_load_ms": 5.0,  # 5ms pro Worker
                # Performance Tuning
                "adaptive_parallelization": True,  # Auto-detect optimal mode
                "log_performance_stats": True,  # Log timing statistics
            }

    def to_config_string_for_display(self) -> str:
        """
        Display string for scenario configuration.

        NOTE: Actual config creation is in ProcessScenarioConfig.from_scenario()
        This is just for display/debugging purposes.

        Returns:
            Human-readable config summary
        """
        return (
            f"Scenario: {self.name}\n"
            f"  Symbol: {self.symbol}\n"
            f"  Period: {self.start_date} → {self.end_date}\n"
            f"  Max Ticks: {self.max_ticks or 'unlimited'}\n"
            f"  Enabled: {self.enabled}"
        )


@dataclass
class LoadedScenarioConfig:
    """Result of config loading - raw data before ScenarioSet creation"""
    scenario_set_name: str
    scenarios: List[SingleScenario]
    config_path: Path


class ScenarioSet:
    """Self-contained scenario set with its own logging infrastructure"""

    def __init__(self, scenario_config: LoadedScenarioConfig, app_config: AppConfigLoader):

        self.scenario_set_name = scenario_config.scenario_set_name
        self.scenarios = scenario_config.scenarios
        self.config_path = scenario_config.config_path
        self.app_config = app_config

        # ScenarioSet erstellt SEINE EIGENEN Logger
        self._run_timestamp = datetime.now(
            timezone.utc).strftime("%Y%m%d_%H%M%S")

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
    def run_timestamp(self) -> str:
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
