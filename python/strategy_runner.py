"""
FiniexTestingIDE - Strategy Runner with VisualConsoleLogger
Compact, colorful logging output

ENTRY POINT: Initializes logger with auto-init via bootstrap_logger
"""

from python.configuration import AppConfigLoader
from python.framework.utils.scenario_set_utils import ScenarioSetUtils
from python.scenario.config_loader import ScenarioConfigLoader
from python.framework.reporting.batch_summary import BatchSummary
from python.framework.exceptions.data_validation_errors import DataValidationError
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.batch_orchestrator import BatchOrchestrator
import os
import platform
import sys
import traceback
import io
import re

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


def run_strategy_test():
    """
    Main strategy testing function with visual output
    """

    # ============================================================
    # System Info
    # ============================================================
    vLog.info(
        f"System: {platform.system()} {platform.release()}")
    vLog.info(f"Python: {platform.python_version()}")
    vLog.info(f"CPU Count: {os.cpu_count()}")

    vLog.info("🚀 Starting [BatchOrchestrator] strategy test")

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

        scenario_set_json = "eurusd_3_windows.json"
        scenario_set = config_loader.load_config(scenario_set_json)

        vLog.info(
            f"📂 Loaded scenario set: {scenario_set_json} ({len(scenario_set.scenarios)} scenarios)"
        )

        # ============================================================
        # Initialize Data Worker
        # ============================================================
        data_worker = TickDataLoader()

        # ============================================================
        # Execute Batch via Orchestrator
        # ============================================================
        orchestrator = BatchOrchestrator(
            scenario_set,
            data_worker,
            app_config_loader
        )

        # Run test
        batch_execution_summary = orchestrator.run()

        # ============================================
        # Capture output for file logging
        # ============================================
        summary = BatchSummary(
            batch_execution_summary=batch_execution_summary,
            app_config=app_config_loader
        )

        # Capture stdout (with ANSI colors for console)
        old_stdout = sys.stdout
        sys.stdout = summary_capture = io.StringIO()

        summary.render_all()

        sys.stdout = old_stdout
        summary_with_colors = summary_capture.getvalue()

        # Print summary in console
        print(summary_with_colors)
        # print in scenario
        summary_clean = re.sub(r'\033\[[0-9;]+m', '', summary_with_colors)
        scenario_set.printed_summary_logger.info(summary_clean)

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

    # Run test
    run_strategy_test()
