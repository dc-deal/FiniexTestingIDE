"""
FiniexAutoTrader - Autotrader Report Coordinator

The live counterpart to BatchReportCoordinator: consumes a finished
AutoTraderResult and writes all session run outputs into the run directory —
the event-stream CSV, the unified report artifacts (#391), the algo diagnostics
CSV, and the post-session console summary. Report derivation itself lives in
framework/reporting/*; this only orchestrates the persist + render.
"""
from pathlib import Path
from typing import Optional

from python.framework.autotrader.reporting.autotrader_post_session_report import AutotraderPostSessionReport
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.diagnostics_csv_sink import flush_decision_diagnostics
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.run_reports.broker_report_builder import build_broker_report_from_session
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
from python.framework.reporting.run_reports.run_summary_builder import build_run_summary
from python.framework.reporting.io.run_summary_io import write_run_summary
from python.framework.reporting.run_reports.run_unit import run_units_from_session
from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report
from python.framework.reporting.io.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.reporting.run_reports.warnings_errors_report_builder import build_warnings_errors_report_from_session
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
from python.framework.reporting.run_reports.worker_decision_report_builder import build_worker_decision_report
from python.framework.reporting.io.worker_decision_report_io import write_worker_decision_report
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


class AutotraderReportCoordinator:
    """
    Coordinates live-session report generation + artifact persistence.

    Responsibilities:
    - Event-stream CSV (events.csv)
    - Unified report artifacts (#391): trade / order / portfolio
    - Algo diagnostics CSV (#376)
    - Post-session console summary

    Note: report derivation lives in framework/reporting/*; this only orchestrates.
    """

    def __init__(
        self,
        result: AutoTraderResult,
        run_dir: Path,
        config: AutoTraderConfig,
        decision_logic: Optional[AbstractDecisionLogic],
        summary_logger: ScenarioLogger,
        global_logger: ScenarioLogger,
        broker_config: Optional[BrokerConfig] = None,
    ):
        """
        Initialize the report coordinator.

        Args:
            result: The completed session result to report
            run_dir: The session's run directory
            config: The autotrader profile config (portfolio unit name/symbol)
            decision_logic: The decision logic (algo diagnostics sinks)
            summary_logger: Logger for the post-session summary + console
            global_logger: Logger for the global file
            broker_config: The resolved live BrokerConfig (the executor's broker) for the
                broker report; None when the session has no executor (startup failure)
        """
        self._result = result
        self._run_dir = run_dir
        self._config = config
        self._decision_logic = decision_logic
        self._summary_logger = summary_logger
        self._global_logger = global_logger
        # Already-resolved BrokerConfig — the sim `data_broker_type` vs. live `broker_type`
        # config-key asymmetry is resolved UPSTREAM (at AutoTrader startup, `config.broker_type`
        # → `_create_broker_config`); here we only ever see the resolved object (its
        # `broker_type` is a `BrokerType` enum), so reporting needs no key translation.
        self._broker_config = broker_config

    def generate_and_log(self) -> None:
        """Write all session artifacts + print the post-session summary."""
        result = self._result

        # Long-format event-stream CSV (#330) — replaces the previous
        # autotrader_orders.csv + autotrader_trades.csv pair with a single
        # chronological events.csv (FIX ExecutionReport style).
        EventStreamWriter.from_autotrader_result(
            trade_history=result.trade_history or [],
            order_history=result.order_history or [],
            run_dir=self._run_dir,
        ).flush('events.csv')

        # Unified report artifacts (#391) — the canonical models the console/CSV
        # render and the API serves; same shape as sim, one set per session run.
        # The session is one run unit → tagged with the profile/symbol name (#393).
        name = self._config.name or self._config.symbol
        # Extract the session's single run unit once (#391 Phase 2).
        units = run_units_from_session(result, name, self._config.symbol)

        report = build_trade_history_report(units)
        write_trade_history_report(report, self._run_dir)
        write_trade_history_csv(report, self._run_dir)

        order_report = build_order_history_report(units)
        write_order_history_report(order_report, self._run_dir)
        write_order_history_csv(order_report, self._run_dir)

        # Portfolio headline — the single session unit (= its own currency aggregate).
        portfolio_report = build_portfolio_report(units)
        write_portfolio_report(portfolio_report, self._run_dir)

        # Pending-orders — empty for live (AutoTraderResult carries no pending stats);
        # written for artifact / API consistency with the sim runs.
        pending_report = build_pending_orders_report(units)
        write_pending_orders_report(pending_report, self._run_dir)

        # Execution-stats headline — the single session unit's order counts (#391).
        execution_stats_report = build_execution_stats_report(units)
        write_execution_stats_report(execution_stats_report, self._run_dir)
        write_execution_stats_csv(execution_stats_report, self._run_dir)

        # Run summary — cross-section KPIs composed from the section aggregates (#390 prework).
        run_summary = build_run_summary(portfolio_report, report, execution_stats_report)
        write_run_summary(run_summary, self._run_dir)

        # Worker/decision — per-unit worker + decision performance (unified, #398).
        worker_decision_report = build_worker_decision_report(units)
        write_worker_decision_report(worker_decision_report, self._run_dir)

        # Warnings & errors — tiered model (#395). Persisted for API parity with the sim runs;
        # the compact post-session summary keeps reading the session buffers directly (same
        # structured source, avoids double-rendering the emergency cause).
        warnings_errors_report = build_warnings_errors_report_from_session(
            result, name, self._config.symbol)
        write_warnings_errors_report(warnings_errors_report, self._run_dir)

        # Broker configuration — the session's single broker + symbol (unified model;
        # same artifact + API shape as the sim runs). Skipped if the session never built
        # an executor (startup failure → no resolved broker_config).
        broker_report = None
        if self._broker_config is not None:
            broker_report = build_broker_report_from_session(
                self._broker_config, self._config.symbol)
            write_broker_report(broker_report, self._run_dir)

        # Diagnostics CSV (#376) — algo-declared sinks, next to events.csv.
        if self._decision_logic:
            flush_decision_diagnostics(self._decision_logic, self._run_dir)

        # Post-session summary — operational view + the #389 analytics line from the
        # model (#393); the big per-trade table stays sim-only / API for live.
        post_session_report = AutotraderPostSessionReport(
            summary_logger=self._summary_logger,
            global_logger=self._global_logger,
        )
        post_session_report.print_report(result, report, broker_report)
