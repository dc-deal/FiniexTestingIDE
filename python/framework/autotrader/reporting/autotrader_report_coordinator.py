"""
FiniexAutoTrader - Autotrader Report Coordinator

The live counterpart to BatchReportCoordinator: consumes a finished
AutoTraderResult and writes all session run outputs into the run directory —
the event-stream CSV, the unified report artifacts (#391), the algo diagnostics
CSV, and the post-session console summary. Report derivation itself lives in
framework/reporting/*; this only orchestrates the persist + render.
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
import io
import re
import sys

from python.configuration.app_config_manager import AppConfigManager
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.console.feed_stability_summary import FeedStabilitySummary
from python.framework.reporting.console.live_session_summary import LiveSessionSummary
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.run_console_renderer import RunConsoleRenderer
from python.framework.reporting.console.signal_summary import SignalSummary
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.diagnostics_csv_sink import flush_decision_diagnostics
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.builders.broker_report_builder import build_broker_report_from_session
from python.framework.reporting.io.broker_report_io import write_broker_report
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.reporting.builders.run_unit import run_units_from_session
from python.framework.reporting.builders.warnings_errors_report_builder import build_warnings_errors_report_from_session
from python.framework.reporting.io.warnings_errors_report_io import write_warnings_errors_report
from python.framework.reporting.shared_report_coordinator import SharedReportCoordinator
from python.framework.reporting.store.run_provenance_builder import build_run_provenance_from_session
from python.framework.reporting.store.run_results_ledger import append_run_to_ledger
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.scenario_types.scenario_set_types import SignalScenarioInfo


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
        run_timestamp: datetime,
        config: AutoTraderConfig,
        decision_logic: Optional[AbstractDecisionLogic],
        summary_logger: ScenarioLogger,
        global_logger: ScenarioLogger,
        broker_config: Optional[BrokerConfig] = None,
        signal_scenario_map: Optional[Dict[Tuple[str, str], SignalScenarioInfo]] = None,
    ):
        """
        Initialize the report coordinator.

        Args:
            result: The completed session result to report
            run_dir: The session's run directory
            run_timestamp: The session start (UTC) — the ledger row's run timestamp
            config: The autotrader profile config (portfolio unit name/symbol)
            decision_logic: The decision logic (algo diagnostics sinks)
            summary_logger: Logger for the post-session summary + console
            global_logger: Logger for the global file
            broker_config: The resolved live BrokerConfig (the executor's broker) for the
                broker report; None when the session has no executor (startup failure)
            signal_scenario_map: The session's prepared signal sources (#433) — from the same
                shared MountPreparer run the sim batch uses; empty for a session without one
        """
        self._result = result
        self._run_dir = run_dir
        self._run_timestamp = run_timestamp
        self._config = config
        self._decision_logic = decision_logic
        self._summary_logger = summary_logger
        self._global_logger = global_logger
        # Already-resolved BrokerConfig — the sim `data_broker_type` vs. live `broker_type`
        # config-key asymmetry is resolved UPSTREAM (at AutoTrader startup, `config.broker_type`
        # → `create_broker_config`); here we only ever see the resolved object (its
        # `broker_type` is a `BrokerType` enum), so reporting needs no key translation.
        self._broker_config = broker_config
        self._signal_scenario_map = signal_scenario_map or {}

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
        # Sentiment feed label (#438): the scenario's data_sentiment_type (mock; '' when none / live).
        units = run_units_from_session(
            result, name, self._config.symbol,
            sentiment_source=(
                self._config.scenario_settings.data_sentiment_type
                if self._config.scenario_settings else ''),
            # #451: the mock session's planned windows label an episode's origin — the
            # timestamps always come from the run (a real live session has none).
            stress_test_config=(
                self._config.scenario_settings.stress_test_config
                if self._config.scenario_settings else None))

        # Report artifacts (JSON + CSV) go into the session's io/ subfolder (#396 housekeeping).
        io_dir = self._run_dir / IO_SUBDIR

        # DERIVE + PERSIST the 7 units-derived sections shared with sim (#403): trade / order /
        # portfolio (single session = its own currency aggregate) / pending (empty for live) /
        # execution-stats / run-summary / worker-decision. The models feed the unified console.
        unified = SharedReportCoordinator.derive_and_persist(
            units, io_dir, self._signal_scenario_map)

        # Warnings & errors — tiered model (#395). Persisted for API parity with the sim runs;
        # the closing block keeps reading the session buffers directly (same structured source,
        # avoids double-rendering the emergency cause, §35).
        warnings_errors_report = build_warnings_errors_report_from_session(
            result, name, self._config.symbol)
        write_warnings_errors_report(warnings_errors_report, io_dir)

        # Broker configuration — the session's single broker + symbol (unified model;
        # same artifact + API shape as the sim runs). Skipped if the session never built
        # an executor (startup failure → no resolved broker_config).
        broker_report = None
        if self._broker_config is not None:
            broker_report = build_broker_report_from_session(
                self._broker_config, self._config.symbol)
            write_broker_report(broker_report, io_dir)

        # Run-results ledger (#390) — append the session to the persistent cross-run store the
        # Parameter Optimization system ranks over. Same RunSummary model + provenance as the sim
        # pipeline; the profile's strategy_config makes the param_hash comparable to the backtest
        # (sim/live parity). A live session is never swept; an emergency → status='error' row.
        provenance = build_run_provenance_from_session(
            self._config, self._run_dir, self._run_timestamp, warnings_errors_report)
        append_run_to_ledger(unified.run_summary, provenance)

        # Diagnostics CSV (#376) — algo-declared sinks, next to events.csv.
        if self._decision_logic:
            flush_decision_diagnostics(self._decision_logic, self._run_dir)

        # === PRESENT — the unified end-of-run console (#403 Phase 2): the shared sections in
        # sim order (trade / portfolio / broker / worker performance), then the live session
        # summary as the closing block. Live is one unit → the per-currency aggregates are
        # skipped (redundant); the same ordered renderer the sim coordinator uses. ===
        renderer = ConsoleRenderer()
        threshold = AppConfigManager().get_console_logging_config_object().scenario_detail_threshold

        console = RunConsoleRenderer(
            unit_count=unified.run_summary.unit_count,
            threshold=threshold,
            portfolio_summary=PortfolioSummary(
                unified.portfolio, unified.pending_orders, unified.execution_stats, None),
            trade_history_summary=TradeHistorySummary(unified.trade_history, unified.order_history),
            broker_summary=BrokerSummary(broker_report) if broker_report is not None else None,
            signal_summary=SignalSummary(unified.signal) if unified.signal.units else None,
            feed_stability_summary=(
                FeedStabilitySummary(unified.feed_stability)
                if unified.feed_stability.units else None),
            performance_summary=PerformanceSummary(unified.worker_decision),
            warnings_summary=WarningsSummary(warnings_errors_report),
            closing_block=LiveSessionSummary(
                result, unified.trade_history, self._run_dir, unified.run_summary),
        )

        # Render once (live always full detail); capture, print to console (with colors), and
        # log the ANSI-stripped block to the summary file.
        old_stdout = sys.stdout
        sys.stdout = capture = io.StringIO()
        console.render_all(renderer, summary_detail=True)
        sys.stdout = old_stdout
        full_output = capture.getvalue()

        print(full_output)
        self._summary_logger.info(re.sub(r'\033\[[0-9;]+m', '', full_output))
        self._global_logger.info(
            f"📋 Session summary written — {result.ticks_processed} ticks, "
            f"{len(result.warning_messages)} warnings, {len(result.error_messages)} errors")
