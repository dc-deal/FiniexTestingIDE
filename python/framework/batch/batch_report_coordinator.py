"""
FiniexTestingIDE - Batch Report Coordinator
Coordinates batch execution report generation and logging

Extracted from strategy_runner.py to separate reporting concerns.
This is the coordination layer - actual rendering logic stays in framework/batch_reporting/
"""
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.batch_reporting.batch_summary import BatchSummary
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.run_reports.broker_report_builder import build_broker_report_from_batch
from python.framework.reporting.run_reports.broker_report_io import write_broker_report
from python.framework.reporting.run_reports.execution_stats_report_builder import build_execution_stats_report
from python.framework.reporting.run_reports.execution_stats_report_io import (
    write_execution_stats_csv, write_execution_stats_report)
from python.framework.reporting.run_reports.order_history_report_builder import build_order_history_report
from python.framework.reporting.run_reports.order_history_report_io import (
    write_order_history_csv, write_order_history_report)
from python.framework.reporting.run_reports.pending_orders_report_builder import build_pending_orders_report
from python.framework.reporting.run_reports.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.run_reports.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.run_reports.portfolio_report_io import write_portfolio_report
from python.framework.reporting.run_reports.profiling_report_builder import build_profiling_report_from_batch
from python.framework.reporting.run_reports.profiling_report_io import write_profiling_report
from python.framework.reporting.run_reports.run_summary_builder import build_run_summary
from python.framework.reporting.run_reports.run_summary_io import write_run_summary
from python.framework.reporting.run_reports.run_unit import run_units_from_batch
from python.framework.reporting.run_reports.scenario_details_report_builder import build_scenario_details_report_from_batch
from python.framework.reporting.run_reports.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report
from python.framework.reporting.run_reports.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.reporting.run_reports.warnings_errors_report_builder import build_warnings_errors_report_from_batch
from python.framework.reporting.run_reports.warnings_errors_report_io import write_warnings_errors_report
from python.framework.reporting.run_reports.worker_decision_report_builder import build_worker_decision_report
from python.framework.reporting.run_reports.worker_decision_report_io import write_worker_decision_report
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
        # Extract the run's units once (#391 Phase 2) — every section maps from these.
        units = run_units_from_batch(self._batch_execution_summary)
        trade_report = build_trade_history_report(units)
        order_report = build_order_history_report(units)
        # Portfolio full projection — per-unit rows (linear console) + per-currency roll-up.
        portfolio_report = build_portfolio_report(units)
        # Pending-orders — per-scenario lifecycle + latency + active orders (#391).
        pending_report = build_pending_orders_report(units)
        # Execution-stats headline — per-scenario order counts + summed total (#391).
        execution_stats_report = build_execution_stats_report(units)
        # Scenario details — per-scenario execution/signal metadata incl. failed (sim-only).
        scenario_details_report = build_scenario_details_report_from_batch(
            self._batch_execution_summary)
        # Run summary — cross-section KPIs composed from the section aggregates (#390 prework).
        run_summary = build_run_summary(
            portfolio_report, trade_report, execution_stats_report)
        # Worker/decision — per-unit worker + decision performance (unified, #398).
        worker_decision_report = build_worker_decision_report(units)
        # Profiling — per-scenario operation timing + inter-tick + clipping + warmup (sim-only, #399).
        profiling_report = build_profiling_report_from_batch(self._batch_execution_summary)
        # Broker configuration — per-broker spec + scenarios + symbols (sim-only).
        broker_report = build_broker_report_from_batch(self._batch_execution_summary)
        # Warnings & errors — tiered, from the validation channels + log pots (#395).
        warnings_errors_report = build_warnings_errors_report_from_batch(
            self._batch_execution_summary)

        # === PRESENT — the migrated sections render from the models (#393) ===
        summary = BatchSummary(
            batch_execution_summary=self._batch_execution_summary,
            app_config=self._app_config,
            generator_profiles=self._scenario_set.get_generator_profiles(),
            trade_report=trade_report,
            order_report=order_report,
            portfolio_report=portfolio_report,
            pending_report=pending_report,
            execution_report=execution_stats_report,
            scenario_details_report=scenario_details_report,
            run_summary=run_summary,
            worker_decision_report=worker_decision_report,
            profiling_report=profiling_report,
            broker_report=broker_report,
            warnings_errors_report=warnings_errors_report,
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
        write_pending_orders_report(pending_report, run_dir)
        write_execution_stats_report(execution_stats_report, run_dir)
        write_execution_stats_csv(execution_stats_report, run_dir)
        write_scenario_details_report(scenario_details_report, run_dir)
        write_run_summary(run_summary, run_dir)
        write_worker_decision_report(worker_decision_report, run_dir)
        write_profiling_report(profiling_report, run_dir)
        write_broker_report(broker_report, run_dir)
        write_warnings_errors_report(warnings_errors_report, run_dir)
