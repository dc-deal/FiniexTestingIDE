"""
FiniexTestingIDE - Batch Report Coordinator
Coordinates batch execution report generation and logging

Extracted from strategy_runner.py to separate reporting concerns.
This is the coordination layer — the section sub-presenters live in framework/reporting/console/.
"""
from typing import Optional

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.reporting.console.block_splitting_disposition import BlockSplittingDisposition
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.console.execution_header_summary import ExecutionHeaderSummary
from python.framework.reporting.console.executive_summary import ExecutiveSummary
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.profiling_summary import ProfilingSummary
from python.framework.reporting.console.scenario_details_summary import ScenarioDetailsSummary
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.console.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.run_reports.aggregated_portfolio_report_builder import build_aggregated_portfolio_report
from python.framework.reporting.io.aggregated_portfolio_report_io import write_aggregated_portfolio_report
from python.framework.reporting.run_reports.block_splitting_report_builder import build_block_splitting_report_from_batch
from python.framework.reporting.io.block_splitting_report_io import write_block_splitting_report
from python.framework.reporting.run_reports.broker_report_builder import build_broker_report_from_batch
from python.framework.reporting.io.broker_report_io import write_broker_report
from python.framework.reporting.run_reports.execution_stats_report_builder import build_execution_stats_report
from python.framework.reporting.io.execution_stats_report_io import (
    write_execution_stats_csv, write_execution_stats_report)
from python.framework.reporting.run_reports.order_history_report_builder import build_order_history_report
from python.framework.reporting.io.order_history_report_io import (
    write_order_history_csv, write_order_history_report)
from python.framework.reporting.run_reports.pending_orders_report_builder import build_pending_orders_report
from python.framework.reporting.io.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.run_reports.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.io.portfolio_report_io import write_portfolio_report
from python.framework.reporting.io.report_store import IO_SUBDIR
from python.framework.reporting.run_reports.profiling_report_builder import build_profiling_report_from_batch
from python.framework.reporting.io.profiling_report_io import write_profiling_report
from python.framework.reporting.run_reports.run_meta_report_builder import build_run_meta_report_from_batch
from python.framework.reporting.io.run_meta_report_io import write_run_meta_report
from python.framework.reporting.run_reports.run_summary_builder import build_run_summary
from python.framework.reporting.io.run_summary_io import write_run_summary
from python.framework.reporting.run_reports.run_unit import run_units_from_batch
from python.framework.reporting.run_reports.scenario_details_report_builder import build_scenario_details_report_from_batch
from python.framework.reporting.io.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report
from python.framework.reporting.io.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.reporting.run_reports.warnings_errors_report_builder import build_warnings_errors_report_from_batch
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
from python.framework.reporting.run_reports.worker_decision_report_builder import build_worker_decision_report
from python.framework.reporting.io.worker_decision_report_io import write_worker_decision_report
from python.configuration.app_config_manager import AppConfigManager
import sys
import io
import re


class BatchReportCoordinator:
    """
    Coordinates batch execution report generation and logging.

    Responsibilities:
    - DERIVE the canonical report models once, off the hot loop
    - PRESENT: orchestrate the section sub-presenters into the console summary
    - Capture stdout with ANSI colors for console, strip colors for file logging
    - PERSIST the model artifacts (JSON/CSV) the API serves

    Note: the section sub-presenters live in framework/reporting/console/; this coordinator is
    their orchestrator (the former BatchSummary, folded in).
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
        1. DERIVE the report models + build the section sub-presenters
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
        # Run meta — run-level timing split + scenario identity (the orchestrator's primary
        # measurements), projected once so PRESENT reads the model instead of the raw type.
        run_meta_report = build_run_meta_report_from_batch(self._batch_execution_summary)
        # Worker/decision — per-unit worker + decision performance (unified, #398).
        worker_decision_report = build_worker_decision_report(units)
        # Profiling — per-scenario operation timing + inter-tick + clipping + warmup (sim-only, #399).
        profiling_report = build_profiling_report_from_batch(self._batch_execution_summary)
        # Broker configuration — per-broker spec + scenarios + symbols (sim-only).
        broker_report = build_broker_report_from_batch(self._batch_execution_summary)
        # Warnings & errors — tiered, from the validation channels + log pots (#395).
        warnings_errors_report = build_warnings_errors_report_from_batch(
            self._batch_execution_summary)
        # Aggregated per-currency portfolio — the rich detail view from the per-unit rows (#397).
        aggregated_portfolio_report = build_aggregated_portfolio_report(
            portfolio_report, execution_stats_report, pending_report)
        # Block-splitting disposition — Profile Runs only (empty otherwise; sim-only).
        block_splitting_report = build_block_splitting_report_from_batch(
            self._batch_execution_summary, self._scenario_set.get_generator_profiles() or [])

        # === PRESENT — the section sub-presenters fed the models; the orchestration that
        # BatchSummary held now lives in this coordinator (the sub-presenters stay, #391) ===
        self._renderer = ConsoleRenderer()
        self._run_summary = run_summary
        self._run_meta = run_meta_report
        self._profiling_report = profiling_report
        self._scenario_details_report = scenario_details_report
        self._warnings_errors_report = warnings_errors_report
        self._aggregated_portfolio_report = aggregated_portfolio_report
        self._block_splitting_report = block_splitting_report
        self._generator_profiles = self._scenario_set.get_generator_profiles()

        self._header_summary = ExecutionHeaderSummary(
            run_meta_report, warnings_errors_report, self._app_config)
        self._portfolio_summary = PortfolioSummary(
            portfolio_report, pending_report, execution_stats_report, aggregated_portfolio_report)
        self._performance_summary = PerformanceSummary(worker_decision_report)
        self._profiling_summary = ProfilingSummary(profiling_report)
        self._worker_decision_breakdown = WorkerDecisionBreakdownSummary(
            profiling_report=profiling_report, worker_decision_report=worker_decision_report)
        self._broker_summary = BrokerSummary(broker_report)
        self._trade_history_summary = TradeHistorySummary(trade_report, order_report)
        self._warnings_summary = WarningsSummary(warnings_errors_report)
        self._scenario_details_summary = ScenarioDetailsSummary(scenario_details_report)

        summary_detail = self._app_config.get_summary_detail()

        # Always capture full output for file logging
        old_stdout = sys.stdout
        sys.stdout = file_capture = io.StringIO()
        self._render_all(summary_detail=True)
        sys.stdout = old_stdout
        full_output = file_capture.getvalue()

        if summary_detail:
            # Full detail mode — same output for console
            console_output = full_output
        else:
            # Compact mode — render again without per-scenario details
            sys.stdout = console_capture = io.StringIO()
            self._render_all(summary_detail=False)
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

        # === PERSIST — the same model objects the console rendered (#391); the report
        # artifacts (JSON + CSV) go into the run's io/ subfolder (#396 housekeeping) ===
        io_dir = run_dir / IO_SUBDIR
        io_dir.mkdir(parents=True, exist_ok=True)
        write_trade_history_report(trade_report, io_dir)
        write_trade_history_csv(trade_report, io_dir)
        write_order_history_report(order_report, io_dir)
        write_order_history_csv(order_report, io_dir)
        write_portfolio_report(portfolio_report, io_dir)
        write_pending_orders_report(pending_report, io_dir)
        write_execution_stats_report(execution_stats_report, io_dir)
        write_execution_stats_csv(execution_stats_report, io_dir)
        write_scenario_details_report(scenario_details_report, io_dir)
        write_run_summary(run_summary, io_dir)
        write_run_meta_report(run_meta_report, io_dir)
        write_worker_decision_report(worker_decision_report, io_dir)
        write_profiling_report(profiling_report, io_dir)
        write_broker_report(broker_report, io_dir)
        write_warnings_errors_report(warnings_errors_report, io_dir)
        write_aggregated_portfolio_report(aggregated_portfolio_report, io_dir)
        # Block-splitting artifact only when there is something to report (Profile Runs).
        if block_splitting_report.symbols:
            write_block_splitting_report(block_splitting_report, io_dir)

    def _render_all(self, summary_detail: Optional[bool] = None):
        """
        Render the complete batch summary into the section sub-presenters.

        Args:
            summary_detail: Override for per-scenario detail rendering.
                            None = use config value, True/False = force.

        Sequence:
        1. Header with basic stats (INCLUDING batch status)
        2. Scenario details (grid)
        3. Portfolio summaries (per process_result + aggregated)
        4. Performance details (per process_result + aggregated)
        5. Bottleneck analysis
        6. Profiling analysis
        7. Worker decision breakdown
        """
        # Header + basic execution stats (model-fed sub-presenter)
        self._header_summary.render(self._renderer)

        # Scenario details — linear, from the model (#393)
        threshold = self._app_config.get_console_logging_config_object().scenario_detail_threshold
        self._scenario_details_summary.render(
            self._renderer, scenario_detail_threshold=threshold)

        # Summary detail flag (per-scenario vs aggregated only)
        if summary_detail is None:
            summary_detail = self._app_config.get_summary_detail()

        compact = not summary_detail

        # Portfolio summaries
        if summary_detail:
            self._portfolio_summary.render_per_scenario(self._renderer)

        # Aggregated per-currency view — from the model (#397)
        self._portfolio_summary.render_aggregated(self._renderer)

        # Trade History
        if summary_detail:
            self._trade_history_summary.render_per_scenario(self._renderer)
        self._trade_history_summary.render_aggregated(self._renderer)

        # Broker configuration
        self._broker_summary.render(self._renderer, compact=compact, threshold=threshold)

        # Performance summaries
        if summary_detail:
            self._performance_summary.render_per_scenario(self._renderer)
        self._performance_summary.render_aggregated(self._renderer)
        self._performance_summary.render_bottleneck_analysis(self._renderer)

        # Profiling Analysis
        if summary_detail:
            self._profiling_summary.render_per_scenario(self._renderer)
        self._profiling_summary.render_aggregated(self._renderer, compact=compact, threshold=threshold)
        self._profiling_summary.render_bottleneck_analysis(self._renderer)

        # Worker Decision Breakdown — the overhead/bottleneck "too high?" verdicts moved to the
        # post-run validator (#395); the breakdown now shows the calculated split only.
        if summary_detail:
            self._worker_decision_breakdown.render_per_scenario(self._renderer)
        self._worker_decision_breakdown.render_aggregated()

        # Warmup phase breakdown (summary_detail only) — from the profiling model (#399)
        if summary_detail:
            self._profiling_summary.render_warmup(self._renderer)

        # Warnings & Notices (always rendered, before executive summary)
        self._warnings_summary.render(self._renderer)

        # Block Splitting Disposition (Profile Runs only — the model is empty otherwise,
        # so render() no-ops; rendered from the model, #391)
        disposition = BlockSplittingDisposition(self._block_splitting_report)
        disposition.render(self._renderer)

        # Executive Summary
        executive = ExecutiveSummary(
            self._app_config, self._run_summary,
            self._run_meta, self._profiling_report, self._scenario_details_report,
            self._warnings_errors_report, self._aggregated_portfolio_report,
            generator_profiles=self._generator_profiles
        )
        executive.render(self._renderer)

        # Footer
        self._renderer.print_separator(width=120)
