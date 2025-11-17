
# ============================================================================
# PROCESS EXECUTOR CLASS (Orchestration Wrapper)
# ============================================================================


from multiprocessing import Queue
from typing import Optional
from python.configuration.app_config_loader import AppConfigLoader
from python.framework.process.process_main import process_main
from python.framework.types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.process_data_types import ProcessDataPackage, ProcessResult, ProcessScenarioConfig
from python.framework.types.scenario_set_types import SingleScenario


class ProcessExecutor:
    """
    Orchestrates scenario execution.

    Wrapper around top-level process functions.
    Provides clean interface for BatchOrchestrator.

    DESIGN:
    - Holds scenario and config
    - Calls process_main() (top-level function)
    - Compatible with ThreadPoolExecutor and ProcessPoolExecutor
    """

    def __init__(
        self,
        scenario: SingleScenario,
        app_config_loader: AppConfigLoader,
        scenario_index: int,
        scenario_set_name: str,
        run_timestamp: str,
        live_stats_config: LiveStatsExportConfig = None
    ):
        """
        Initialize process executor.

        CORRECTED: Added scenario_set_name and run_timestamp

        Args:
            scenario: Scenario to execute
            app_config: Application configuration
            scenario_index: Index in scenario list
            scenario_set_name: Name of scenario set (for logger)
            run_timestamp: Shared timestamp (for logger)
        """
        self.scenario = scenario
        self.app_config = app_config_loader
        self.scenario_index = scenario_index
        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp
        self.live_stats_config = live_stats_config

        # Create config (serializable)
        self.config = ProcessScenarioConfig.from_scenario(
            scenario=scenario,
            app_config_loader=app_config_loader,
            scenario_index=scenario_index,
            scenario_set_name=scenario_set_name,
            run_timestamp=run_timestamp,
            live_stats_config=live_stats_config
        )

    def run(self,
            shared_data: ProcessDataPackage,
            live_queue: Optional[Queue] = None) -> ProcessResult:
        """
        Execute scenario with shared data.

        Entry point for executor. Calls process_main().

        Args:
            shared_data: Prepared shared data

        Returns:
            ProcessResult with execution results
        """
        return process_main(self.config, shared_data, live_queue)
