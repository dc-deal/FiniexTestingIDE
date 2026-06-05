"""
FiniexTestingIDE - Field Study Certificate Analyzer Tests (#332)

Drives FieldStudyCertificate against synthetic JSONL fixtures: a clean run certifies
PASSED, a run with a failed phase + not-flat end certifies FAILED, and a run missing a
phase result (aborted) certifies FAILED.
"""

import json
from pathlib import Path

import pytest

from python.framework.reporting.field_study_certificate import FieldStudyCertificate

_FIXTURES = Path('tests/fixtures/field_study')


def test_pass_run_certifies_passed(tmp_path):
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'pass_run.jsonl'), release_version='test', reports_dir=str(tmp_path)
    )
    cert = json.loads(cert_path.read_text())
    assert cert['overall_status'] == 'PASSED'
    assert cert['flat_at_session_end'] is True
    assert cert['failed_phases'] == []
    assert cert['missing_phases'] == []
    assert cert['realized_cost'] == pytest.approx(0.008)
    assert cert['record_kind'] == 'certificate'


def test_fail_run_certifies_failed(tmp_path):
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'fail_run.jsonl'), release_version='test', reports_dir=str(tmp_path)
    )
    cert = json.loads(cert_path.read_text())
    assert cert['overall_status'] == 'FAILED'
    assert 'limit_modify_test' in cert['failed_phases']
    assert cert['flat_at_session_end'] is False


def test_missing_phase_result_fails(tmp_path):
    jsonl = tmp_path / 'partial.jsonl'
    jsonl.write_text(
        json.dumps({
            'record_kind': 'header', 'schema_version': '1.0', 'started_utc': 'x',
            'profile': 'p', 'symbol': 'ETHUSD', 'release_target': 'dev', 'phases': ['a', 'b'],
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 1, 'plane': 'bot', 'event_type': 'phase_result',
            'phase': 'a', 'phase_index': 0, 'status': 'pass',
        }) + '\n',
        encoding='utf-8',
    )
    analysis = FieldStudyCertificate.analyze(str(jsonl))
    assert analysis['overall_status'] == 'FAILED'
    assert 'b' in analysis['missing_phases']


def test_certificate_required_fields_present(tmp_path):
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'pass_run.jsonl'), release_version='1.3.0', reports_dir=str(tmp_path)
    )
    cert = json.loads(cert_path.read_text())
    for field in (
        'release_version', 'git_commit', 'timestamp', 'valid_until',
        'overall_status', 'phases', 'flat_at_session_end', 'realized_cost',
    ):
        assert field in cert
