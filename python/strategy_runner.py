"""
FiniexTestingIDE - Strategy Runner with VisualConsoleLogger
Compact, colorful logging output

ENTRY POINT: Initializes logger with setup_logging()
"""

from python.configuration import AppConfigLoader
from python.scenario.config_loader import ScenarioConfigLoader
from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.reporting.batch_summary import BatchSummary
from python.framework.exceptions.data_validation_errors import DataValidationError
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.batch_orchestrator import BatchOrchestrator
import os
import platform
import sys
import traceback

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


def run_strategy_test() -> dict:
    """
    Main strategy testing function with visual output
    """

    vLog.info("🚀 Starting [BatchOrchestrator] strategy test", "StrategyRunner")

    try:
        # ============================================================
        # Load Application Configuration
        # ============================================================
        app_config_loader = AppConfigLoader()

        # Extract execution defaults
        default_parallel_scenarios = app_config_loader.get_default_parallel_scenarios()
        default_max_parallel_scenarios = app_config_loader.get_default_max_parallel_scenarios()
        default_parallel_workers = app_config_loader.get_default_parallel_workers()

        vLog.info(
            f"📋 Execution config: "
            f"Parallel Scenarios={default_parallel_scenarios}, "
            f"Max Workers={default_max_parallel_scenarios}, "
            f"Worker Parallelism={default_parallel_workers}"
        )

        # ============================================================
        # Load Scenario Configuration
        # ============================================================
        config_loader = ScenarioConfigLoader()

        scenario_set_name = "eurusd_3_windows.json"
        scenarios = config_loader.load_config(scenario_set_name)

        vLog.info(
            f"📂 Loaded scenario set: {scenario_set_name} ({len(scenarios)} scenarios)"
        )

        # ============================================================
        # Initialize Data Worker
        # ============================================================
        data_worker = TickDataLoader()

        # ============================================================
        # Initialize Performance Log (NEW: C#003)
        # ============================================================
        performance_log = ScenarioSetPerformanceManager()

        # ============================================================
        # Execute Batch via Orchestrator
        # ============================================================
        orchestrator = BatchOrchestrator(
            scenarios,
            data_worker,
            app_config_loader,
            performance_log
        )

        # Run test
        results = orchestrator.run()

        # ============================================
        # NEW (C#003): Direct Reporting via BatchSummary
        # ============================================
        summary = BatchSummary(
            performance_log=performance_log,
            app_config=app_config_loader
        )
        summary.render_all()
        return results

    except DataValidationError as e:
        vLog.validation_error(
            message=str(e),
            context=e.get_context()
        )

    except FileNotFoundError as e:
        vLog.config_error(
            f"Config file not found: {e}",
            file_path=str(e)
        )

    except Exception as e:
        vLog.hard_error(
            f"Unexpected error during strategy test",
            exception=e
        )


if __name__ == "__main__":
    """Entry point"""

    # System info
    vLog.info(f"System: {platform.system()} {platform.release()}", "System")
    vLog.info(f"Python: {platform.python_version()}", "System")
    vLog.info(f"CPU Count: {os.cpu_count()}", "System")
    vLog.section_separator()

    # Run test
    results = run_strategy_test()

    # Exit with status
    if results and results.get('success', False):
        vLog.info("✅ All tests passed!", "StrategyRunner")
        exit(0)
    else:
        vLog.error("❌ Some tests failed!", "StrategyRunner")
        exit(1)
