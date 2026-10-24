"""
FiniexAutoTrader - Autotrader Report Coordinator

The live counterpart to BatchReportCoordinator: consumes a finished
AutoTraderResult and writes all session run outputs into the run directory —
the event-stream CSV, the unified report artifacts (#391), the algo diagnostics
CSV, and the post-session console summary. Report derivation itself lives in
framework/reporting/*; this only orchestrates the persist + render.
"""
import io
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.acceptance_studies.abstract_acceptance_study import AbstractAcceptanceStudy
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.discoveries.signal_coverage.signal_scenario_info import SignalScenarioInfo
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.builders.broker_report_builder import (
    build_broker_report_from_session,
)
from python.framework.reporting.builders.cold_start_report_builder import (
    build_cold_start_report_from_session,
)
from python.framework.reporting.builders.run_unit import (
    run_units_from_session,
    unit_roster_from_session,
)
from python.framework.reporting.builders.safety_report_builder import (
    build_safety_report_from_session,
)
from python.framework.reporting.builders.venue_account_report_builder import (
    build_venue_account_report,
)
from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.console.feed_stability_summary import FeedStabilitySummary
from python.framework.reporting.console.autotrader_session_summary import AutotraderSessionSummary
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.run_console_renderer import RunConsoleRenderer
from python.framework.reporting.console.signal_summary import SignalSummary
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.diagnostics_csv_sink import flush_decision_diagnostics
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.reporting.io.artifact_specs import (
    BOOKING_PERIODS_ARTIFACT,
    BROKER_ARTIFACT,
    COLD_START_ARTIFACT,
    SAFETY_ARTIFACT,
    VENUE_ACCOUNT_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
)
from python.framework.reporting.shared_report_coordinator import SharedReportCoordinator
from python.framework.reporting.store.run_provenance_builder import (
    build_run_provenance_from_session,
)
from python.framework.reporting.builders.booking_periods_report_builder import (
    build_booking_periods_report,
)
from python.framework.reporting.console.booking_periods_summary import BookingPeriodsSummary
from python.framework.reporting.store.run_results_ledger import append_run_to_ledger
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.store.entry_members.run_footprint import (
    expectation,
    expectation_after,
    write_footprint,
)
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.api.report_types import RunSummary
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.log_level import LogLevel
from python.framework.types.run_footprint_types import MemberExpectation
from python.framework.types.signal_data_types import SignalObservedSeries
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.console_renderer import ConsoleRenderer


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
        run_id: str,
        run_timestamp: datetime,
        config: AutoTraderConfig,
        decision_logic: Optional[AbstractDecisionLogic],
        summary_logger: ScenarioLogger,
        global_logger: ScenarioLogger,
        broker_config: Optional[BrokerConfig] = None,
        signal_scenario_map: Optional[Dict[Tuple[str, str], SignalScenarioInfo]] = None,
        replayed_scenario: Optional[SingleScenario] = None,
        observed_feed: Optional[SignalObservedSeries] = None,
        deployment_id: str = '',
    ):
        """
        Initialize the report coordinator.

        Args:
            result: The completed session result to report
            run_dir: The session's run directory
            run_id: The session's identity — every report names the run it was built from, so
                a consumer can check what it received rather than trusting the route (#475)
            run_timestamp: The session start (UTC) — the ledger row's run timestamp
            config: The autotrader profile config (portfolio unit name/symbol)
            decision_logic: The decision logic (algo diagnostics sinks)
            summary_logger: Logger for the post-session summary + console
            global_logger: Logger for the global file
            broker_config: The resolved live BrokerConfig (the executor's broker) for the
                broker report; None when the session has no executor (startup failure)
            signal_scenario_map: The session's prepared signal sources (#433) — from the same
                shared MountPreparer run the sim batch uses; empty for a session without one
            replayed_scenario: A mock session's scenario after the mount filled what it read —
                its ledger row's consumption record; None for a live-adapter session
        """
        self._result = result
        self._run_dir = run_dir
        self._run_id = run_id
        # The RESOLVED deployment, '' when this session stands alone (#497).
        self._deployment_id = deployment_id
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
        self._replayed_scenario = replayed_scenario
        self._observed_feed = observed_feed

    def generate_and_log(self) -> RunSummary:
        """
        Write all session artifacts + print the post-session summary.

        Returns:
            The run's figures as the report built them — the operator's closing alert reads
            them from here rather than computing its own (#235)
        """
        result = self._result
        writer = RunEntryWriter(self._run_dir, RUN_TYPE_AUTOTRADER)

        # Long-format event-stream CSV (#330) — replaces the previous
        # autotrader_orders.csv + autotrader_trades.csv pair with a single
        # chronological events.csv (FIX ExecutionReport style).
        events_writer = EventStreamWriter.from_autotrader_result(
            trade_history=result.trade_history or [],
            order_history=result.order_history or [],
        )
        events_writer.flush(writer, RunEntryMemberId.SESSION_EVENTS)

        # Unified report artifacts (#391) — the canonical models the console/CSV
        # render and the API serves; same shape as sim, one set per session run.
        # The session is one run unit → tagged with the profile/symbol name (#393).
        name = self._config.get_unit_name()
        # Extract the session's single run unit once (#391 Phase 2).
        # Sentiment feed label (#438): the scenario's data_sentiment_type (mock; '' when none / live).
        units = run_units_from_session(
            result, name, self._config.symbol,
            # The broker key, not the display name: it is what the data API is addressed by,
            # so a report consumer can link this unit to its chart (sim has always carried it).
            # The broker whose ticks the session READ — a mock session may replay another
            # broker's archive than the one it trades against, and the chart lives there. A
            # plain str ('kraken_spot'), NOT self._broker_config.broker_type, which is a
            # BrokerType enum.
            data_broker_type=self._config.get_data_broker_type(),
            data_sentiment_type=self._config.get_data_sentiment_type(),
            # #451: the mock session's planned windows label an episode's origin — the
            # timestamps always come from the run (a live-adapter session has none).
            stress_test_config=(
                self._config.scenario_settings.stress_test_config
                if self._config.scenario_settings else None))

        # DERIVE + PERSIST the 7 units-derived sections shared with sim (#403): trade / order /
        # portfolio (single session = its own currency aggregate) / pending (empty for live) /
        # execution-stats / run-summary / worker-decision. The models feed the unified console.
        unified = SharedReportCoordinator.derive_and_persist(
            self._run_id, units, writer, self._signal_scenario_map, self._observed_feed,
            roster=unit_roster_from_session(result, name))

        # Warnings & errors — tiered model (#395). Persisted for API parity with the sim runs;
        # the closing block keeps reading the session buffers directly (same structured source,
        # avoids double-rendering the emergency cause, §35).
        warnings_errors_report = build_warnings_errors_report_from_session(
            self._run_id, result, name, self._config.symbol)
        writer.write_section(WARNINGS_ERRORS_ARTIFACT, warnings_errors_report)

        # Broker configuration — the session's single broker + symbol (unified model;
        # same artifact + API shape as the sim runs). Skipped if the session never built
        # an executor (startup failure → no resolved broker_config).
        broker_report = None
        if self._broker_config is not None:
            broker_report = build_broker_report_from_session(
                self._run_id, self._broker_config, self._config.symbol)
            writer.write_section(BROKER_ARTIFACT, broker_report)

        # Cold start (#355 / #493) — what the boot step found and what was decided about it.
        # Absent for a simulation, a dry run and a Field Study: the three cases with nothing
        # to find. Filed whether the algo accounted for the situation or not, so a yes never
        # makes the case invisible.
        # An EMPTY situation is not a finding: writing an artifact and printing a header for
        # every clean boot would bury the boots that DID inherit something under noise.
        cold_start_report = None
        if (result.cold_start_situation is not None
                and not result.cold_start_situation.is_empty()):
            cold_start_report = build_cold_start_report_from_session(
                self._run_id,
                result.cold_start_situation,
                result.cold_start_verdict,
                type(self._decision_logic).__name__ if self._decision_logic else '',
            )
            writer.write_section(COLD_START_ARTIFACT, cold_start_report)

        # Safety (#356 / #314) — the risk denominator this session ran against and how far
        # the account moved away from it. Written whenever a baseline was TAKEN, including
        # for a session whose limits were switched off: that record is what says what would
        # have fired, which is the question a parity proof asks before anything is armed.
        # No baseline means nothing was ever measured, and an artifact then asserts a
        # denominator that does not exist.
        safety_report = None
        if (result.safety_session is not None
                and result.safety_session.baseline is not None):
            safety_report = build_safety_report_from_session(
                self._run_id, result.safety_session, self._config.safety,
                self._config.symbol)
            writer.write_section(SAFETY_ARTIFACT, safety_report)

        # Venue account (#362) — what the venue held at the session's start and end, and what
        # the reconciliation recorded in between, derived from the broker-truth lines of the
        # order-event stream. Written only when the session asked its venue anything: a dry run
        # against a real venue asks nothing, and a row would describe an account nobody read.
        venue_account_report = None
        if any(unit.broker_truth for unit in units):
            venue_account_report = build_venue_account_report(self._run_id, units)
            writer.write_section(VENUE_ACCOUNT_ARTIFACT, venue_account_report)

        # The booking periods, derived once and used three times: the artifact, the table below,
        # and — through the periods themselves — the ledger rows. The reconciliation against the
        # run's own figure is computed here rather than in the renderer (§12), and it is why the
        # report is PERSISTED rather than rebuilt from the ledger later: the independent figure it
        # checks against exists only while the run does (#539).
        booking_periods = build_booking_periods_report(
            self._run_id, units, unified.run_summary)
        writer.write_section(BOOKING_PERIODS_ARTIFACT, booking_periods)

        # Every artifact of this session is on disk now — the index records WHICH, so a consumer
        # knows what it can fetch instead of discovering it by 404 (#475).
        SharedReportCoordinator.record_run_artifacts(self._run_dir)

        # Run-results ledger (#390) — append the session to the persistent cross-run store the
        # Parameter Optimization system ranks over. Same RunSummary model + provenance as the sim
        # pipeline; the profile's strategy_config makes the param_hash comparable to the backtest
        # (sim/live parity). An AutoTrader session is never swept; an emergency → status='error'
        # row. The header is read from THIS run directory, never looked up in the derived run index
        # (#551), and an unreadable one is reported in the session's own summary log.
        provenance = build_run_provenance_from_session(
            self._config, self._run_id, self._run_timestamp, warnings_errors_report,
            deployment_id=self._deployment_id, run_dir=self._run_dir,
            logger=self._summary_logger, replayed_scenario=self._replayed_scenario)
        # The session's ledger entries (#537): its booking periods ARE its ledger rows, and no
        # aggregate row is written beside them — the deployment history sums over rows, and a
        # summary standing next to its own evidence would count the month twice.
        append_run_to_ledger(
            unified.run_summary, provenance,
            [period for unit in units for period in unit.booking_periods])

        # Diagnostics CSV (#376) — algo-declared sinks, next to events.csv.
        if self._decision_logic:
            flush_decision_diagnostics(self._decision_logic, writer)

        # === PRESENT — the unified end-of-run console (#403 Phase 2): the shared sections in
        # sim order (trade / portfolio / broker / worker performance), then the AutoTrader
        # session summary as the closing block. A session is one unit → the per-currency
        # aggregates are skipped (redundant); the same ordered renderer the sim coordinator
        # uses. ===
        renderer = ConsoleRenderer()
        threshold = AppConfigManager().get_console_logging_config_object().scenario_detail_threshold

        console = RunConsoleRenderer(
            unit_count=unified.run_summary.unit_count,
            threshold=threshold,
            portfolio_summary=PortfolioSummary(
                unified.portfolio, unified.pending_orders, unified.execution_stats, None),
            trade_history_summary=TradeHistorySummary(
                unified.trade_history, unified.order_history, unified.execution_stats),
            broker_summary=BrokerSummary(broker_report) if broker_report is not None else None,
            signal_summary=SignalSummary(unified.signal) if unified.signal.units else None,
            feed_stability_summary=(
                FeedStabilitySummary(unified.feed_stability)
                if unified.feed_stability.units else None),
            performance_summary=PerformanceSummary(unified.worker_decision),
            warnings_summary=WarningsSummary(warnings_errors_report),
            # The session's booking periods as an ordered section (#537) — an AutoTrader
            # session renders once, with detail ON, so it always gets the full table on the
            # terminal AND in the file.
            booking_periods_summary=BookingPeriodsSummary(booking_periods),
            closing_block=AutotraderSessionSummary(
                result, unified.trade_history, self._run_dir, unified.run_summary,
                warnings_errors_report, cold_start_report, safety_report,
                venue_account_report),
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
            f'📋 Session summary written — {result.ticks_processed} ticks, '
            f'{result.count_logged(LogLevel.WARNING)} warnings, '
            f'{result.count_logged(LogLevel.ERROR)} errors')

        # === CLOSE (#621): the session's own footprint, written last so a session that has one
        # got here — after the console and the summary log, which can still fail ===
        expectations = self._footprint_expectations(
            # From what the table had to hold, not from the write: a write that fails returns
            # nothing either, and must not read as a session that placed no order
            events_written=events_writer.has_events(),
            broker_written=broker_report is not None,
            cold_start_written=cold_start_report is not None,
            safety_written=safety_report is not None,
            venue_account_written=venue_account_report is not None)
        try:
            write_footprint(self._run_dir, self._run_id, RUN_TYPE_AUTOTRADER, expectations)
        except OSError as error:
            self._global_logger.error(
                f"The session's footprint could not be written ({error}) — the session reads as "
                f'not closed')
        return unified.run_summary

    def _footprint_expectations(self, events_written: bool, broker_written: bool,
                                cold_start_written: bool, safety_written: bool,
                                venue_account_written: bool) -> List[MemberExpectation]:
        """
        For each file a session writes only sometimes, whether this session expected it — judged
        from what the session knows at its close, so a writer that failed reads as failed rather
        than as a file that did not apply. The report sections are judged by the branches above
        that decided them; the streams by the profile and by how far the session got, since a
        start that was refused or aborted closes too; the logs by the log switch.

        Args:
            events_written: Whether the event table was written
            broker_written: Whether the broker section was written
            cold_start_written: Whether the cold-start section was written
            safety_written: Whether the safety section was written
            venue_account_written: Whether the venue-account section was written

        Returns:
            One expectation per member the session decides on
        """
        logs_on = AppConfigManager().get_file_logging_config_object().scenario_enabled
        capture = (self._decision_logic.get_capture_member()
                   if isinstance(self._decision_logic, AbstractAcceptanceStudy) else None)
        sinks = self._decision_logic.get_diagnostics_sinks() if self._decision_logic else []
        reach = self._result.session_reach
        log_members = (RunEntryMemberId.SESSION_GLOBAL_LOG, RunEntryMemberId.SESSION_SUMMARY_LOG,
                       RunEntryMemberId.SESSION_BOOT_LOG)
        return [
            *(expectation(member_id, logs_on, 'log files are switched on',
                          'log files are switched off') for member_id in log_members),
            expectation_after(RunEntryMemberId.SESSION_DAY_LOG, logs_on, reach.day_log_opened,
                              'log files are switched on', 'log files are switched off',
                              'the session made no pass of its tick loop'),
            expectation_after(RunEntryMemberId.ORDER_EVENTS, True, reach.order_record_opened,
                              'the session opened its order record', '',
                              'the session stopped before it opened its order record'),
            expectation(RunEntryMemberId.SESSION_EVENTS, events_written,
                        'the session placed an order', 'the session placed no order'),
            expectation(RunEntryMemberId.BROKER, broker_written,
                        'the session built its broker configuration',
                        'the session built no broker configuration'),
            expectation(RunEntryMemberId.COLD_START, cold_start_written,
                        'the boot found something to inherit', 'the boot found nothing to inherit'),
            expectation(RunEntryMemberId.SAFETY, safety_written,
                        'a risk baseline was taken', 'no risk baseline was taken'),
            expectation(RunEntryMemberId.VENUE_ACCOUNT, venue_account_written,
                        'the session asked its venue', 'the session asked its venue nothing'),
            expectation(RunEntryMemberId.SESSION_DIAGNOSTICS,
                        any(sink.get_row_count() for sink in sinks),
                        'the logic recorded a diagnostics row',
                        'the logic recorded no diagnostics row'),
            expectation_after(RunEntryMemberId.STATE_FRAMES, self._config.state_frames.enabled,
                              reach.tick_loop_entered, 'the profile records state frames',
                              'the profile records no state frames',
                              'the session stopped before its tick loop'),
            expectation_after(RunEntryMemberId.TICK_STREAM_EVENTS,
                              self._config.tick_source.type != 'mock', reach.tick_source_started,
                              'the session reads its ticks from a venue',
                              'the session replays recorded ticks',
                              'the session stopped before its tick source started'),
            expectation_after(RunEntryMemberId.FIELD_STUDY_CAPTURE,
                              capture is RunEntryMemberId.FIELD_STUDY_CAPTURE,
                              reach.capture_opened,
                              'the decision logic is the field study',
                              'the decision logic is no field study',
                              'the session stopped before the study opened its capture'),
            expectation_after(RunEntryMemberId.SECURITY_CERT_CAPTURE,
                              capture is RunEntryMemberId.SECURITY_CERT_CAPTURE,
                              reach.capture_opened,
                              'the decision logic is a security-study stage',
                              'the decision logic is no security-study stage',
                              'the session stopped before the study opened its capture'),
        ]
