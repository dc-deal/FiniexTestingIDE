"""
Report store (#391) — resolves persisted run-report artifacts under the run tree.

The API's read-only source: given a run id, find the run's artifact (written by either pipeline
into its run directory), read it, and — where the API filters — apply the shared filter. Where a
file lies is the run store's declaration, asked through the run entry reader. A run is located
through the run index, never by walking the tree — a directory means nothing to this class (#475).

ONE typed getter serves every artifact (#486). It used to be one hand-written getter per
artifact, fifteen of them differing in three tokens each; the artifact spec carries those three
tokens, and `get(run_id, BROKER_ARTIFACT)` is still statically a `BrokerReport`.
"""

from datetime import datetime
from pathlib import Path
import json
from typing import Any, Dict, List, Optional, Tuple, TypeVar

from pydantic import BaseModel, ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.run_layout_constants import (
    STATE_FRAMES_SCHEMA_VERSION,
    STATE_FRAMES_STREAM,
)
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.fixture_catalog.fixture_catalog import entry_ids
from python.framework.fixture_catalog.fixture_production_store import (
    PRODUCTION_RECORD_FILE,
    FixtureProductionStore,
)
from python.framework.reporting.io.artifact_specs import (
    ORDER_HISTORY_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
)
from python.framework.reporting.io.order_event_stream_io import order_events_of
from python.framework.reporting.io.report_artifact_io import ArtifactSpec
from python.framework.reporting.io.report_filters import (
    filter_order_history_report,
    filter_trade_history_report,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_list_figures import get_run_list_figures
from python.framework.reporting.store.served_run_header import served_run_header
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.recorded_stream_types import (
    RecordedStateFrame,
    StateFramePage,
    StateFrameStreamOverview,
)
from python.framework.types.api.report_types import (
    OrderEventsReport,
    OrderHistoryReport,
    RunConfigSnapshot,
    RunHeader,
    RunInfo,
    RunListFigures,
    TradeHistoryReport,
)
from python.framework.types.api.state_frame_types import (
    StateFrame,
    StateFrameStreamEnds,
)
from python.framework.types.run_stream_types import RunStreamEnds, StreamTimeWindow
from python.framework.types.store_member_types import RunEntryMemberId

T = TypeVar('T', bound=BaseModel)


class ReportStore:
    """Locates + serves persisted run-report artifacts (simulation + live runs)."""

    def __init__(self, run_index_path: Optional[Path] = None, ledger_dir: Optional[Path] = None):
        """
        Args:
            run_index_path: The run index to read; from config when not given. Injectable so a
                caller pointed at an isolated tree can be pointed at that tree's index too,
                rather than asking the real one about runs that only exist in tmp
            ledger_dir: The run-results ledger the run list joins its figures from; from config
                when not given, injectable for the same reason
        """
        index_path = Path(
            run_index_path or AppConfigManager().get_file_logging_config_object().run_index)
        self._index = RunIndex(index_path)
        self._reader = RunEntryReader(index_path)
        self._ledger_dir = Path(ledger_dir or AppConfigManager().get_run_ledger_path())
        # Beside the index it describes runs of, wherever that index is (#576).
        self._fixture_record = FixtureProductionStore(index_path.parent / PRODUCTION_RECORD_FILE,
                                                      declared_entries=set(entry_ids()))

    def list_runs(self) -> List[RunInfo]:
        """Every indexed run, both types, newest first.

        Not only runs carrying artifacts: `artifacts` says which do, and a caller that wants the
        narrower set filters on it. An index that silently omitted a type would be its own
        surprise.

        Returns:
            One identity row per run — id, run type, owning set / profile, artifacts
        """
        return self._index.list_runs()

    def list_runs_with_results(self) -> List[RunInfo]:
        """
        Every indexed run, with what the run-results ledger recorded it DID.

        The index says what a run IS and the ledger what it did; the two are joined here on
        `run_id`, once for the whole list. A run the ledger holds nothing for keeps `results`
        None — it is still going, died before its close, or never reported. Whether a run of the
        fixture catalog is still its entry's current fixture is joined the same way, from the
        catalog's production record.

        Returns:
            The runs of `list_runs`, each carrying its figures where the ledger has them
        """
        figures = get_run_list_figures(self._ledger_dir)
        superseded = self._fixture_record.fixture_superseded_by_run()
        runs = self._index.list_runs()
        return [run.model_copy(update={
                    **(_figure_fields(figures[run.run_id]) if run.run_id in figures else {}),
                    'fixture_superseded': superseded.get(run.run_id)})
                for run in runs]

    def get(self, run_id: str, spec: ArtifactSpec[T]) -> Optional[T]:
        """
        Read one of a run's report artifacts.

        An artifact that is present but does not match the current model is named rather than
        allowed to escape as a bare validation failure — the usual cause is age, and an
        unexplained server error says nothing about that.

        Args:
            run_id: The run's identity
            spec: Which artifact to read; its model is what the result is typed as

        Returns:
            The decoded report, or None when the run has no such artifact
        """
        directory = self._reader.directory(run_id)
        if directory is None:
            return None
        try:
            return directory.read_section(spec)
        except ValidationError as e:
            raise ReportArtifactUnreadableError(
                spec.filename, str(directory.section_path(spec)), str(e)) from e

    def get_trade_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        close_reason: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Optional[TradeHistoryReport]:
        """
        Read + filter a run's trade-history report.

        Filtering stays a STORE concern rather than the API's: console, file and API all filter
        through the one path, so a filtered view cannot disagree with itself between surfaces.

        Args:
            run_id: The run's identity
            symbol / close_reason / start / end: Filters (see filter_trade_history_report)

        Returns:
            The filtered report, or None if the run has no trade-history artifact
        """
        report = self.get(run_id, TRADE_HISTORY_ARTIFACT)
        if report is None:
            return None
        return filter_trade_history_report(report, symbol, close_reason, start, end)

    def get_order_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[OrderHistoryReport]:
        """
        Read + filter a run's order-history report.

        Args:
            run_id: The run's identity
            symbol / status: Filters (see filter_order_history_report)

        Returns:
            The filtered report, or None if the run has no order-history artifact
        """
        report = self.get(run_id, ORDER_HISTORY_ARTIFACT)
        if report is None:
            return None
        return filter_order_history_report(report, symbol, status)

    def get_order_events(
        self,
        run_id: str,
        scenario_name: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> Optional[OrderEventsReport]:
        """
        Read a run's order-event stream, optionally narrowed to one unit or one order (#362).

        Read whenever the stream exists — a live session's stream grows from its first order, and
        a session that died before its report has one, which is when it is worth the most. A
        session's stream is read across the trading days it sealed out of it (#476). A cut-off
        last line is left out and reported on the report rather than failing the read.

        Narrowed to one order, the broker-truth lines drop out: each one is the venue's whole
        account at a moment, never a step of one order.

        Args:
            run_id: The run's identity
            scenario_name: Keep only this unit's lines
            order_id: Keep only this order's events

        Returns:
            The report, or None when the run has no stream
        """
        stream = self._reader.read_stream(run_id, RunEntryMemberId.ORDER_EVENTS)
        if stream is None:
            return None
        path = self._reader.directory(run_id).path_of(RunEntryMemberId.ORDER_EVENTS)
        rows, truths, truncated = order_events_of(stream, path.name, str(path))
        if scenario_name is not None:
            rows = [row for row in rows if row.scenario_name == scenario_name]
            truths = [truth for truth in truths if truth.scenario_name == scenario_name]
        if order_id is not None:
            rows = [row for row in rows if row.order_id == order_id]
            truths = []
        return OrderEventsReport(
            run_id=run_id, events=rows, count=len(rows), broker_truth=truths,
            truncated_tail=truncated)

    def get_state_frame_ends(self, run_id: str) -> Optional[StateFrameStreamEnds]:
        """
        A run's state-frame stream at its two ends — its first frame and its latest (#611).

        Read without the lines between, whatever the stream's length, and while the session still
        writes it — across the trading days it sealed out of it (#476), so right after a seal the
        latest frame is the sealed day's last. A header naming another stream or a schema this
        reader does not know, records without a header, and a frame that does not fit the model
        make the stream unreadable rather than half-read.

        Args:
            run_id: The run's identity

        Returns:
            The two ends, or None when the run records no state frames
        """
        ends = self._reader.read_stream_ends(run_id, RunEntryMemberId.STATE_FRAMES)
        if ends is None:
            return None
        return self._state_frame_ends_of(run_id, ends)

    def get_state_frame_overview(self, run_id: str) -> Optional[StateFrameStreamOverview]:
        """
        A run's state-frame stream at its two ends and by its trading days, from one read of its
        file (#580) — a unit's timeline. Unreadable on the same terms as its two ends.

        Args:
            run_id: The run's identity

        Returns:
            The overview, or None when the run records no state frames
        """
        overview = self._reader.read_stream_overview(run_id, RunEntryMemberId.STATE_FRAMES)
        if overview is None:
            return None
        return StateFrameStreamOverview(ends=self._state_frame_ends_of(run_id, overview.ends),
                                        days=overview.days)

    def get_state_frame_page(self, run_id: str, window: StreamTimeWindow,
                             limit: int) -> Optional[StateFramePage]:
        """
        One page of a run's state frames within a time window, each with its position in the
        stream (#580). Unreadable on the same terms as its two ends, and a frame on the page that
        does not fit the model makes it unreadable too.

        Args:
            run_id: The run's identity
            window: The stretch to read
            limit: The most frames the page holds, at least one

        Returns:
            The page, or None when the run records no state frames
        """
        page = self._reader.read_stream_page(run_id, RunEntryMemberId.STATE_FRAMES, window, limit)
        if page is None:
            return None
        path = self._state_frames_path(run_id)
        unit_name = self._state_frame_header_of(path, page.header, bool(page.records))[0]
        try:
            frames = [RecordedStateFrame.model_validate({**record, 'record_no': record_no})
                      for record, record_no in zip(page.records, page.record_nos)]
        except ValidationError as e:
            raise ReportArtifactUnreadableError(path.name, str(path), str(e)) from e
        return StateFramePage(unit_name=unit_name, frames=frames, matched=page.matched,
                              truncated=page.truncated, next_time=page.next_time,
                              newest_time=page.newest_time)

    def _state_frame_ends_of(self, run_id: str, ends: RunStreamEnds) -> StateFrameStreamEnds:
        """
        A state-frame stream's two ends as the routes read them — its header checked, its frames
        held to the model.

        Args:
            run_id: The run's identity
            ends: The stream's two ends as read

        Returns:
            The two ends
        """
        path = self._state_frames_path(run_id)
        unit_name, cadence_timeframe, cadence_s = self._state_frame_header_of(
            path, ends.header, ends.last is not None)
        try:
            first = StateFrame.model_validate(ends.first) if ends.first is not None else None
            last = StateFrame.model_validate(ends.last) if ends.last is not None else None
        except (KeyError, TypeError, ValueError) as e:
            raise ReportArtifactUnreadableError(path.name, str(path), str(e)) from e
        return StateFrameStreamEnds(unit_name=unit_name, cadence_timeframe=cadence_timeframe,
                                    cadence_s=cadence_s, first=first, last=last,
                                    truncated_tail=ends.truncated_tail)

    def _state_frames_path(self, run_id: str) -> Path:
        """
        Where a run's state-frame stream is written, for the message that it cannot be read.

        Args:
            run_id: The run's identity

        Returns:
            The stream's file
        """
        return self._reader.directory(run_id).path_of(RunEntryMemberId.STATE_FRAMES)

    @staticmethod
    def _state_frame_header_of(path: Path, header: Optional[Dict[str, Any]],
                               has_records: bool
                               ) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        """
        What a state-frame stream's header says — the unit, and the cadence as a timeframe and in
        seconds. A header naming another stream or a schema this reader does not know, and records
        without a header, make the stream unreadable rather than half-read.

        Args:
            path: The stream's file, for the message
            header: Its header line, None when none was read
            has_records: Whether the stream holds a record

        Returns:
            (unit name, cadence timeframe, cadence in seconds) — all None while the stream is
            opened and its header not yet on the disk
        """
        if header is None:
            if has_records:
                raise ReportArtifactUnreadableError(
                    path.name, str(path), 'records without a header line')
            # Opened, and its header not yet on the disk: no frame yet
            return None, None, None
        stream = header.get('stream')
        version = header.get('schema_version')
        if stream != STATE_FRAMES_STREAM or version != STATE_FRAMES_SCHEMA_VERSION:
            raise ReportArtifactUnreadableError(
                path.name, str(path),
                f'its header names stream {stream!r} at schema_version {version!r}; this reader '
                f'knows {STATE_FRAMES_STREAM!r} at {STATE_FRAMES_SCHEMA_VERSION}')
        try:
            return (str(header['unit_name']), str(header['cadence_timeframe']),
                    float(header['cadence_s']))
        except (KeyError, TypeError, ValueError) as e:
            raise ReportArtifactUnreadableError(path.name, str(path), str(e)) from e

    def get_config_snapshot(self, run_id: str) -> Optional[RunConfigSnapshot]:
        """
        The configuration a run was commissioned with, resolved from the run-config store.

        Read from the STORE and not from the run directory. The run used to keep its own verbatim
        copy beside it, which was redundant the moment #538 began freezing the same content under
        a content id — and worse than redundant: the copy was governed by a LOGGING switch while
        the header declared it unconditionally, so a record could name a file that was not there.
        The copy is gone; `config_id` is the pointer that survives.

        Serves the frozen CONTENT and never the store's index row — that row carries
        `source_path`, an operator's own workspace path, which may name `user_configs/` or
        `user_algos/`. The content itself is clean.

        Args:
            run_id: The run's identity

        Returns:
            The configuration, or None when the run is unknown or predates the store — a run
            started before #538 carries an empty `config_id`, which is genuinely unknown rather
            than absent
        """
        info = next((r for r in self.list_runs() if r.run_id == run_id), None)
        if info is None or not info.config_id:
            return None
        frozen = RunConfigStore(
            Path(AppConfigManager().get_run_configs_path())).frozen_path_of(info.config_id)
        if frozen is None or not frozen.exists():
            return None
        return RunConfigSnapshot(
            run_id=run_id,
            config_snapshot=info.config_snapshot,
            config_id=info.config_id,
            config=json.loads(frozen.read_text(encoding='utf-8')),
        )

    def get_run_header(self, run_id: str) -> Optional[RunHeader]:
        """
        A run's header as it may be served (#582) — the record, with every path relative.

        Args:
            run_id: The run's identity

        Returns:
            The served header, or None when the run is unknown. A header the current model cannot
            read raises ReportArtifactUnreadableError; one that is gone from a run the index still
            lists raises FileNotFoundError, because that is a different fault with its own remedy
        """
        directory = self._reader.directory(run_id)
        if directory is None:
            return None
        try:
            header = directory.read_header()
        except ValidationError as e:
            # The reason is served, so it names the run and the fields that failed and never a
            # value: Pydantic's own text quotes the input, and a header's input holds paths.
            failed = ', '.join('.'.join(str(part) for part in error['loc']) or error['type']
                               for error in e.errors()[:5])
            raise ReportArtifactUnreadableError(
                directory.path_of(RunEntryMemberId.HEADER).name, f'run {run_id}',
                f'fields that do not match: {failed}') from e
        return served_run_header(header)


def _figure_fields(figures: RunListFigures) -> Dict[str, Any]:
    """
    The RunInfo fields one run's ledger figures fill.

    Args:
        figures: What the ledger recorded for the run

    Returns:
        Field name → value, the nested figures kept as models
    """
    return {'results': list(figures.results), 'run_outcome': figures.run_outcome,
            'error_count': figures.error_count, 'warning_count': figures.warning_count,
            'log_warning_count': figures.log_warning_count,
            'tick_timespan_seconds': figures.tick_timespan_seconds}
