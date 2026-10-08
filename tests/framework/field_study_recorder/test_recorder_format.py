"""
FiniexTestingIDE - Field Study Recorder Format Tests (#332)

Verifies the analysis-ready JSONL contract: a header first line, stable core keys on
every event, monotonic sequence, two recorded planes, None-field omission, and the
session-end marker. The order and venue lines themselves are written by the stream projection
(#566); what is held here is the recorder's own format and the phase it hands the projection.
"""

import json

from python.framework.reporting.field_study_recorder import (
    PLANE_BOT,
    PLANE_BROKER_TRUTH,
    SESSION_END_PHASE,
    FieldStudyRecorder,
)
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.autotrader_types.field_study_types import (
    PhaseOutcome,
    PhaseResult,
    PhaseType,
)


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
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1', 'p2'], _StubLogger())
    recorder.close()
    header, _ = _read(path)
    assert header['record_kind'] == 'header'
    assert header['schema_version'] == '2.0'
    assert header['phases'] == ['p1', 'p2']
    assert header['symbol'] == 'ETHUSD'
    assert 'release_target' not in header, 'never read — the certificate takes the release'


def test_events_carry_stable_core_keys_and_monotonic_seq(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
    recorder.record_phase_start('p1', 0, 'LONG')
    recorder.write_projected(PLANE_BOT, 'order_filled', 'p1', 0, order_id='o1', side='LONG',
                             lots=0.01, price=2000.0, status='filled')
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
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
    recorder.write_projected(PLANE_BOT, 'order_filled', 'p1', 0, order_id='o1', side='LONG',
                             status='filled')
    recorder.write_projected(PLANE_BROKER_TRUTH, 'broker_snapshot', 'preflight', -1,
                             status='flat', extra={'order_count': 0, 'balances': {'USD': 100.0}})
    recorder.close()
    _, events = _read(path)
    planes = {e['plane'] for e in events}
    assert PLANE_BOT in planes
    assert PLANE_BROKER_TRUTH in planes


def test_none_fields_omitted(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
    recorder.write_projected(PLANE_BOT, 'order_cancelled', 'p1', 0, order_id='o1',
                             status='cancelled')
    recorder.close()
    _, events = _read(path)
    cancel = next(e for e in events if e['event_type'] == 'order_cancelled')
    assert 'lots' not in cancel  # None fields dropped
    assert cancel['order_id'] == 'o1'


def test_session_end_marker_is_last(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
    recorder.close('done')
    _, events = _read(path)
    assert events[-1]['event_type'] == 'session_end'
    assert events[-1]['phase'] == SESSION_END_PHASE


def test_the_phase_in_progress_is_what_a_submission_is_filed_under(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
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
    recorder = FieldStudyRecorder(str(tmp_path / 'field_study.jsonl'), 'prof', 'ETHUSD', ['p1'],
                                  logger)

    def disk_full(self, record):
        raise OSError('No space left on device')
    monkeypatch.setattr(JsonlStreamWriter, 'write', disk_full)

    recorder.write_projected(PLANE_BOT, 'order_filled', 'p1', 0, order_id='o1')
    recorder.write_projected(PLANE_BOT, 'order_filled', 'p1', 0, order_id='o2')
    recorder.close()

    assert len(logger.errors) == 1
    assert 'could not be written' in logger.errors[0]


def test_the_reconciliation_totals_are_filed_at_the_session_end(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', ['p1'], _StubLogger())
    recorder.record_phase_start('p1', 0, None)
    recorder.record_reconcile_summary({'enabled': True, 'cycles': 3, 'skipped': 0,
                                       'divergences_seen': 0})
    recorder.close()
    _, events = _read(path)
    summary = next(e for e in events if e['event_type'] == 'reconcile_summary')
    assert (summary['phase'], summary['reconcile']['cycles']) == (SESSION_END_PHASE, 3)
