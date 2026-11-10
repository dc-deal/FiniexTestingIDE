"""
One comparison of a live deployment with backtests of its windows, from the records to the
verdict (#512).

The phases, each one a building block of its own: the live side is read from the deployment's
records — this machine's stores or a copy of the bot host's — with the venue's ledger deciding
where the account was changed; each segment's window is backtested from what its sessions
recorded; the gate decides whether the backtest may be compared at all; the gaps, the measures,
the venue's confirmation of the trades our archive lacks; then the verdict, the report, its record
and its text.

A segment whose window the archive does not cover yet, whose backtest does not start, or which the
gate refuses is named with why and not measured — never measured in part.
"""

import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.constants.divergence_report_constants import (
    DIVERGENCE_LIVE_INDEX_FILE,
    DIVERGENCE_SCENARIO_SET_NAME,
    DIVERGENCE_WARMUP_RECORD_FILE,
)
from python.framework.constants.divergence_verdict_constants import (
    ATTRIBUTION_RESIDUAL_BOUND,
    CROSS_SECTION_WEIGHTS,
    EXECUTION_PRICE_BOUND_PCT,
    EXPLAINED_FLIPS_MAX,
    FEE_RATE_BOUND_PP,
    MINIMUM_COMPARED_DAYS,
    SIZE_BOUND_PCT,
)
from python.framework.discoveries.data_coverage.data_coverage_report_cache import (
    DataCoverageReportCache,
)
from python.framework.factory.recorded_scenario_builder import RecordedScenarioBuilder
from python.framework.factory.venue_history_reader_factory import (
    public_trade_reader,
    venue_ledger_reader,
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.builders.divergence.backtest_side_collector import (
    BacktestSideCollector,
    window_trades,
)
from python.framework.reporting.builders.divergence.gap_list_builder import (
    build_gap_list,
    trade_id_runs,
)
from python.framework.reporting.builders.divergence.live_segment_builder import (
    derive_segments,
    recorded_window_of,
)
from python.framework.reporting.builders.divergence.live_side_collector import LiveSideCollector
from python.framework.reporting.builders.divergence.order_divergence_builder import (
    taker_rate_pct_of,
)
from python.framework.reporting.builders.divergence.segment_measures_builder import (
    measure_segment,
)
from python.framework.reporting.builders.divergence.session_records import (
    SessionRecords,
    configured_records,
    copied_records,
)
from python.framework.reporting.builders.divergence.venue_record_builder import (
    account_changes_of,
    bot_order_refs,
    capture_bars,
    confirm_capture,
    decision_bar_of,
)
from python.framework.reporting.builders.divergence.venue_side_collector import (
    VenueSideCollector,
)
from python.framework.reporting.console.divergence_report_console import render_divergence_report
from python.framework.reporting.store.divergence_report_store import (
    new_report_directory,
    write_report,
)
from python.framework.store.broker_config_freeze import read_frozen_broker_config
from python.framework.types.api.divergence_report_types import (
    DecisionComparison,
    DeclaredStop,
    DivergenceReading,
    DivergenceReport,
    ExplainedFlipKind,
    GateFindingRow,
    GateReport,
    LiveSegment,
    SegmentMeasures,
    SegmentReport,
    VenueAccountChange,
)
from python.framework.types.divergence_capture_types import (
    DerivedSegment,
    LiveDayRecords,
    LiveDeploymentCapture,
)
from python.framework.types.divergence_gate_types import DivergenceGateResult
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.recorded_scenario_types import RecordedScenarioInputs
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.validation_types import Severity
from python.framework.utils.atomic_file_utils import write_file_atomic
from python.framework.utils.git_info_utils import get_git_info
from python.framework.validators.divergence_day_validator import live_side_of
from python.framework.validators.divergence_gate_validator import check_divergence_gate
from python.framework.validators.divergence_verdict_validator import judge_segment
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_strategy_runner import initialize_batch_and_run


class DivergenceReportCoordinator:
    """
    Runs one comparison and keeps its record.

    Args:
        deployment_id: The deployment compared
        decision_timeframe: The timeframe whose bar's close is a day's decision
        reading: The rules the comparison is read by
        logger: Where the venue's readers and the phases report
        until: The boundary the comparison ends at; None for the last one sealed
        records_root: A copy of the bot host's data home to read the sessions from; None for this
            machine's stores
        declared_stops: The stops the operator declared
        ledger_credentials: The configured name of the key that may read the venue's ledger
        run_purpose: What the backtests' runs are for — REGULAR for a comparison, FIXTURE in a test
    """

    def __init__(self, deployment_id: str, decision_timeframe: str, reading: DivergenceReading,
                 logger: AbstractLogger, until: Optional[datetime] = None,
                 records_root: Optional[Path] = None,
                 declared_stops: Sequence[DeclaredStop] = (),
                 ledger_credentials: str = 'venues/kraken_ledger_credentials.json',
                 run_purpose: RunPurpose = RunPurpose.REGULAR):
        self._deployment_id = deployment_id
        self._decision_timeframe = decision_timeframe
        self._reading = reading
        self._logger = logger
        self._until = until
        self._records_root = records_root
        self._declared_stops = list(declared_stops)
        self._ledger_credentials = ledger_credentials
        self._run_purpose = run_purpose

    def run(self) -> Tuple[DivergenceReport, str, Path]:
        """
        The comparison, from the records to its record.

        Returns:
            (the report, its text as printed, the comparison's directory)
        """
        report_dir = new_report_directory()
        report_id = report_dir.name
        records = (copied_records(self._records_root, report_dir / DIVERGENCE_LIVE_INDEX_FILE)
                   if self._records_root is not None else configured_records())
        collector = LiveSideCollector(
            ledger=records.ledger, run_index=records.run_index, reader=records.reader,
            store=records.report_store, run_configs=records.run_configs,
            read_broker_config=lambda document_id: read_frozen_broker_config(
                document_id, records.run_configs))
        capture = collector.collect_sessions(self._deployment_id)
        day_records = collector.collect_days(capture)
        venue = self._venue(capture.broker_type, self._adapters_of(capture, records))
        venue_changes, ledger_unread = self._venue_changes(venue, capture, day_records)
        segments, named = derive_segments(capture, self._until, self._declared_stops,
                                          venue_changes=venue_changes)
        live = live_side_of(capture, segments, named, day_records, self._until)
        live_frames = collector.collect_session_frames(capture)
        outages = collector.collect_session_outages(capture)
        symbol = next((s.symbol for s in capture.sessions if s.symbol), '') or ''
        reports = [self._segment(report_dir, derived, live_segment, capture, day_records,
                                 live_frames, outages, records, venue, symbol)
                   for derived, live_segment in zip(segments, live.segments)]
        git = get_git_info()
        report = DivergenceReport(
            report_id=report_id, created_at=datetime.now(timezone.utc),
            deployment_id=self._deployment_id, symbol=symbol,
            decision_timeframe=self._decision_timeframe, reading=self._reading,
            until_requested=self._until, records_root=records.root,
            report_commit=git.commit if git is not None else '',
            report_dirty=git.dirty if git is not None else True,
            weights=dict(CROSS_SECTION_WEIGHTS), bounds=_bounds(), live=live,
            segments=reports, venue_ledger_unread=ledger_unread,
            unreadable=[f'{s.run_id}: {k} — {v}' for s in capture.sessions
                        for k, v in s.unreadable.items()])
        text = render_divergence_report(report)
        write_report(report, text, report_dir)
        return report, text, report_dir

    def _venue(self, broker_type: str, adapters: Set[str]) -> VenueSideCollector:
        """
        The venue's own record, through the seam's readers and the broker's connection numbers —
        only where the sessions traded at the venue. A session on the mock adapter replayed
        recorded data against a simulated venue: the real venue's ledger and trades are another
        account's, and read as this one's they would end the window at movements the bot never made.

        Args:
            broker_type: The deployment's broker
            adapters: The adapters the deployment's sessions ran on

        Returns:
            The venue side's collector
        """
        if adapters != {'live'}:
            return VenueSideCollector(
                broker_type, None, None,
                not_read_because=f'the sessions ran on the {", ".join(sorted(adapters)) or "unknown"}'
                                 f' adapter, so no venue holds their record')
        policy = MarketConfigManager().get_broker_entry(broker_type).broker_transport.connection
        return VenueSideCollector(
            broker_type, public_trade_reader(broker_type, policy, self._logger),
            venue_ledger_reader(broker_type, self._ledger_credentials, policy, self._logger))

    @staticmethod
    def _adapters_of(capture: LiveDeploymentCapture, records: SessionRecords) -> Set[str]:
        """
        The adapters a deployment's sessions ran on, from their rendered profiles.

        Args:
            capture: The deployment's sessions
            records: Where their records are read

        Returns:
            The adapter types; empty where no session's rendered profile could be read
        """
        adapters: Set[str] = set()
        for session in capture.sessions:
            if not session.rendered_config_id:
                continue
            try:
                rendered = records.run_configs.read_frozen(session.rendered_config_id,
                                                           RunConfigKind.AUTOTRADER_RENDERED)
            except (OSError, ValueError, KeyError):
                continue
            adapters.add(str((rendered.get('blocks') or {}).get('adapter_type') or ''))
        adapters.discard('')
        return adapters

    def _venue_changes(self, venue: VenueSideCollector, capture: LiveDeploymentCapture,
                       day_records: Mapping[Tuple[str, date], LiveDayRecords]
                       ) -> Tuple[Optional[List[VenueAccountChange]], str]:
        """
        The account's movements the venue's ledger shows that are no order of the bot.

        Args:
            venue: The venue side's collector
            capture: The deployment's sessions
            day_records: What each session recorded per day

        Returns:
            (the movements, None where the ledger was not read; why it was not read)
        """
        starts = [s.start_time for s in capture.sessions if s.start_time is not None]
        if not starts:
            return None, 'no session names its start'
        end = self._until or datetime.now(timezone.utc)
        read = venue.read_venue_ledger(min(starts), end)
        if read.entries is None:
            return None, read.unreadable
        events = [event for record in day_records.values() for event in record.order_events]
        return account_changes_of(read.entries, bot_order_refs(events),
                                  _account_currencies(capture)), ''

    def _segment(self, report_dir: Path, derived: DerivedSegment, segment: LiveSegment,
                 capture: LiveDeploymentCapture,
                 day_records: Mapping[Tuple[str, date], LiveDayRecords],
                 live_frames: Mapping, outages: Mapping, records: SessionRecords,
                 venue: VenueSideCollector, symbol: str) -> SegmentReport:
        """
        One segment: its backtest, the gate, the measures and the verdict.

        Args:
            report_dir: The comparison's directory, named by its id
            derived: The segment as derived, with its sessions
            segment: The segment as the live side classified it
            capture: The deployment's sessions
            day_records: What each session recorded per day
            live_frames: Each session's frames
            outages: Each session's tick-stream outages
            records: Where the sessions' records are read
            venue: The venue side's collector
            symbol: The deployment's symbol

        Returns:
            The segment's report; not compared, with why, where a step refused
        """
        set_name = DIVERGENCE_SCENARIO_SET_NAME.format(report_id=report_dir.name,
                                                       segment=derived.index)
        named = SegmentReport(segment=segment, scenario_set_name=set_name)
        window = derived.window
        refusal = preflight(derived, capture.broker_type, symbol)
        if refusal:
            return named.model_copy(update={'not_compared_because': refusal})
        warmup_file = keep_warmup_record(
            records.reader.path_of(derived.sessions[0].run_id, RunEntryMemberId.WARMUP_HISTORY),
            report_dir, derived.index)
        named = named.model_copy(update={'warmup_record_file': warmup_file})
        run_id, refusal = self._backtest(derived, set_name, records, report_dir)
        if refusal:
            return named.model_copy(update={'not_compared_because': refusal})
        from_store = configured_records()
        backtest_collector = BacktestSideCollector(
            ledger=from_store.ledger, reader=from_store.reader, run_configs=from_store.run_configs)
        backtest = backtest_collector.collect_run(run_id)
        scenario_name = backtest.scenarios[0].name if backtest.scenarios else ''
        in_window = [s for s in derived.sessions
                     if s.get_began() is None or s.get_began() < window.end]
        gate = check_divergence_gate(in_window, backtest, window)
        gate_report = _gate_report(gate)
        named = named.model_copy(update={'backtest_run_id': run_id, 'gate': gate_report})
        if not gate_report.comparable:
            return named.model_copy(update={
                'not_compared_because': 'the gate refuses the comparison: ' + '; '.join(
                    f.check for f in gate_report.findings if f.severity == Severity.ERROR.value)})
        scenario_records = backtest_collector.collect_scenario_records(run_id, scenario_name)
        run_ids = {s.run_id for s in derived.sessions}
        tick_index = TickIndexManager()
        tick_index.load_index()
        ids, msc = window_trades(tick_index, capture.broker_type, symbol, window.start, window.end)
        runs = trade_id_runs(ids, msc)
        answers = venue.answer_runs(symbol, runs)
        coverage = DataCoverageReportCache().get_report(capture.broker_type, symbol)
        gaps = build_gap_list(
            window.start, window.end, list(coverage.gaps or []) if coverage else [], runs,
            {k: v.outages for k, v in outages.items() if k in run_ids},
            {k: v.frames for k, v in live_frames.items() if k in run_ids},
            venue_answers=answers.counts,
            unreadable=answers.unreadable + [f'{k}: {v.unreadable}' for k, v in outages.items()
                                             if k in run_ids and v.unreadable])
        broker_document = read_frozen_broker_config(derived.sessions[0].broker_config_id,
                                                    records.run_configs)
        timeframes = sorted({tf for frame in scenario_records.frames.frames
                             for tf in frame.closed_bars} | {self._decision_timeframe})
        stamps = backtest_collector.collect_render_stamps(capture.broker_type, symbol, timeframes)
        measures = measure_segment(
            segment, capture, day_records, live_frames, backtest, scenario_records,
            taker_rate_pct_of(broker_document), self._decision_timeframe, gaps, stamps)
        measures, venue_unread = self.confirm_captures(measures, venue, tick_index,
                                                        capture.broker_type, symbol)
        lot_step = derived.sessions[0].lot_step
        return named.model_copy(update={
            'measures': measures, 'verdict': judge_segment(measures, self._reading, lot_step),
            'venue_unread': answers.unreadable + venue_unread})

    def _backtest(self, derived: DerivedSegment, set_name: str, records: SessionRecords,
                  report_dir: Path) -> Tuple[str, str]:
        """
        The segment's backtest, built from what its sessions recorded and run like any backtest.

        Args:
            derived: The segment
            set_name: The generated scenario set's name
            records: Where the sessions' records are read
            report_dir: Where the generated set is kept

        Returns:
            (the backtest's run id; why it did not run, '' where it ran)
        """
        window = recorded_window_of(derived)
        rendered = records.run_configs.read_frozen(window.rendered_config_id,
                                                   RunConfigKind.AUTOTRADER_RENDERED)
        inputs = RecordedScenarioInputs(
            scenario_set_name=set_name, scenario_name=f'segment_{derived.index}',
            rendered_profile=rendered, warmup_from_run=window.warmup_from_run,
            broker_config_document=window.broker_config_id, start=window.start, end=window.end,
            balances=window.balances or {}, run_purpose=self._run_purpose)
        path = RecordedScenarioBuilder.write(RecordedScenarioBuilder.build(inputs), report_dir)
        loaded = ScenarioConfigLoader(records.reader, records.run_configs).load_config(str(path))
        summary = initialize_batch_and_run(loaded, AppConfigManager())
        if summary is None:
            return '', 'the backtest did not start — its reason is in the global log'
        failed = [result for result in summary.process_result_list if not result.success]
        if failed:
            return '', 'the backtest failed: ' + '; '.join(
                f'{r.scenario_name}: {r.error_type or ""} {r.error_message or ""}'.strip()
                for r in failed)
        run_id = newest_run_named(set_name)
        if run_id is None:
            return '', f'no run named {set_name} is in the run index'
        return run_id, ''

    def confirm_captures(self, measures: SegmentMeasures, venue: VenueSideCollector,
                          tick_index: TickIndexManager, broker_type: str,
                          symbol: str) -> Tuple[SegmentMeasures, List[str]]:
        """
        Every day proposed as a capture difference, confirmed or refuted by the venue's trades in
        its decision bar.

        Args:
            measures: The segment's measures
            venue: The venue side's collector
            tick_index: The archive's tick index
            broker_type: The broker
            symbol: The symbol

        Returns:
            (the measures with each such day answered; what of the venue could not be read)
        """
        bars = capture_bars(measures.decisions, self._decision_timeframe)
        if not bars:
            return measures, []
        found = venue.bar_trades(symbol, bars)
        decisions: List[DecisionComparison] = []
        for comparison in measures.decisions:
            bar = decision_bar_of(comparison, self._decision_timeframe)
            trades = found.trades.get(bar[0]) if bar is not None else None
            if comparison.proposed_kind is not ExplainedFlipKind.CAPTURE or trades is None:
                decisions.append(comparison)
                continue
            ids, _ = window_trades(tick_index, broker_type, symbol, bar[0], bar[1])
            decisions.append(confirm_capture(comparison, {int(i) for i in ids}, trades))
        return measures.model_copy(update={'decisions': decisions}), found.unreadable


def keep_warmup_record(source: Optional[Path], report_dir: Path, segment: int) -> str:
    """
    A copy of the warmup history a segment's backtest starts from, kept beside the comparison — the
    session's own record lies on another machine or in a copy that may not stay.

    Args:
        source: The session's warmup history; None where its run is unknown
        report_dir: The comparison's directory
        segment: The segment's number

    Returns:
        The kept file's name; '' where the session holds none
    """
    if source is None or not source.exists():
        return ''
    name = DIVERGENCE_WARMUP_RECORD_FILE.format(segment=segment)
    write_file_atomic(report_dir / name, lambda path: shutil.copyfile(source, path))
    return name


def preflight(derived: DerivedSegment, broker_type: str, symbol: str) -> str:
    """
    Whether a segment can be backtested at all.

    Args:
        derived: The segment
        broker_type: The broker
        symbol: The symbol

    Returns:
        Why not, in a sentence; '' where it can
    """
    window = derived.window
    if window.start is None or window.end is None:
        missing = 'start' if window.start is None else 'end'
        return f'the window has no {missing} ({window.end_cause.value})'
    return archive_reaches_past(broker_type, symbol, window.end)


def archive_reaches_past(broker_type: str, symbol: str, end: datetime) -> str:
    """
    Whether the archive holds a symbol's ticks past a window's end, so a backtest can cover it.

    Args:
        broker_type: The broker
        symbol: The symbol
        end: The window's end

    Returns:
        Why not, in a sentence; '' where it does
    """
    tick_index = TickIndexManager()
    tick_index.load_index()
    coverage = tick_index.get_symbol_file_coverage(broker_type, symbol)
    if not coverage:
        return f'the archive holds no {symbol} ticks of {broker_type}'
    archive_end = pd.Timestamp(coverage['end_time'])
    archive_end = (archive_end.tz_localize('UTC') if archive_end.tzinfo is None
                   else archive_end.tz_convert('UTC')).to_pydatetime()
    if archive_end <= end:
        return (f'the archive ends {archive_end.isoformat()}, not past the window\'s end '
                f'{end.isoformat()} — wait for the import that covers it')
    return ''


def newest_run_named(set_name: str, index_path: Optional[Path] = None) -> Optional[str]:
    """
    The newest run of a scenario set in the run index — a comparison's set names one backtest.

    Args:
        set_name: The set's name
        index_path: The run index; the configured one when omitted

    Returns:
        The run id; None where no run is named so
    """
    index_path = index_path or Path(AppConfigManager().get_file_logging_config_object().run_index)
    if not index_path.exists():
        return None
    rows = pd.read_parquet(index_path, columns=['run_id', 'run_name', 'start_time'])
    rows = rows[rows['run_name'] == set_name]
    if rows.empty:
        return None
    return str(rows.sort_values('start_time')['run_id'].iloc[-1])


def _gate_report(gate: DivergenceGateResult) -> GateReport:
    """
    The gate's answer as the report keeps it.

    Args:
        gate: The gate's result

    Returns:
        Whether the pair is comparable, every finding, and the files a declaration permitted
    """
    path = gate.code_path
    return GateReport(
        comparable=gate.is_comparable(),
        findings=[GateFindingRow(check=f.check, severity=f.severity.value, message=f.message)
                  for f in gate.findings],
        release=path.release if path is not None else '',
        session_commit=path.session_commit if path is not None else '',
        backtest_commit=path.backtest_commit if path is not None else '',
        declared_files=[v.path for v in path.files if v.declaration is not None]
        if path is not None else [])


def _account_currencies(capture: LiveDeploymentCapture) -> Set[str]:
    """
    The currencies the compared account holds — its base and its quote.

    Args:
        capture: The deployment's sessions

    Returns:
        The currencies
    """
    currencies: Set[str] = set()
    for session in capture.sessions:
        if session.base_currency:
            currencies.add(session.base_currency)
        frame = session.frame_ends.first if session.frame_ends is not None else None
        if frame is not None and frame.account_currency:
            currencies.add(frame.account_currency)
    return currencies


def _bounds() -> Dict[str, float]:
    """
    The bounds applied, by name, for the record.

    Returns:
        Each bound
    """
    return {
        'explained_flips_max': float(EXPLAINED_FLIPS_MAX),
        'execution_price_pct': EXECUTION_PRICE_BOUND_PCT,
        'fee_rate_pp': FEE_RATE_BOUND_PP,
        'size_pct': SIZE_BOUND_PCT,
        'attribution_residual': ATTRIBUTION_RESIDUAL_BOUND,
        'minimum_compared_days': float(MINIMUM_COMPARED_DAYS),
    }
