"""
FiniexTestingIDE - Field Study Certificate Analyzer Tests (#332)

Drives FieldStudyCertificate against synthetic JSONL fixtures: a clean run certifies
PASSED, a run with a failed phase + not-flat end certifies FAILED, and a run missing a
phase result (aborted) certifies FAILED.
"""

import json
from pathlib import Path

import pytest

from python.framework.reporting.certificates.field_study_certificate import FieldStudyCertificate

_FIXTURES = Path('tests/fixtures/field_study')


def test_pass_run_certifies_passed(tmp_path):
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'pass_run.jsonl'), release_version='dev', reports_dir=str(tmp_path)
    )
    cert = json.loads(cert_path.read_text())
    assert cert['overall_status'] == 'PASSED'
    assert cert['flat_at_session_end'] is True
    assert cert['failed_phases'] == []
    assert cert['missing_phases'] == []
    assert cert['realized_cost'] == pytest.approx(0.008)
    assert cert['record_kind'] == 'certificate'


def test_fail_run_certifies_failed(tmp_path):
    """
    The analyzer's own verdict, not an identity guard's.

    'dev' marks a rehearsal and is exempt from the version / dirty-tree guards, so a FAILED
    here can only come from the run analysis. With a declared release the certificate would
    fail on identity too, and this test would pass while proving nothing.
    """
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'fail_run.jsonl'), release_version='dev', reports_dir=str(tmp_path)
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


def test_realized_cost_comes_from_the_run_s_own_stamp(tmp_path):
    """
    The total is READ, not reconstructed (#506).

    Summing the per-event `commission` values can only see legs that emit an event, and a
    FULL close emits none — `_notify_outcome` fires for opens only. The run therefore stamps
    its own figure, derived from the order history, and the certificate reads that. The
    fixture's own history is one round trip at 0.004 per leg.
    """
    cert_path = FieldStudyCertificate.generate(
        str(_FIXTURES / 'pass_run.jsonl'), release_version='dev', reports_dir=str(tmp_path)
    )
    cert = json.loads(cert_path.read_text())

    assert cert['realized_cost'] == pytest.approx(0.008)
    assert cert['realized_cost_source'] == 'portfolio_cost_breakdown'


def test_a_run_without_the_stamp_says_its_figure_is_partial(tmp_path):
    """
    An older capture has no stamp, so the fallback runs — and must NOT look authoritative.

    That is the failure this field already had: four committed certificates recorded
    `realized_cost: 0` and nothing in the artifact said the number was a reconstruction.
    """
    jsonl = tmp_path / 'no_stamp.jsonl'
    jsonl.write_text(
        json.dumps({
            'record_kind': 'header', 'schema_version': '1.0', 'started_utc': 'x',
            'profile': 'p', 'symbol': 'ETHUSD', 'release_target': 'dev', 'phases': ['a'],
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 1, 'plane': 'bot', 'event_type': 'order_filled',
            'phase': 'a', 'phase_index': 0, 'extra': {'commission': 0.004},
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 2, 'plane': 'bot', 'event_type': 'phase_result',
            'phase': 'a', 'phase_index': 0, 'status': 'pass',
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 3, 'plane': 'broker_truth',
            'event_type': 'broker_snapshot', 'phase': 'session_end', 'phase_index': -1,
            'extra': {'order_count': 0, 'balances': {}},
        }) + '\n',
        encoding='utf-8',
    )

    analysis = FieldStudyCertificate.analyze(str(jsonl))

    assert analysis['realized_cost'] == pytest.approx(0.004)
    assert analysis['realized_cost_source'] == 'event_sum_partial'


def test_a_missing_session_end_snapshot_does_not_pass_the_flat_gate(tmp_path):
    """
    The end snapshot is selected by PHASE, never by position.

    `broker_snapshots[-1]` used to be "the last snapshot of any kind", so a session-end
    snapshot that never got written — every exception in the shutdown path does that —
    silently promoted the PREFLIGHT one, and a release gate then answered from the account
    state BEFORE the run. Here only a preflight snapshot exists, and it says flat.
    """
    jsonl = tmp_path / 'no_end_snapshot.jsonl'
    jsonl.write_text(
        json.dumps({
            'record_kind': 'header', 'schema_version': '1.0', 'started_utc': 'x',
            'profile': 'p', 'symbol': 'ETHUSD', 'release_target': 'dev', 'phases': ['a'],
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 1, 'plane': 'broker_truth',
            'event_type': 'broker_snapshot', 'phase': 'preflight', 'phase_index': -1,
            'extra': {'order_count': 0, 'balances': {}},
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 2, 'plane': 'bot', 'event_type': 'phase_result',
            'phase': 'a', 'phase_index': 0, 'status': 'pass',
        }) + '\n',
        encoding='utf-8',
    )

    analysis = FieldStudyCertificate.analyze(str(jsonl))

    assert analysis['flat_at_session_end'] is False
    assert analysis['overall_status'] == 'FAILED'


def _snapshot(seq: int, phase: str, balances, order_count: int = 0) -> str:
    """
    One broker-truth snapshot line.

    Args:
        seq: Sequence number
        phase: 'preflight' or 'session_end'
        balances: Asset → amount, or None when the venue could not be read
        order_count: Resting orders at that moment

    Returns:
        The JSONL line, newline-terminated
    """
    return json.dumps({
        'ts_utc': 'x', 'seq': seq, 'plane': 'broker_truth',
        'event_type': 'broker_snapshot', 'phase': phase, 'phase_index': -1,
        'extra': {'order_count': order_count, 'balances': balances},
    }) + '\n'


def _run_with_snapshots(tmp_path, *snapshot_lines) -> dict:
    """
    Analyze a synthetic run consisting of a header, one passing phase and some snapshots.

    Args:
        tmp_path: pytest temporary directory
        snapshot_lines: Pre-rendered snapshot JSONL lines

    Returns:
        The analysis dict
    """
    jsonl = tmp_path / 'delta.jsonl'
    jsonl.write_text(
        json.dumps({
            'record_kind': 'header', 'schema_version': '1.0', 'started_utc': 'x',
            'profile': 'p', 'symbol': 'ETHUSD', 'release_target': 'dev', 'phases': ['a'],
        }) + '\n' +
        json.dumps({
            'ts_utc': 'x', 'seq': 1, 'plane': 'bot', 'event_type': 'phase_result',
            'phase': 'a', 'phase_index': 0, 'status': 'pass',
        }) + '\n' +
        ''.join(snapshot_lines),
        encoding='utf-8',
    )
    return FieldStudyCertificate.analyze(str(jsonl))


def test_the_account_delta_is_the_venue_side_counterpart_to_the_booking(tmp_path):
    """
    The figure that can CONTRADICT our booking (#506).

    The quote currency is the whole point: both snapshots of both real runs of 2026-09-08
    listed the two base balances that had not moved and omitted the one that had, so nothing
    in the artifact could disagree with a realized cost of zero.
    """
    analysis = _run_with_snapshots(
        tmp_path,
        _snapshot(2, 'preflight', {'ZUSD': 32.1985, 'XETH': 0.01655583}),
        _snapshot(3, 'session_end', {'ZUSD': 31.8412, 'XETH': 0.01655583}),
    )

    delta = analysis['account_delta']
    assert delta['status'] == 'ok'
    assert delta['per_asset']['ZUSD'] == pytest.approx(-0.3573)
    assert 'XETH' not in delta['per_asset'], 'an unchanged balance is not a movement'


def test_an_asset_that_appeared_counts_as_a_movement_from_zero(tmp_path):
    analysis = _run_with_snapshots(
        tmp_path,
        _snapshot(2, 'preflight', {'ZUSD': 100.0}),
        _snapshot(3, 'session_end', {'ZUSD': 90.0, 'XETH': 0.004}),
    )

    delta = analysis['account_delta']
    assert delta['per_asset']['XETH'] == pytest.approx(0.004)
    assert delta['per_asset']['ZUSD'] == pytest.approx(-10.0)


def test_a_missing_end_snapshot_says_so_instead_of_reporting_no_movement(tmp_path):
    """A silent empty delta reads as 'nothing moved' — the failure realized_cost already had."""
    analysis = _run_with_snapshots(
        tmp_path, _snapshot(2, 'preflight', {'ZUSD': 100.0}))

    assert analysis['account_delta']['status'] == 'no_end_snapshot'
    assert analysis['account_delta']['per_asset'] == {}


def test_an_unreadable_balance_sheet_says_so(tmp_path):
    """
    `None` is what the ladder gives up with, and it must not be read as an empty account.
    """
    analysis = _run_with_snapshots(
        tmp_path,
        _snapshot(2, 'preflight', None),
        _snapshot(3, 'session_end', {'ZUSD': 90.0}),
    )

    assert analysis['account_delta']['status'] == 'balances_unreadable'
