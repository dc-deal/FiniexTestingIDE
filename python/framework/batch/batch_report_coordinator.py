"""
FiniexTestingIDE - Batch Report Coordinator
Coordinates batch execution report generation and logging

Extracted from strategy_runner.py to separate reporting concerns.
This is the coordination layer — the section sub-presenters live in framework/reporting/console/.
"""
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.reporting.console.block_splitting_disposition import BlockSplittingDisposition
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.console.execution_header_summary import ExecutionHeaderSummary
from python.framework.reporting.console.sim_executive_summary import SimExecutiveSummary
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.profiling_summary import ProfilingSummary
from python.framework.reporting.console.scenario_details_summary import ScenarioDetailsSummary
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.console.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.reporting.console.run_console_renderer import RunConsoleRenderer
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.builders.aggregated_portfolio_report_builder import build_aggregated_portfolio_report
from python.framework.reporting.io.aggregated_portfolio_report_io import write_aggregated_portfolio_report
from python.framework.reporting.builders.block_splitting_report_builder import build_block_splitting_report_from_batch
from python.framework.reporting.io.block_splitting_report_io import write_block_splitting_report
from python.framework.reporting.builders.broker_report_builder import build_broker_report_from_batch
from python.framework.reporting.io.broker_report_io import write_broker_report
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.reporting.builders.profiling_report_builder import build_profiling_report_from_batch
from python.framework.reporting.io.profiling_report_io import write_profiling_report
from python.framework.reporting.builders.robustness_report_builder import build_robustness_report_from_batch
from python.framework.reporting.io.robustness_report_io import write_robustness_report
from python.framework.reporting.console.robustness_summary import RobustnessSummary
from python.framework.reporting.builders.run_meta_report_builder import build_run_meta_report_from_batch
from python.framework.reporting.io.run_meta_report_io import write_run_meta_report
from python.framework.reporting.builders.run_unit import run_units_from_batch
from python.framework.reporting.builders.scenario_details_report_builder import build_scenario_details_report_from_batch
from python.framework.reporting.io.scenario_details_report_io import write_scenario_details_report
from python.framework.reporting.builders.warnings_errors_report_builder import build_warnings_errors_report_from_batch
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
from python.framework.reporting.shared_report_coordinator import SharedReportCoordinator
from python.framework.reporting.store.run_provenance_builder import build_run_provenance
from python.framework.reporting.store.run_results_ledger import append_run_to_ledger
from python.framework.types.run_results_types import SweepContext
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
        app_config: AppConfigManager,
        sweep_context: SweepContext = None
    ):
        """
        Initialize batch report coordinator.

        Args:
            batch_execution_summary: Execution results to report
            scenario_set: Scenario set with logger
            app_config: Application configuration
            sweep_context: Optional sweep tagging when run as a sweep combination (#390)
        """
        self._batch_execution_summary = batch_execution_summary
        self._scenario_set = scenario_set
        self._app_config = app_config
        self._sweep_context = sweep_context

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

        # === DERIVE + PERSIST the shared units-derived sections (#403) ===
        # The 7 sections both pipelines share are built + written once by the shared
        # coordinator (into the run's io/ subfolder); the names below stay for the console.
        io_dir = run_dir / IO_SUBDIR
        units = run_units_from_batch(self._batch_execution_summary)
        unified = SharedReportCoordinator.derive_and_persist(units, io_dir)
        trade_report = unified.trade_history
        order_report = unified.order_history
        portfolio_report = unified.portfolio
        pending_report = unified.pending_orders
        execution_stats_report = unified.execution_stats
        run_summary = unified.run_summary
        worker_decision_report = unified.worker_decision

        # === DERIVE the sim-only / pipeline-specific sections ===
        # Scenario details — per-scenario execution/signal metadata incl. failed (sim-only).
        scenario_details_report = build_scenario_details_report_from_batch(
            self._batch_execution_summary)
        # Run meta — run-level timing split + scenario identity (the orchestrator's primary
        # measurements), projected once so PRESENT reads the model instead of the raw type.
        run_meta_report = build_run_meta_report_from_batch(self._batch_execution_summary)
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
        # Robustness validation — multi-window + IS/OOS (empty unless robustness enabled; sim-only, #367).
        robustness_report = build_robustness_report_from_batch(self._batch_execution_summary)

        # === PRESENT — build the section sub-presenters from the models and render them
        # through the shared ordered renderer (#403 Phase 2; the section order lives in one
        # place). Sim provides every slot + the Executive Summary as the closing block. ===
        renderer = ConsoleRenderer()
        threshold = self._app_config.get_console_logging_config_object().scenario_detail_threshold

        console = RunConsoleRenderer(
            unit_count=run_summary.unit_count,
            threshold=threshold,
            header_summary=ExecutionHeaderSummary(
                run_meta_report, warnings_errors_report, self._app_config),
            scenario_details_summary=ScenarioDetailsSummary(scenario_details_report),
            portfolio_summary=PortfolioSummary(
                portfolio_report, pending_report, execution_stats_report, aggregated_portfolio_report),
            trade_history_summary=TradeHistorySummary(trade_report, order_report),
            broker_summary=BrokerSummary(broker_report),
            performance_summary=PerformanceSummary(worker_decision_report),
            profiling_summary=ProfilingSummary(profiling_report),
            worker_decision_breakdown=WorkerDecisionBreakdownSummary(
                profiling_report=profiling_report, worker_decision_report=worker_decision_report),
            warnings_summary=WarningsSummary(warnings_errors_report),
            block_splitting_disposition=BlockSplittingDisposition(block_splitting_report),
            robustness_summary=RobustnessSummary(robustness_report),
            closing_block=SimExecutiveSummary(
                self._app_config, run_summary, run_meta_report, profiling_report,
                scenario_details_report, warnings_errors_report, aggregated_portfolio_report,
                generator_profiles=self._scenario_set.get_generator_profiles()),
        )

        summary_detail = self._app_config.get_summary_detail()

        # Always capture full output for file logging
        old_stdout = sys.stdout
        sys.stdout = file_capture = io.StringIO()
        console.render_all(renderer, summary_detail=True)
        sys.stdout = old_stdout
        full_output = file_capture.getvalue()

        if summary_detail:
            # Full detail mode — same output for console
            console_output = full_output
        else:
            # Compact mode — render again without per-scenario details
            sys.stdout = console_capture = io.StringIO()
            console.render_all(renderer, summary_detail=False)
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

        # === PERSIST the pipeline-specific sections (the shared 7 were written by the
        # shared coordinator above); same io/ subfolder (#396 housekeeping) ===
        write_scenario_details_report(scenario_details_report, io_dir)
        write_run_meta_report(run_meta_report, io_dir)
        write_profiling_report(profiling_report, io_dir)
        write_broker_report(broker_report, io_dir)
        write_warnings_errors_report(warnings_errors_report, io_dir)
        write_aggregated_portfolio_report(aggregated_portfolio_report, io_dir)
        # Block-splitting artifact only when there is something to report (Profile Runs).
        if block_splitting_report.symbols:
            write_block_splitting_report(block_splitting_report, io_dir)
        # Robustness artifact only when robustness mode is enabled (#367).
        if robustness_report.enabled:
            write_robustness_report(robustness_report, io_dir)

        # === Run-results ledger (#390) — append the run to the persistent cross-run store the
        # Parameter Optimization system ranks over. The run's status/error (a total failure, e.g.
        # an out-of-range parameter combination) comes from the canonical warnings/errors outcome
        # inside build_run_provenance — recorded as an error-flagged row, never silently absent (#1). ===
        provenance = build_run_provenance(
            self._batch_execution_summary, self._scenario_set, run_dir,
            self._sweep_context, warnings_errors_report)
        append_run_to_ledger(run_summary, provenance)
