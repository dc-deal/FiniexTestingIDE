"""
FiniexTestingIDE - The Order-Event Stream on Disk (#362)

`io/order_events.jsonl`: a header line naming the schema once, then one line per event, written
and flushed as the event happens. A session can die between two lines and in the middle of one —
the reader keeps everything before a cut-off last line and says that it was cut off; a broken
line anywhere else, or a schema it does not know, is a file it cannot read, and says so.

The stream is not a report: the run index lists it under `stream_files`, never under `artifacts`,
because an empty artifact list is how a run that never finished is told apart from one that did.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.reporting.field_study_recorder import FieldStudyRecorder
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.reporting.io.order_event_stream_io import (
    ORDER_EVENTS_SCHEMA_VERSION,
    read_order_event_stream,
    write_order_event_stream,
)
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import RunHeader
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
    BrokerTruthRecord,
    BrokerTruthSnapshot,
)
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    ReconcileDivergence,
    ReconcileState,
)
from python.framework.types.log_layout_types import RUN_TYPE_SIMULATION
from python.framework.types.trading_env_types.order_event_types import (
    OrderEvent,
    OrderEventPlane,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from tests.autotrader.cold_start.conftest import RecordingLogger

_RUN_ID = '20261006_120000_a1b2c3d4'
_TIME = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _event(seq: int, event_type: OrderEventType = OrderEventType.SUBMITTED,
           order_id: str = 'pos_btcusd_1') -> OrderEvent:
    """One event, as the executor records it."""
    return OrderEvent(
        seq=seq, event_type=event_type, order_id=order_id, submitted_seq=1,
        order_type=OrderType.MARKET, symbol='BTCUSD', direction=OrderDirection.LONG,
        lots=0.01, event_time=_TIME)


def _truth(seq: int, read_reason: BrokerTruthReadReason = BrokerTruthReadReason.SESSION_START,
           **snapshot) -> BrokerTruthRecord:
    """One broker-truth record, as the live executor records it."""
    return BrokerTruthRecord(seq=seq, read_reason=read_reason,
                             snapshot=BrokerTruthSnapshot(**snapshot), ts_init=_TIME)


def _venue_order() -> BrokerOrder:
    """A resting limit the venue reports, raw payload and all."""
    return BrokerOrder(
        broker_ref='OQ3V2K-ABCDE-FGHIJK', symbol='BTCUSD', direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING, price=60000.0,
        client_order_id='p1a2b_3', raw={'descr': {'order': 'buy 0.01 XBTUSD @ limit 60000'}})


def _lines(path: Path) -> list:
    """The file's lines, parsed."""
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class TestTheFile:
    """A header, then one flushed line per event, nulls left out."""

    def test_the_first_line_names_the_schema_once(self, tmp_path):
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_run', RecordingLogger())
        writer(_event(1))
        writer.close()

        header, line = _lines(tmp_path / ORDER_EVENTS_STREAM)
        assert header == {'record_kind': 'header', 'stream': 'order_events',
                          'schema_version': ORDER_EVENTS_SCHEMA_VERSION, 'run_id': _RUN_ID}
        assert 'schema_version' not in line, 'stated once, not on every line'

    def test_an_event_is_on_disk_before_the_next_is_recorded(self, tmp_path):
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_run', RecordingLogger())
        writer(_event(1))

        rows, _, truncated = read_order_event_stream(tmp_path / ORDER_EVENTS_STREAM)
        assert [r.seq for r in rows] == [1] and not truncated, 'flushed, not buffered'
        writer.close()

    def test_a_field_with_no_value_is_left_out(self, tmp_path):
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_run', RecordingLogger())
        writer(_event(1))
        writer.close()

        line = _lines(tmp_path / ORDER_EVENTS_STREAM)[1]
        assert 'fill_price' not in line and 'ts_init' not in line
        assert line['scenario_name'] == 'btc_run'

    def test_a_failed_write_is_said_once_and_ends_the_stream(self, tmp_path, monkeypatch):
        """
        Called from inside a fill: a disk that refuses a line must not end the fill or the
        session. It is said once on the session's channel, and nothing follows the broken line.
        """
        logger = RecordingLogger()
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_run', logger)
        writer(_event(1))

        def refuse(self, record):
            raise OSError(28, 'No space left on device')
        monkeypatch.setattr(JsonlStreamWriter, 'write', refuse)
        writer(_event(2, OrderEventType.FILLED))
        writer(_event(3, OrderEventType.FILLED))
        monkeypatch.undo()
        writer.close()

        assert len(logger.errors) == 1 and 'seq 2' in logger.errors[0]
        rows, _, truncated = read_order_event_stream(tmp_path / ORDER_EVENTS_STREAM)
        assert [r.seq for r in rows] == [1] and not truncated

    def test_a_backtest_writes_its_units_one_after_the_other(self, tmp_path):
        path = write_order_event_stream(tmp_path, _RUN_ID, [
            ('btc_run', [_event(1), _event(2, OrderEventType.FILLED)]),
            ('eth_run', []),
            ('sol_run', [_event(1)]),
        ])

        rows, truths, _ = read_order_event_stream(path)
        assert truths == [], 'a backtest asks no venue'
        assert [(r.scenario_name, r.seq) for r in rows] == [
            ('btc_run', 1), ('btc_run', 2), ('sol_run', 1)]

    def test_a_backtest_that_placed_no_order_writes_no_stream(self, tmp_path):
        assert write_order_event_stream(tmp_path, _RUN_ID, [('btc_run', [])]) is None
        assert not (tmp_path / ORDER_EVENTS_STREAM).exists()


class TestReadingItBack:
    """What a session left behind when it stopped, read for what it is."""

    def _two_events(self, tmp_path: Path) -> Path:
        """A stream with two complete events."""
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_run', RecordingLogger())
        writer(_event(1))
        writer(_event(2, OrderEventType.ACCEPTED))
        writer.close()
        return tmp_path / ORDER_EVENTS_STREAM

    def test_a_cut_off_last_line_is_left_out_and_reported(self, tmp_path):
        path = self._two_events(tmp_path)
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write('{"scenario_name": "btc_run", "seq": 3, "event_ty')

        rows, _, truncated = read_order_event_stream(path)

        assert [r.seq for r in rows] == [1, 2]
        assert truncated

    def test_a_broken_line_in_the_middle_is_a_damaged_file(self, tmp_path):
        path = self._two_events(tmp_path)
        lines = path.read_text(encoding='utf-8').splitlines()
        lines.insert(2, '{"broken')
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        with pytest.raises(ReportArtifactUnreadableError):
            read_order_event_stream(path)

    def test_a_schema_this_reader_does_not_know_is_refused(self, tmp_path):
        path = self._two_events(tmp_path)
        lines = path.read_text(encoding='utf-8').splitlines()
        header = json.loads(lines[0])
        header['schema_version'] = ORDER_EVENTS_SCHEMA_VERSION + 1
        lines[0] = json.dumps(header)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        with pytest.raises(ReportArtifactUnreadableError):
            read_order_event_stream(path)

    def test_the_previous_schema_is_refused_too(self, tmp_path):
        """One version is read: a stream of the previous one is rewritten by re-running its run."""
        path = self._two_events(tmp_path)
        lines = path.read_text(encoding='utf-8').splitlines()
        header = json.loads(lines[0])
        header['schema_version'] = ORDER_EVENTS_SCHEMA_VERSION - 1
        lines[0] = json.dumps(header)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        with pytest.raises(ReportArtifactUnreadableError):
            read_order_event_stream(path)

    @pytest.mark.parametrize('line', ['[1, 2]', '42', '"text"'])
    def test_a_line_that_is_not_an_object_is_a_damaged_file(self, tmp_path, line):
        path = self._two_events(tmp_path)
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')

        with pytest.raises(ReportArtifactUnreadableError):
            read_order_event_stream(path)

    def test_a_byte_that_is_not_utf8_is_a_damaged_file(self, tmp_path):
        """The writer escapes everything outside ASCII, so this cannot be a cut-off line."""
        path = self._two_events(tmp_path)
        with open(path, 'ab') as handle:
            handle.write(b'\xff\xfe')

        with pytest.raises(ReportArtifactUnreadableError):
            read_order_event_stream(path)


class TestBrokerTruthLines:
    """What the venue said, in the same file and on the same counter as the order events."""

    def _write(self, tmp_path: Path, *records) -> Path:
        """A session stream holding the given records, events and broker truth alike."""
        writer = OrderEventStreamWriter(tmp_path, _RUN_ID, 'btc_session', RecordingLogger())
        for record in records:
            if isinstance(record, BrokerTruthRecord):
                writer.write_broker_truth(record)
            else:
                writer(record)
        writer.close()
        return tmp_path / ORDER_EVENTS_STREAM

    def test_both_planes_read_back_on_one_counter(self, tmp_path):
        path = self._write(
            tmp_path,
            _truth(1, venue_orders=[], venue_balances={'USD': 812.4}),
            _event(2),
            _truth(3, BrokerTruthReadReason.SESSION_END, venue_orders=[],
                   venue_balances={'USD': 800.0}))

        rows, truths, truncated = read_order_event_stream(path)

        assert [r.seq for r in rows] == [2]
        assert [(t.seq, t.read_reason) for t in truths] == [
            (1, BrokerTruthReadReason.SESSION_START), (3, BrokerTruthReadReason.SESSION_END)]
        assert all(t.record_plane is OrderEventPlane.BROKER_TRUTH for t in truths)
        assert not truncated

    def test_a_venue_order_is_projected_not_copied(self, tmp_path):
        path = self._write(tmp_path, _truth(1, venue_orders=[_venue_order()], venue_balances={}))

        order = _lines(path)[1]['venue_orders'][0]

        assert order['limit_price'] == 60000.0 and order['client_order_id'] == 'p1a2b_3'
        assert 'raw' not in order, "the venue's payload stays with its adapter"

    def test_the_three_states_of_a_part_survive_the_file(self, tmp_path):
        """A value — an empty one included —, null and named unread, null and unnamed."""
        path = self._write(tmp_path, _truth(
            1, venue_orders=[], venue_balances=None,
            unread_parts=[BrokerTruthPart.VENUE_BALANCES]))

        _, (truth,), _ = read_order_event_stream(path)

        assert truth.venue_orders == [], 'the venue holds no order — a statement, not a gap'
        assert truth.venue_balances is None
        assert truth.unread_parts == [BrokerTruthPart.VENUE_BALANCES], 'the read gave up'
        assert truth.venue_positions is None, 'spot: not read on this occasion'
        assert BrokerTruthPart.VENUE_POSITIONS not in truth.unread_parts

    def test_a_divergent_reconcile_line_names_its_members(self, tmp_path):
        record = BrokerTruthRecord(
            seq=1, read_reason=BrokerTruthReadReason.RECONCILE,
            snapshot=BrokerTruthSnapshot(venue_orders=[_venue_order()]),
            reconcile_state=ReconcileState.DIVERGENT,
            divergence=ReconcileDivergence(ghost_orders=['OQ3V2K-ABCDE-FGHIJK']),
            ts_init=_TIME)
        path = self._write(tmp_path, record)

        _, (truth,), _ = read_order_event_stream(path)

        assert truth.reconcile_state is ReconcileState.DIVERGENT
        assert truth.divergence.ghost_orders == ['OQ3V2K-ABCDE-FGHIJK']
        assert truth.venue_balances is None and truth.unread_parts == [], (
            'no crossing between clean and divergent — balances are not read, not lost')


class TestTheSharedWriter:
    """The field study writes through the same writer, and its lines did not change."""

    def test_a_line_is_the_record_without_its_nulls(self, tmp_path):
        writer = JsonlStreamWriter(tmp_path / 'x.jsonl')
        writer.write({'a': 1, 'b': None, 'c': 'z'})
        writer.close()

        assert (tmp_path / 'x.jsonl').read_text(encoding='utf-8') == '{"a": 1, "c": "z"}\n'

    def test_writing_after_close_does_nothing(self, tmp_path):
        writer = JsonlStreamWriter(tmp_path / 'x.jsonl')
        writer.close()
        writer.write({'a': 1})

        assert (tmp_path / 'x.jsonl').read_text(encoding='utf-8') == ''

    def test_the_field_study_header_is_written_as_before(self, tmp_path):
        path = tmp_path / 'field_study.jsonl'
        recorder = FieldStudyRecorder(
            output_path=str(path), profile='fs', symbol='ETHUSD', release_target='dev',
            phase_ids=['p1'], logger=GlobalLogger('FieldStudyStream'))
        recorder.close('done')

        lines = path.read_text(encoding='utf-8').splitlines()
        assert all(line == json.dumps(json.loads(line)) for line in lines), (
            'one compact JSON object per line, exactly as json.dumps writes it')
        assert all(None not in json.loads(line).values() for line in lines)


class TestTheRunIndexListsItAsAStream:
    """Never an artifact: an empty artifact list is how an unfinished run is recognised."""

    def _run(self, tmp_path: Path) -> tuple:
        """A registered run with a stream in its io/ folder and no report."""
        index = RunIndex(tmp_path / 'runs_index.parquet')
        run_dir = tmp_path / 'runs' / 'simulation' / 'my_set' / _RUN_ID
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        index.register_run(RunHeader(run_id=_RUN_ID, start_time=_TIME,
                                     run_type=RUN_TYPE_SIMULATION, run_name='my_set'), run_dir)
        writer = OrderEventStreamWriter(run_dir / IO_SUBDIR, _RUN_ID, 'btc_run',
                                        RecordingLogger())
        writer(_event(1))
        writer.close()
        return index, run_dir

    def test_the_stream_is_named_while_the_run_is_going(self, tmp_path):
        index, run_dir = self._run(tmp_path)

        index.record_streams(_RUN_ID, run_dir)

        run = index.list_runs()[0]
        assert run.stream_files == [ORDER_EVENTS_STREAM]
        assert run.artifacts == [], 'a run with only its stream has not reported'

    def test_the_report_records_artifacts_without_the_stream(self, tmp_path):
        index, run_dir = self._run(tmp_path)
        (run_dir / IO_SUBDIR / 'portfolio.json').write_text('{}', encoding='utf-8')

        index.record_artifacts(_RUN_ID, run_dir)

        run = index.list_runs()[0]
        assert run.artifacts == ['portfolio.json']
        assert run.stream_files == [ORDER_EVENTS_STREAM]
