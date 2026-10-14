"""
FiniexTestingIDE - Acceptance Study Recorder Format Tests (#332, #358)

Verifies the analysis-ready JSONL contract: a header first line — with where the venue
reports a fill's fee, where the session knew it — stable core keys on every event, monotonic
sequence, two recorded planes, None-field omission, and the session-end marker. The order and venue lines themselves are written by the stream projection
(#566); what is held here is the recorder's own format and the phase it hands the projection.
A line's type and status are enums, each status from the set its type declares, and the JSON
carries their values.
"""

import json

import pytest

from python.framework.reporting.acceptance_study_recorder import AcceptanceStudyRecorder
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.acceptance_study_types.field_study_types import PhaseType
from python.framework.types.acceptance_study_types.study_capture_types import (
    SESSION_END_PHASE,
    STATUS_TYPE_BY_LINE,
    CaptureLineType,
    SnapshotStatus,
)
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseOutcome,
    PhaseResult,
)
from python.framework.types.trading_env_types.order_event_types import (
    OrderEventPlane,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import FeeReport

# The study's name and the step after it — what every recorder is told when it opens
_STUDY = ('Field Study', 'generate the acceptance certificate')


class _StubLogger:
    """Minimal logger — the recorder only emits an info banner."""
    file_logger = None

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _read(path):
    lines = [ln for ln in path.read_text(encoding='utf-8').splitlines() if ln.strip()]
    return json.loads(lines[0]), [json.loads(ln) for ln in lines[1:]]


def test_header_is_first_line(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1', 'p2'], _StubLogger(),
                                       *_STUDY)
    recorder.close()
    header, _ = _read(path)
    assert header['record_kind'] == 'header'
    assert header['schema_version'] == '2.0'
    assert header['phases'] == ['p1', 'p2']
    assert header['symbol'] == 'ETHUSD'
    assert 'release_target' not in header, 'never read — the certificate takes the release'


@pytest.mark.parametrize('fee_report', list(FeeReport), ids=lambda report: report.value)
def test_the_header_records_where_the_venue_reports_a_fill_s_fee(tmp_path, fee_report):
    """The venue's declaration (#508), as its value."""
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(),
                                       *_STUDY, fee_report=fee_report)
    recorder.close()
    header, _ = _read(path)
    assert header['fee_report'] == fee_report.value


@pytest.mark.parametrize('expected', [True, False])
def test_the_header_records_what_the_executor_expects_of_a_fill_s_fee(tmp_path, expected):
    """The answer the certificate judges an estimated fee by (#508) — false is a value too."""
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(),
                                       *_STUDY, venue_fee_expected=expected)
    recorder.close()
    header, _ = _read(path)
    assert header['venue_fee_expected'] is expected


def test_a_header_opened_without_the_declaration_leaves_it_out(tmp_path):
    """Absent, never a guessed value — the certificate then judges no estimate."""
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.close()
    header, _ = _read(path)
    assert 'fee_report' not in header
    assert 'venue_fee_expected' not in header


def test_events_carry_stable_core_keys_and_monotonic_seq(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.record_phase_start('p1', 0, 'LONG')
    recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_FILLED, 'p1', 0,
                             order_id='o1', side='LONG', lots=0.01, price=2000.0,
                             status=OrderEventType.FILLED)
    recorder.record_phase_result(PhaseResult('p1', PhaseType.MARKET_OPEN, PhaseOutcome.PASS, 'filled'))
    recorder.close()
    _, events = _read(path)
    core = {'ts_utc', 'seq', 'plane', 'event_type', 'phase', 'phase_index'}
    for event in events:
        assert core.issubset(event.keys())
    seqs = [e['seq'] for e in events]
    assert seqs == sorted(seqs)


def test_two_planes_recorded(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_FILLED, 'p1', 0,
                             order_id='o1', side='LONG', status=OrderEventType.FILLED)
    recorder.write_projected(OrderEventPlane.BROKER_TRUTH, CaptureLineType.BROKER_SNAPSHOT,
                             'preflight', -1, status=SnapshotStatus.FLAT,
                             extra={'order_count': 0, 'balances': {'USD': 100.0}})
    recorder.close()
    _, events = _read(path)
    planes = {e['plane'] for e in events}
    assert OrderEventPlane.BOT.value in planes
    assert OrderEventPlane.BROKER_TRUTH.value in planes


def test_none_fields_omitted(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_CANCELLED, 'p1', 0,
                             order_id='o1', status=OrderEventType.CANCELLED)
    recorder.close()
    _, events = _read(path)
    cancel = next(e for e in events if e['event_type'] == 'order_cancelled')
    assert 'lots' not in cancel  # None fields dropped
    assert cancel['order_id'] == 'o1'


def test_session_end_marker_is_last(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.close()
    _, events = _read(path)
    assert events[-1]['event_type'] == 'session_end'
    assert events[-1]['phase'] == SESSION_END_PHASE


def test_the_phase_in_progress_is_what_a_submission_is_filed_under(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    assert recorder.get_phase() == ('', -1)
    recorder.record_phase_start('p1', 0, 'LONG')
    assert recorder.get_phase() == ('p1', 0)
    recorder.close()


class _ErrorLogger(_StubLogger):
    """Counts what the recorder reports on the session channel."""

    def __init__(self):
        self.errors = []

    def error(self, message, *args, **kwargs):
        self.errors.append(message)


def test_a_write_that_fails_is_reported_once_and_never_raised(tmp_path, monkeypatch):
    """
    The recorder writes from inside the executor's order path — a disk error there must not
    become an exception in a real-money session. Reported once; the capture stops there.
    """
    logger = _ErrorLogger()
    recorder = AcceptanceStudyRecorder(str(tmp_path / 'field_study.jsonl'), 'prof', 'ETHUSD', ['p1'],
                                       logger, *_STUDY)

    def disk_full(self, record):
        raise OSError('No space left on device')
    monkeypatch.setattr(JsonlStreamWriter, 'write', disk_full)

    recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_FILLED, 'p1', 0,
                             order_id='o1')
    recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_FILLED, 'p1', 0,
                             order_id='o2')
    recorder.close()

    assert len(logger.errors) == 1
    assert 'could not be written' in logger.errors[0]


def test_the_reconciliation_totals_are_filed_at_the_session_end(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(), *_STUDY)
    recorder.record_phase_start('p1', 0, None)
    recorder.record_reconcile_summary({'enabled': True, 'cycles': 3, 'skipped': 0,
                                       'divergences_seen': 0})
    recorder.close()
    _, events = _read(path)
    summary = next(e for e in events if e['event_type'] == 'reconcile_summary')
    assert (summary['phase'], summary['reconcile']['cycles']) == (SESSION_END_PHASE, 3)


class TestALineSaysWhatItsStatusMeans:
    """
    A line's status draws from the set its type declares (#358 prework) — typed, and the JSON
    carries the values it always carried.
    """

    def test_every_line_type_declares_its_status_set(self):
        assert set(STATUS_TYPE_BY_LINE) == set(CaptureLineType)

    def test_the_json_carries_the_values(self, tmp_path):
        path = tmp_path / 'field_study.jsonl'
        recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger(),
                                           *_STUDY)
        recorder.write_projected(OrderEventPlane.BROKER_TRUTH, CaptureLineType.BROKER_SNAPSHOT,
                                 'preflight', -1, status=SnapshotStatus.NOT_FLAT)
        recorder.close()
        _, events = _read(path)
        snapshot, end = events[0], events[-1]
        assert (snapshot['plane'], snapshot['event_type'], snapshot['status']) == (
            'broker_truth', 'broker_snapshot', 'not_flat')
        assert (end['event_type'], end['status']) == ('session_end', 'session end')

    def test_a_status_from_another_set_is_reported_and_still_written(self, tmp_path):
        """
        Reported, never raised: the recorder writes from inside the executor's listener, and a
        capture defect must not end a real-money session.
        """
        logger = _ErrorLogger()
        path = tmp_path / 'field_study.jsonl'
        recorder = AcceptanceStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], logger, *_STUDY)
        recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.ORDER_FILLED, 'p1', 0,
                                 status=PhaseOutcome.PASS)
        recorder.close()
        _, events = _read(path)
        assert len(logger.errors) == 1 and 'order_filled' in logger.errors[0]
        assert events[0]['status'] == 'pass'
