"""
FiniexTestingIDE - Live Side Collector
Reads a live deployment's records for the divergence report (#512) — CAPTURE: everything is read
here, nothing is derived.

It reads in two stages. The first reads no stream: the deployment's ledger rows, its runs in the
run index, how each session ended, its first tick and the two ends of its state frames — enough to
cut the deployment into segments and place their windows, and cheap enough to search many
deployments with. The second reads the sealed day records of each session, for the days to be
classified.

What cannot be read is named on the session or the day and read as not checked, never as absent.
"""

import copy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.bars.warmup_record_reader import read_warmup_record
from python.framework.constants.run_layout_constants import DAY_LABEL_FORMAT
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.reporting.builders.deployment_history_builder import (
    add_unbooked_sessions,
    build_deployment_histories,
)
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    boundary_position_of,
    closing_of,
)
from python.framework.reporting.io.artifact_specs import WARNINGS_ERRORS_ARTIFACT
from python.framework.reporting.io.order_event_stream_io import order_events_of
from python.framework.reporting.io.run_stream_day_record import (
    read_day_record_lines,
    read_day_record_summary,
)
from python.framework.reporting.io.state_frame_stream_io import read_recorded_frames
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.broker_config_freeze import read_frozen_broker_config
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import (
    ParentKind,
    RunInfo,
    RunResultRow,
    SessionBookingState,
    WarningsErrorsOutcome,
)
from python.framework.types.api.state_frame_types import StateFrame, StateFrameStreamEnds
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    BoundaryPosition,
    LiveDayRecords,
    LiveDeploymentCapture,
    LiveSessionCapture,
    RecordedFrames,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_origin_types import CodeIdentity
from python.framework.types.run_stream_types import RunStreamLines
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.time_utils import parse_iso_datetime

# What a read can fail with when a record is missing, damaged or of a shape this reader does not
# know — a data condition, named on the record; anything else is a fault and propagates
_UNREADABLE = (ReportArtifactUnreadableError, OSError, ValueError, KeyError)

# Where a session whose start cannot be read sorts among the others: after them
_NO_START = datetime.min.replace(tzinfo=timezone.utc)


class LiveSideCollector:
    """
    Reads one live deployment's records — its sessions first, their sealed days second.
    """

    def __init__(self,
                 ledger: Optional[RunResultsLedger] = None,
                 run_index: Optional[RunIndex] = None,
                 reader: Optional[RunEntryReader] = None,
                 store: Optional[ReportStore] = None,
                 market_config: Optional[MarketConfigManager] = None,
                 read_broker_config: Callable[[str], Dict[str, Any]] = read_frozen_broker_config,
                 run_configs: Optional[RunConfigStore] = None):
        """
        Args:
            ledger: The run-results ledger; the configured one when omitted
            run_index: The run index; the configured one when omitted
            reader: The run reader; one over that index when omitted
            store: The report store; one over that index and ledger when omitted
            market_config: Where a broker's trading day flips; the configured one when omitted
            read_broker_config: Reads a frozen broker configuration by its id
            run_configs: The run-config store a session's rendered profile is read from; the
                configured one when omitted
        """
        if ledger is None or run_index is None or reader is None or store is None:
            config = AppConfigManager()
            index_path = Path(config.get_file_logging_config_object().run_index)
            ledger_dir = Path(config.get_run_ledger_path())
            ledger = ledger or RunResultsLedger(ledger_dir)
            run_index = run_index or RunIndex(index_path)
            reader = reader or RunEntryReader(index_path)
            store = store or ReportStore(index_path, ledger_dir)
        self._ledger = ledger
        self._run_index = run_index
        self._reader = reader
        self._store = store
        self._market_config = market_config or MarketConfigManager()
        self._read_broker_config = read_broker_config
        self._run_configs = run_configs or RunConfigStore(
            Path(AppConfigManager().get_run_configs_path()))

    def collect_sessions(self, deployment_id: str) -> LiveDeploymentCapture:
        """
        Every session of one deployment, from its ledger rows and the run index — no stream read.

        Args:
            deployment_id: The deployment

        Returns:
            The deployment's sessions, ordered by start
        """
        rows = self._ledger.read_rows(deployment_id=deployment_id)
        runs = [run for run in self._run_index.list_runs()
                if run.parent_kind is ParentKind.DEPLOYMENT and run.parent_id == deployment_id]
        if not rows and not runs:
            raise ValueError(f'No session names the deployment {deployment_id!r} — neither a '
                             f'ledger row nor the run index.')
        brokers = sorted({row.data_broker_type for row in rows if row.data_broker_type})
        if len(brokers) > 1:
            raise ValueError(f'The deployment {deployment_id!r} names {len(brokers)} brokers '
                             f'({", ".join(brokers)}); one deployment is compared on one market.')
        if not brokers:
            raise ValueError(f'No session of the deployment {deployment_id!r} has booked a row '
                             f'yet, so its market — and where its trading day flips — is not '
                             f'known.')
        histories = add_unbooked_sessions(build_deployment_histories(rows), rows, runs)
        states = {session.run_id: session.booking_state
                  for session in histories.get(deployment_id, [])}
        rows_by_run: Dict[str, List[RunResultRow]] = {}
        for row in rows:
            rows_by_run.setdefault(row.run_id, []).append(row)
        run_by_id = {run.run_id: run for run in runs}
        run_ids = sorted(set(states) | set(rows_by_run) | set(run_by_id))
        self._reader.remember_run_dirs(run_ids)
        sessions = [self._capture_session(
            run_id, rows_by_run.get(run_id, []), run_by_id.get(run_id),
            states.get(run_id, SessionBookingState.NOT_BOOKED_YET)) for run_id in run_ids]
        sessions.sort(key=lambda session: (
            session.start_time is None, session.start_time or _NO_START, session.run_id))
        return LiveDeploymentCapture(
            deployment_id=deployment_id, broker_type=brokers[0],
            anchor=self._market_config.get_trading_day_anchor(brokers[0]),
            weekend_closure=self._market_config.has_weekend_closure(brokers[0]),
            sessions=sessions)

    def collect_days(self,
                     capture: LiveDeploymentCapture) -> Dict[Tuple[str, date], LiveDayRecords]:
        """
        What every session of a deployment recorded per trading day it sealed.

        Args:
            capture: The deployment's sessions

        Returns:
            (session, day) → that day's records, for every day a session holds a day record of
        """
        days: Dict[Tuple[str, date], LiveDayRecords] = {}
        for session in capture.sessions:
            directory = self._reader.directory(session.run_id)
            if directory is None:
                continue
            for day in self._read_days(directory, session.run_id, capture.anchor):
                days[(session.run_id, day.trading_day)] = day
        return days

    def collect_session_frames(self, capture: LiveDeploymentCapture) -> Dict[str, RecordedFrames]:
        """
        Every session's state frames, read whole across its sealed days and its own file.

        Args:
            capture: The deployment's sessions

        Returns:
            Session → its frames; a session that records none says so
        """
        return {session.run_id: read_recorded_frames(self._reader, session.run_id,
                                                     RunEntryMemberId.STATE_FRAMES)
                for session in capture.sessions}

    def _capture_session(self, run_id: str, rows: List[RunResultRow], run: Optional[RunInfo],
                         booking_state: SessionBookingState) -> LiveSessionCapture:
        """
        One session's records, read without its streams.

        Args:
            run_id: The session
            rows: Its ledger rows
            run: Its run index entry; None where the index no longer carries it
            booking_state: Whether its figures exist, and if not, why

        Returns:
            The session as its records show it
        """
        unreadable: Dict[str, str] = {}
        rows = sorted(rows, key=lambda row: (row.period_no is None, row.period_no or 0))
        stated = [row for row in rows if not row.measured_nothing()]
        identity = {name: _agreed(stated, name, unreadable) for name in (
            'code_stand', 'data_broker_type', 'rendered_config_id', 'broker_config_id', 'symbols',
            'app_version')}
        symbols = identity['symbols'] or []
        symbol = symbols[0] if symbols else None
        broker_config_id = identity['broker_config_id'] or ''
        lot_step, base_currency = self._symbol_facts(
            broker_config_id, identity['data_broker_type'] or '', symbol, unreadable)
        start = run.start_time if run is not None and run.start_time else (
            rows[0].run_timestamp if rows else '')
        pruned = next((row.records_pruned_at for row in rows if row.records_pruned_at), '')
        return LiveSessionCapture(
            run_id=run_id, start_time=_instant(start, 'start_time', unreadable), rows=rows,
            booking_state=booking_state, code_stand=identity['code_stand'],
            broker_type=identity['data_broker_type'] or '', symbol=symbol,
            rendered_config_id=identity['rendered_config_id'] or '',
            broker_config_id=broker_config_id, lot_step=lot_step, base_currency=base_currency,
            first_tick=self._first_tick(run_id, unreadable),
            outcome=self._outcome(run_id, unreadable),
            frame_ends=self._frame_ends(run_id, unreadable),
            records_pruned_at=_instant(pruned, 'records_pruned_at', unreadable),
            app_version=identity['app_version'],
            rendered_strategy_config=self._rendered_strategy(
                identity['rendered_config_id'] or '', unreadable),
            code_identity=self._code_identity(run_id, unreadable),
            unreadable=unreadable)

    def _symbol_facts(self, broker_config_id: str, broker_type: str, symbol: Optional[str],
                      unreadable: Dict[str, str]) -> Tuple[Optional[float], Optional[str]]:
        """
        The symbol's lot increment and base currency in the broker configuration a session froze.

        A venue's runtime cache names its broker type; a static seed is typed by the folder it lives
        in, which its frozen copy no longer sits in — so the session's own broker names it.

        Args:
            broker_config_id: The frozen configuration's id; '' where the session froze none
            broker_type: The broker the session's rows name
            symbol: The symbol; None where it is not known
            unreadable: Where a failed read is named

        Returns:
            (the lot step, the base currency), each None where it could not be read
        """
        if not broker_config_id or not broker_type or symbol is None:
            return None, None
        try:
            document = copy.deepcopy(self._read_broker_config(broker_config_id))
            broker_config = BrokerConfigFactory.from_serialized_dict(
                BrokerType(document.get('broker_type', broker_type)), document)
            spec = broker_config.get_symbol_specification(symbol)
        except _UNREADABLE as e:
            unreadable['broker_config'] = str(e)
            return None, None
        return spec.volume_step, spec.base_currency

    def _rendered_strategy(self, rendered_config_id: str,
                           unreadable: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        A session's strategy as its components were built, from its rendered profile.

        Args:
            rendered_config_id: The run-config store id of its rendered profile; '' where its
                rows name none
            unreadable: Where a failed read is named

        Returns:
            The rendered strategy; None where it cannot be read
        """
        if not rendered_config_id:
            return None
        try:
            document = self._run_configs.read_frozen(rendered_config_id,
                                                     RunConfigKind.AUTOTRADER_RENDERED)
        except _UNREADABLE as e:
            unreadable['rendered_config'] = str(e)
            return None
        strategy = document.get('strategy_config')
        if not isinstance(strategy, dict):
            unreadable['rendered_config'] = 'its rendered profile holds no strategy'
            return None
        return strategy

    def _code_identity(self, run_id: str, unreadable: Dict[str, str]) -> Optional[CodeIdentity]:
        """
        Which code a session ran, from its header.

        Args:
            run_id: The session
            unreadable: Where a failed read is named

        Returns:
            Its code identity; None where the header is missing, cannot be read or predates it
        """
        try:
            header = self._reader.read_header(run_id)
        except _UNREADABLE as e:
            unreadable['header'] = str(e)
            return None
        return header.code_identity if header is not None else None

    def _first_tick(self, run_id: str, unreadable: Dict[str, str]) -> Optional[datetime]:
        """
        A session's first tick, from its warmup history.

        Args:
            run_id: The session
            unreadable: Where a failed read is named

        Returns:
            The instant; None where the record is missing or the tick never came
        """
        try:
            record = read_warmup_record(run_id, self._reader)
        except _UNREADABLE as e:
            unreadable['warmup_history'] = str(e)
            return None
        return _instant(record.first_tick or '', 'first_tick', unreadable)

    def _outcome(self, run_id: str, unreadable: Dict[str, str]) -> Optional[WarningsErrorsOutcome]:
        """
        How a session ended, from its warnings-errors report.

        Args:
            run_id: The session
            unreadable: Where a failed read is named

        Returns:
            The outcome; None where the session wrote no report
        """
        try:
            report = self._reader.read_section(run_id, WARNINGS_ERRORS_ARTIFACT)
        except _UNREADABLE as e:
            unreadable['warnings_errors'] = str(e)
            return None
        return report.outcome if report is not None else None

    def _frame_ends(self, run_id: str,
                    unreadable: Dict[str, str]) -> Optional[StateFrameStreamEnds]:
        """
        A session's state frames at both ends.

        Args:
            run_id: The session
            unreadable: Where a failed read is named

        Returns:
            The two ends; None where the session records no frames
        """
        try:
            return self._store.get_state_frame_ends(run_id)
        except _UNREADABLE as e:
            unreadable['state_frames'] = str(e)
            return None

    def _read_days(self, directory: RunDirReader, run_id: str,
                   anchor: DayAnchorConfig) -> List[LiveDayRecords]:
        """
        Every sealed trading day one session holds a day record of.

        Args:
            directory: The session's run directory
            run_id: The session
            anchor: Where the market's day flips

        Returns:
            One entry per day, oldest first
        """
        order_paths = _by_day(directory, RunEntryMemberId.ORDER_EVENTS)
        frame_paths = _by_day(directory, RunEntryMemberId.STATE_FRAMES)
        tick_paths = _by_day(directory, RunEntryMemberId.TICK_STREAM_EVENTS)
        days: List[LiveDayRecords] = []
        for day in sorted(set(order_paths) | set(frame_paths) | set(tick_paths)):
            order_path = order_paths.get(day)
            events, unreadable, label, cut, last_time = [], None, None, False, None
            if order_path is not None:
                try:
                    summary = read_day_record_summary(order_path)
                    stream = RunStreamLines(
                        header=summary.header, records=read_day_record_lines(order_path),
                        truncated_tail=summary.cut_last_line, last_unterminated=False)
                    rows, _, cut = order_events_of(stream, order_path.name, str(order_path))
                    events = sorted(rows, key=lambda event: event.seq)
                    label, last_time = summary.trading_day, summary.last_time
                except _UNREADABLE as e:
                    unreadable = str(e)
            frames_path = frame_paths.get(day)
            first_frame, last_frame, closing_position = None, None, None
            if frames_path is not None:
                try:
                    frames_summary = read_day_record_summary(frames_path)
                    first_frame, last_frame = frames_summary.first_time, frames_summary.last_time
                    closing_position = _closing_position(frames_path, closing_of(day, anchor))
                except _UNREADABLE:
                    closing_position = None
            days.append(LiveDayRecords(
                run_id=run_id, trading_day=day, order_events=events, order_events_path=order_path,
                order_events_unreadable=unreadable, order_events_label=label,
                order_events_cut_last_line=cut, order_events_last_time=last_time,
                frames_path=frames_path, frames_first_time=first_frame,
                frames_last_time=last_frame, closing_position=closing_position,
                tick_stream_events_path=tick_paths.get(day)))
        return days


def _by_day(directory: RunDirReader, member_id: RunEntryMemberId) -> Dict[date, Path]:
    """
    A stream's day records by the day their folder names — read where the declaration places the
    day in the path, so a record whose footer cannot be read still belongs to its day.

    Args:
        directory: The session's run directory
        member_id: The stream

    Returns:
        Day → its record
    """
    day_member_id = RUN_ENTRY_MEMBERS_BY_ID[member_id].day_record
    if day_member_id is None:
        return {}
    position = RUN_ENTRY_MEMBERS_BY_ID[day_member_id].path_pattern.split('/').index('{day}')
    found: Dict[date, Path] = {}
    for path in directory.day_record_paths(member_id):
        parts = path.relative_to(directory.get_run_dir()).parts
        try:
            found[datetime.strptime(parts[position], DAY_LABEL_FORMAT).date()] = path
        except (IndexError, ValueError):
            continue   # a folder named by no day is no day's record
    return found


def _closing_position(frames_path: Path, closing: datetime) -> BoundaryPosition:
    """
    What the account held at a day's closing, from the last frame its day record holds.

    Args:
        frames_path: The day record of the state frames
        closing: When the day closed

    Returns:
        The position at the closing
    """
    lines = read_day_record_lines(frames_path)
    for record in reversed(lines):
        if record.get('record_kind') == 'frame':
            frame = StateFrame.model_validate(record)
            if frame.frame_time < closing:
                return boundary_position_of([frame], closing)
    return BoundaryPosition(boundary=closing, frame_time=None, net_lots={}, holdings=None)


def _agreed(rows: List[RunResultRow], name: str, unreadable: Dict[str, str]) -> Any:
    """
    A column that is the same on every row of one run — the first value, a disagreement named.

    Args:
        rows: The run's rows that measured something
        name: The column
        unreadable: Where a disagreement is named

    Returns:
        The value of the first row carrying one; None where none does
    """
    values = [getattr(row, name) for row in rows if getattr(row, name)]
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        unreadable[name] = f'its ledger rows disagree on {name}'
    return values[0]


def _instant(text: str, name: str, unreadable: Dict[str, str]) -> Optional[datetime]:
    """
    An instant one of our own records wrote, read back.

    Args:
        text: The ISO text; '' where the record holds none
        name: What it is, for a failed read
        unreadable: Where a failed read is named

    Returns:
        The instant; None where there is none or it cannot be read
    """
    if not text:
        return None
    try:
        return parse_iso_datetime(text)
    except ValueError as e:
        unreadable[name] = str(e)
        return None
