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
from python.framework.reporting.run_reports.order_history_report_builder import build_order_history_report_from_session
from python.framework.reporting.run_reports.order_history_report_io import (
    write_order_history_csv, write_order_history_report)
from python.framework.reporting.run_reports.portfolio_report_builder import build_portfolio_report_from_session
from python.framework.reporting.run_reports.portfolio_report_io import write_portfolio_report
from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report_from_session
from python.framework.reporting.run_reports.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
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
        """
        self._result = result
        self._run_dir = run_dir
        self._config = config
        self._decision_logic = decision_logic
        self._summary_logger = summary_logger
        self._global_logger = global_logger

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
        report = build_trade_history_report_from_session(result, name)
        write_trade_history_report(report, self._run_dir)
        write_trade_history_csv(report, self._run_dir)

        order_report = build_order_history_report_from_session(result, name)
        write_order_history_report(order_report, self._run_dir)
        write_order_history_csv(order_report, self._run_dir)

        # Portfolio headline — the single session unit (= its own currency aggregate).
        portfolio_report = build_portfolio_report_from_session(
            result, name=name, symbol=self._config.symbol)
        write_portfolio_report(portfolio_report, self._run_dir)

        # Diagnostics CSV (#376) — algo-declared sinks, next to events.csv.
        if self._decision_logic:
            flush_decision_diagnostics(self._decision_logic, self._run_dir)

        # Post-session summary — operational view + the #389 analytics line from the
        # model (#393); the big per-trade table stays sim-only / API for live.
        post_session_report = AutotraderPostSessionReport(
            summary_logger=self._summary_logger,
            global_logger=self._global_logger,
        )
        post_session_report.print_report(result, report)
