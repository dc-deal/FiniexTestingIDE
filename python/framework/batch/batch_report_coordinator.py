"""
FiniexTestingIDE - Batch Report Coordinator
Coordinates batch execution report generation and logging

Extracted from strategy_runner.py to separate reporting concerns.
This is the coordination layer - actual rendering logic stays in framework/reporting/
"""
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.reporting.batch_summary import BatchSummary
from python.configuration.app_config_manager import AppConfigManager
import sys
import io
import re


class BatchReportCoordinator:
    """
    Coordinates batch execution report generation and logging.

    Responsibilities:
    - Create BatchSummary instance
    - Capture stdout with ANSI colors for console
    - Strip colors for file logging
    - Log to scenario set logger

    Note: Actual report rendering logic is in framework/reporting/batch_summary.py
    """

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        scenario_set: ScenarioSet,
        app_config: AppConfigManager
    ):
        """
        Initialize batch report coordinator.

        Args:
            batch_execution_summary: Execution results to report
            scenario_set: Scenario set with logger
            app_config: Application configuration
        """
        self._batch_execution_summary = batch_execution_summary
        self._scenario_set = scenario_set
        self._app_config = app_config

    def generate_and_log(self) -> None:
        """
        Generate report, print to console, and log to file.

        Workflow:
        1. Create BatchSummary instance
        2. Capture rendered output with ANSI colors
        3. Print colored version to console
        4. Strip colors and log to scenario file
        """
        # Create summary renderer
        summary = BatchSummary(
            batch_execution_summary=self._batch_execution_summary,
            app_config=self._app_config
        )

        # Capture stdout (with ANSI colors for console)
        old_stdout = sys.stdout
        sys.stdout = summary_capture = io.StringIO()

        summary.render_all()

        sys.stdout = old_stdout
        summary_with_colors = summary_capture.getvalue()

        # Print summary to console (with colors)
        print(summary_with_colors)

        # Log to file (without colors)
        summary_clean = re.sub(r'\033\[[0-9;]+m', '', summary_with_colors)
        self._scenario_set.printed_summary_logger.info(summary_clean)
