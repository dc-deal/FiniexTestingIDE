"""
FiniexTestingIDE - Field Study Recorder Format Tests (#332)

Verifies the analysis-ready JSONL contract: a header first line, stable core keys on
every event, monotonic sequence, two recorded planes, None-field omission, and the
session-end marker.
"""

import json

from python.framework.reporting.field_study_recorder import (
    FieldStudyRecorder,
    PLANE_BOT,
    PLANE_BROKER_TRUTH,
)
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


def _read(path):
    lines = [ln for ln in path.read_text(encoding='utf-8').splitlines() if ln.strip()]
    return json.loads(lines[0]), [json.loads(ln) for ln in lines[1:]]


def test_header_is_first_line(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', 'dev', ['p1', 'p2'], _StubLogger())
    recorder.close()
    header, _ = _read(path)
    assert header['record_kind'] == 'header'
    assert header['schema_version'] == '1.0'
    assert header['phases'] == ['p1', 'p2']
    assert header['symbol'] == 'ETHUSD'


def test_events_carry_stable_core_keys_and_monotonic_seq(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', 'dev', ['p1'], _StubLogger())
    recorder.record_phase_start('p1', 0, 'LONG')
    recorder.record_order_event('order_filled', order_id='o1', side='LONG', lots=0.01, price=2000.0, status='filled')
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
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', 'dev', ['p1'], _StubLogger())
    recorder.set_phase('p1', 0)
    recorder.record_order_event('order_filled', order_id='o1', side='LONG', status='filled')
    recorder.record_broker_truth(order_count=0, balances={'USD': 100.0}, is_flat=True)
    recorder.close()
    _, events = _read(path)
    planes = {e['plane'] for e in events}
    assert PLANE_BOT in planes
    assert PLANE_BROKER_TRUTH in planes


def test_none_fields_omitted(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', 'dev', ['p1'], _StubLogger())
    recorder.set_phase('p1', 0)
    recorder.record_order_event('order_cancelled', order_id='o1', status='cancelled')
    recorder.close()
    _, events = _read(path)
    cancel = next(e for e in events if e['event_type'] == 'order_cancelled')
    assert 'lots' not in cancel  # None fields dropped
    assert cancel['order_id'] == 'o1'


def test_session_end_marker_is_last(tmp_path):
    path = tmp_path / 'field_study.jsonl'
    recorder = FieldStudyRecorder(str(path), 'prof', 'ETHUSD', 'dev', ['p1'], _StubLogger())
    recorder.close('done')
    _, events = _read(path)
    assert events[-1]['event_type'] == 'session_end'
