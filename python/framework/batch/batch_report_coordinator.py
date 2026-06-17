"""
FiniexTestingIDE - Batch Report Coordinator
Coordinates batch execution report generation and logging

Extracted from strategy_runner.py to separate reporting concerns.
This is the coordination layer - actual rendering logic stays in framework/batch_reporting/
"""
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.batch_reporting.batch_summary import BatchSummary
from python.framework.batch_reporting.portfolio_aggregator import PortfolioAggregator
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.run_reports.order_history_report_builder import build_order_history_report_from_batch
from python.framework.reporting.run_reports.order_history_report_io import (
    write_order_history_csv, write_order_history_report)
from python.framework.reporting.run_reports.portfolio_report_builder import build_portfolio_report_from_batch
from python.framework.reporting.run_reports.portfolio_report_io import write_portfolio_report
from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report_from_batch
from python.framework.reporting.run_reports.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
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

    Note: Actual report rendering logic is in framework/batch_reporting/batch_summary.py
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
        2. Capture full output (all per-scenario details) for file logging
        3. Capture console output (respects summary_detail config)
        4. Print colored version to console
        5. Strip colors and log full version to scenario file
        """
        run_dir = self._scenario_set.logger.get_log_dir()

        # === DERIVE (once, off the hot loop) — the canonical report models ===
        trade_report = build_trade_history_report_from_batch(self._batch_execution_summary)
        order_report = build_order_history_report_from_batch(self._batch_execution_summary)
        # Portfolio headline — units (scenarios) + per-currency roll-up. The aggregate is
        # the existing currency aggregator (single source for the total), injected so the
        # builder stays out of the console-summary layer.
        currency_aggregates = PortfolioAggregator(
            self._batch_execution_summary.process_result_list).aggregate_by_currency()
        portfolio_report = build_portfolio_report_from_batch(
            self._batch_execution_summary, currency_aggregates)

        # === PRESENT — the migrated sections render from the models (#393) ===
        summary = BatchSummary(
            batch_execution_summary=self._batch_execution_summary,
            app_config=self._app_config,
            generator_profiles=self._scenario_set.get_generator_profiles(),
            trade_report=trade_report,
            order_report=order_report,
        )

        summary_detail = self._app_config.get_summary_detail()

        # Always capture full output for file logging
        old_stdout = sys.stdout
        sys.stdout = file_capture = io.StringIO()
        summary.render_all(summary_detail=True)
        sys.stdout = old_stdout
        full_output = file_capture.getvalue()

        if summary_detail:
            # Full detail mode — same output for console
            console_output = full_output
        else:
            # Compact mode — render again without per-scenario details
            sys.stdout = console_capture = io.StringIO()
            summary.render_all(summary_detail=False)
            sys.stdout = old_stdout
            console_output = console_capture.getvalue()

        # Print summary to console (with colors)
        print(console_output)

        # Log to file (without colors) — always full detail
        summary_clean = re.sub(r'\033\[[0-9;]+m', '', full_output)
        self._scenario_set.printed_summary_logger.info(summary_clean)

        # Long-format event-stream CSV per scenario (#330 / #233).
        # Writes one events_<scenario>.csv per scenario into an events/
        # subfolder of the scenario set's log dir — keeps the run dir tidy
        # when many scenarios produce many CSVs.
        events_dir = run_dir / 'events'
        events_dir.mkdir(exist_ok=True)
        for process_result in self._batch_execution_summary.process_result_list:
            tlr = process_result.tick_loop_results
            if tlr is None:
                continue
            EventStreamWriter.from_sim_result(
                trade_history=tlr.trade_history or [],
                order_history=tlr.order_history or [],
                run_dir=events_dir,
            ).flush(f'events_{process_result.scenario_name}.csv')

        # === PERSIST — the same model objects the console rendered (#391) ===
        write_trade_history_report(trade_report, run_dir)
        write_trade_history_csv(trade_report, run_dir)
        write_order_history_report(order_report, run_dir)
        write_order_history_csv(order_report, run_dir)
        write_portfolio_report(portfolio_report, run_dir)
