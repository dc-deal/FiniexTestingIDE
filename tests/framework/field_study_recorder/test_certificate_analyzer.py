"""
FiniexTestingIDE - Field Study Certificate Analyzer Tests (#332)

Drives FieldStudyCertificate against synthetic JSONL fixtures: a clean run certifies
PASSED, a run with a failed phase + not-flat end certifies FAILED, and a run missing a
phase result (aborted) certifies FAILED. Beside the verdict, the informational figures each
say where they came from or that they are missing: the fees from the run's own report, whose
figure each fill's fee is (#508), the account's movement, the slippage per order type (#566),
the REST calls, the divergences.
"""

import json
from pathlib import Path
from typing import Optional

import pytest

from python.framework.constants.run_layout_constants import IO_SUBDIR
from python.framework.reporting.acceptance_study_recorder import AcceptanceStudyRecorder
from python.framework.reporting.acceptance_study_stream_projection import (
    AcceptanceStudyStreamProjection,
)
from python.framework.reporting.certificates.field_study_certificate import FieldStudyCertificate
from python.framework.reporting.io.artifact_specs import RUN_SUMMARY_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.types.acceptance_study_types.study_capture_types import CaptureLineType
from python.framework.types.acceptance_study_types.study_phase_types import PhaseOutcome
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
    BrokerTruthRecord,
    BrokerTruthSnapshot,
)
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.order_event_types import OrderEventPlane
from python.framework.types.trading_env_types.order_types import FeeReport

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
    assert cert['fees_charged']['value'] == pytest.approx(0.008)
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
        'overall_status', 'phases', 'flat_at_session_end', 'fees_charged', 'fee_source',
        'slippage',
    ):
        assert field in cert


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


def test_the_fees_come_from_the_run_s_own_report(tmp_path):
    """
    The figure is READ from the run's record beside the capture (#566).

    `fees_charged` is every fee the run booked, open positions included — the figure the run's
    report serves. The capture's own lines add up to the same bookings, so the report wins and
    the source says which was read.
    """
    run_dir = tmp_path / 'run'
    (run_dir / IO_SUBDIR).mkdir(parents=True)
    capture = run_dir / 'field_study.jsonl'
    capture.write_text((_FIXTURES / 'pass_run.jsonl').read_text(encoding='utf-8'), encoding='utf-8')
    write_artifact(RunSummary(run_id='r1', currencies=[RunSummaryCurrency(
        currency='USD', net_pnl=-0.01, profit_factor=None, win_rate=0.0,
        account_max_drawdown=0.01, total_fees=0.008, fees_charged=0.0081, total_trades=1,
        winning_trades=0, losing_trades=1, expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0,
        r_trade_count=0)]),
        run_dir / IO_SUBDIR, RUN_SUMMARY_ARTIFACT)

    analysis = FieldStudyCertificate.analyze(str(capture))

    assert analysis['fees_charged'] == {'value': pytest.approx(0.0081), 'currency': 'USD',
                                        'source': 'run_record'}


def test_without_the_report_the_capture_s_fill_fees_are_added_up(tmp_path):
    """Every fill line carries its fee since the projection, a full close included."""
    analysis = FieldStudyCertificate.analyze(str(_FIXTURES / 'pass_run.jsonl'))

    assert analysis['fees_charged'] == {'value': pytest.approx(0.008), 'currency': 'USD',
                                        'source': 'event_sum'}


def test_a_capture_without_a_fee_says_so(tmp_path):
    """No report and no fee on any line: not available, never a zero."""
    analysis = _run_with_snapshots(tmp_path, _snapshot(2, 'session_end', {'ZUSD': 1.0}))

    assert analysis['fees_charged'] == {'value': None, 'currency': None,
                                        'source': 'not_available'}


def _fill_line(seq: int, order_type: str, side: str, price: float, reference) -> str:
    """
    One projected fill line with its slippage sources.

    Args:
        seq: Sequence number
        order_type: 'market', 'limit' or 'stop'
        side: 'buy' or 'sell'
        price: The fill
        reference: What it is measured against, or None when it was not captured

    Returns:
        The JSONL line, newline-terminated
    """
    source = {'order_type': order_type, 'side': side, 'measured_against': 'x'}
    if reference is not None:
        source['reference_price'] = reference
    return json.dumps({
        'ts_utc': 'x', 'seq': seq, 'plane': 'bot', 'event_type': 'order_filled',
        'phase': 'a', 'phase_index': 0, 'price': price, 'slippage': source,
        'extra': {'action': 'open', 'order_type': order_type, 'fee': 0.001},
    }) + '\n'


class TestTheSlippageBlock:
    """Each fill against what it is measured by; positive is adverse; missing is said."""

    def test_market_fills_against_their_submission_mid(self, tmp_path):
        analysis = _run_with_snapshots(
            tmp_path,
            _fill_line(2, 'market', 'buy', 2001.0, 2000.0),
            _fill_line(3, 'market', 'sell', 1999.5, 2000.0))

        market = analysis['slippage']['by_order_type']['market']
        assert (market['legs'], market['measured']) == (2, 2)
        assert market['adverse_max_pct'] == pytest.approx(0.05)
        assert market['adverse_avg_pct'] == pytest.approx(0.0375)
        assert analysis['slippage']['status'] == 'measured'

    def test_a_limit_against_its_own_limit_never_the_mid(self, tmp_path):
        analysis = _run_with_snapshots(tmp_path, _fill_line(2, 'limit', 'buy', 1990.0, 1990.0))

        assert analysis['slippage']['by_order_type']['limit']['vs_limit_max_pct'] == 0.0

    def test_a_fill_without_its_reference_is_a_leg_not_a_measurement(self, tmp_path):
        analysis = _run_with_snapshots(
            tmp_path,
            _fill_line(2, 'market', 'buy', 2001.0, 2000.0),
            _fill_line(3, 'market', 'buy', 2001.0, None))

        market = analysis['slippage']['by_order_type']['market']
        assert (market['legs'], market['measured']) == (2, 1)
        assert analysis['slippage']['status'] == 'partly_measured'

    def test_a_capture_from_before_the_projection_is_not_measured(self, tmp_path):
        """No source on any line: `not_measured`, never the 0.0 every old certificate printed."""
        analysis = _run_with_snapshots(tmp_path, json.dumps({
            'ts_utc': 'x', 'seq': 2, 'plane': 'bot', 'event_type': 'order_filled',
            'phase': 'a', 'phase_index': 0, 'price': 2000.0, 'extra': {'commission': 0.004},
        }) + '\n')

        assert analysis['slippage']['status'] == 'not_measured'
        assert analysis['slippage']['by_order_type'] == {}


class TestTheRestCalls:

    def test_the_telemetry_written_at_the_end_is_summed(self):
        analysis = FieldStudyCertificate.analyze(str(_FIXTURES / 'pass_run.jsonl'))

        assert analysis['api_calls'] == {'status': 'recorded', 'calls': 2, 'errors': 0,
                                         'slow_calls': 0}

    def test_a_capture_without_it_says_not_recorded(self, tmp_path):
        analysis = _run_with_snapshots(tmp_path)

        assert analysis['api_calls'] == {'status': 'not_recorded'}


def _reconcile_line(seq: int, **summary) -> str:
    """One `reconcile_summary` line, as the session writes it at its end."""
    return json.dumps({
        'ts_utc': 'x', 'seq': seq, 'plane': 'bot', 'event_type': 'reconcile_summary',
        'phase': 'session_end', 'phase_index': -1, 'reconcile': summary,
    }) + '\n'


def _alert(seq: int, status: str) -> str:
    """One `reconcile_alert` line — written whenever the reconciliation picture changed."""
    return json.dumps({'ts_utc': 'x', 'seq': seq, 'plane': 'broker_truth',
                       'event_type': 'reconcile_alert', 'phase': 'a', 'phase_index': 0,
                       'status': status}) + '\n'


class TestTheReconciliation:
    """
    Whether the session compared, and what it found — never a bare zero (#566).

    A capture with no divergent line could be a clean session, one whose every cycle was skipped,
    or one that never reconciled; the session's own totals at its end tell them apart.
    """

    def test_a_clean_session(self, tmp_path):
        analysis = _run_with_snapshots(
            tmp_path, _reconcile_line(2, enabled=True, cycles=6, skipped=0, divergences_seen=0))

        assert analysis['reconciliation'] == {'status': 'checked', 'cycles': 6, 'skipped': 0,
                                              'divergences_seen': 0, 'divergent_records': 0}

    def test_a_divergence_and_its_end_count_once(self, tmp_path):
        analysis = _run_with_snapshots(
            tmp_path, _alert(2, 'divergent'), _alert(3, 'clean'),
            _reconcile_line(4, enabled=True, cycles=6, skipped=0, divergences_seen=3))

        assert analysis['reconciliation']['divergent_records'] == 1
        assert analysis['reconciliation']['divergences_seen'] == 3

    @pytest.mark.parametrize('cycles, skipped, status', [
        (6, 2, 'partly_skipped'), (6, 6, 'skipped'), (0, 0, 'not_run')])
    def test_a_session_that_did_not_fully_compare_says_so(self, tmp_path, cycles, skipped, status):
        analysis = _run_with_snapshots(tmp_path, _reconcile_line(
            2, enabled=True, cycles=cycles, skipped=skipped, divergences_seen=0))

        assert analysis['reconciliation']['status'] == status

    def test_reconciliation_switched_off(self, tmp_path):
        analysis = _run_with_snapshots(tmp_path, _reconcile_line(2, enabled=False))

        assert analysis['reconciliation'] == {'status': 'disabled'}

    def test_a_capture_without_the_totals_is_not_recorded(self, tmp_path):
        analysis = _run_with_snapshots(tmp_path, _alert(2, 'divergent'))

        assert analysis['reconciliation'] == {'status': 'not_recorded', 'divergent_records': 1}


class TestTheGateWithoutAMeasurement:
    """The flat gate passes only on a counted, empty order book."""

    def test_an_unread_order_book_at_the_session_end_does_not_pass(self, tmp_path):
        """Built through the projection, so the producer and the reader share one shape."""
        recorder = AcceptanceStudyRecorder(str(tmp_path / 'field_study.jsonl'), 'p', 'ETHUSD',
                                           ['a'], _QuietLogger(), 'Field Study',
                                           'generate the acceptance certificate')
        projection = AcceptanceStudyStreamProjection(recorder, 'p')
        projection.write_broker_truth(BrokerTruthRecord(
            seq=1, read_reason=BrokerTruthReadReason.SESSION_START,
            snapshot=BrokerTruthSnapshot(venue_orders=[], venue_balances={'ZUSD': 10.0})))
        recorder.record_phase_start('a', 0, None)
        recorder.write_projected(OrderEventPlane.BOT, CaptureLineType.PHASE_RESULT, 'a', 0,
                                 status=PhaseOutcome.PASS)
        projection.write_broker_truth(BrokerTruthRecord(
            seq=2, read_reason=BrokerTruthReadReason.SESSION_END,
            snapshot=BrokerTruthSnapshot(unread_parts=[BrokerTruthPart.VENUE_ORDERS,
                                                       BrokerTruthPart.VENUE_BALANCES])))
        recorder.close()

        analysis = FieldStudyCertificate.analyze(str(recorder.get_path()))

        assert analysis['flat_at_session_end'] is False
        assert analysis['overall_status'] == 'FAILED'
        assert analysis['account_delta']['status'] == 'balances_unreadable'


class _QuietLogger:
    """The recorder's banner and its error report go nowhere."""

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class TestTheRunReport:

    def test_a_report_that_no_longer_reads_falls_back_to_the_capture(self, tmp_path):
        """Another version of the report's model must not crash the certificate."""
        run_dir = tmp_path / 'run'
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        capture = run_dir / 'field_study.jsonl'
        capture.write_text((_FIXTURES / 'pass_run.jsonl').read_text(encoding='utf-8'),
                           encoding='utf-8')
        (run_dir / IO_SUBDIR / RUN_SUMMARY_ARTIFACT.filename).write_text(
            '{"run_id": "r1", "currencies": [{"currency": "USD"}]}', encoding='utf-8')

        analysis = FieldStudyCertificate.analyze(str(capture))

        assert analysis['fees_charged']['source'] == 'event_sum'


class TestWhatTheSlippageIsMeasuredAgainst:

    def test_it_is_read_from_the_lines(self, tmp_path):
        analysis = _run_with_snapshots(
            tmp_path, json.dumps({
                'ts_utc': 'x', 'seq': 2, 'plane': 'bot', 'event_type': 'order_filled',
                'phase': 'a', 'phase_index': 0, 'price': 2001.0,
                'slippage': {'order_type': 'iceberg', 'side': 'buy',
                             'measured_against': 'submission_mid', 'reference_price': 2000.0},
            }) + '\n')

        assert analysis['slippage']['measured_against'] == {'iceberg': 'submission_mid'}
        assert analysis['slippage']['by_order_type']['iceberg']['measured'] == 1


def _fee_fill(seq: int, fee_source: Optional[FeeSource]) -> str:
    """
    One fill line with its fee, and whose figure that fee is where one is given.

    Args:
        seq: Sequence number
        fee_source: The fee's source; None writes a line from before the source was recorded

    Returns:
        The JSONL line, newline-terminated
    """
    extra = {'action': 'open', 'order_type': 'market', 'fee': 0.004, 'fee_currency': 'USD'}
    if fee_source is not None:
        extra['fee_source'] = fee_source.value
    return json.dumps({
        'ts_utc': 'x', 'seq': seq, 'plane': 'bot', 'event_type': 'order_filled',
        'phase': 'a', 'phase_index': 0, 'price': 2000.0, 'extra': extra,
    }) + '\n'


def _passing_capture(tmp_path, fee_report: Optional[FeeReport], *fill_lines,
                     venue_fee_expected: Optional[bool] = None) -> Path:
    """
    A capture that passes every gate — one passing phase, an empty book at both ends — around
    some fills.

    Args:
        tmp_path: pytest temporary directory
        fee_report: What the header records about where the venue reports a fill's fee; None
            writes a header from before it was recorded
        fill_lines: Pre-rendered fill lines
        venue_fee_expected: What the header records the executor expected of a fill's fee;
            None writes a header from before it was recorded

    Returns:
        The capture's path
    """
    header = {'record_kind': 'header', 'schema_version': '2.0', 'started_utc': 'x',
              'profile': 'p', 'symbol': 'ETHUSD', 'phases': ['a']}
    if fee_report is not None:
        header['fee_report'] = fee_report.value
    if venue_fee_expected is not None:
        header['venue_fee_expected'] = venue_fee_expected
    capture = tmp_path / 'field_study.jsonl'
    capture.write_text(
        json.dumps(header) + '\n'
        + _snapshot(1, 'preflight', {'ZUSD': 100.0})
        + ''.join(fill_lines)
        + json.dumps({'ts_utc': 'x', 'seq': 90, 'plane': 'bot', 'event_type': 'phase_result',
                      'phase': 'a', 'phase_index': 0, 'status': 'pass'}) + '\n'
        + _snapshot(91, 'session_end', {'ZUSD': 99.99}),
        encoding='utf-8')
    return capture


class TestTheFeeSource:
    """
    Whose figure each fill's fee is — the venue's own, or our fee model's estimate (#508).

    Counted per fill. An estimate where the session's executor expected the venue's fee is a
    warning, never a failure, and a capture from before the source was recorded says so rather
    than passing for the venue's figures.
    """

    def test_a_run_whose_every_fee_is_the_venue_s(self, tmp_path, capsys):
        cert_path = FieldStudyCertificate.generate(
            str(_FIXTURES / 'pass_run.jsonl'), release_version='dev', reports_dir=str(tmp_path))
        cert = json.loads(cert_path.read_text())

        assert cert['fee_source'] == {
            'status': 'recorded', 'fills': 2,
            'by_source': {FeeSource.VENUE.value: 2, FeeSource.ESTIMATE.value: 0},
            'not_recorded': 0, 'fee_report': FeeReport.STATUS_ANSWER.value,
            'venue_fee_expected': True}
        assert cert['warnings'] == []
        assert 'USD (event_sum) · venue 2/2 fills' in capsys.readouterr().out

    def test_an_estimate_where_the_venue_states_its_fee_is_a_warning_never_a_failure(
            self, tmp_path, capsys):
        capture = _passing_capture(tmp_path, FeeReport.STATUS_ANSWER,
                                   _fee_fill(2, FeeSource.VENUE), _fee_fill(3, FeeSource.ESTIMATE),
                                   venue_fee_expected=True)

        cert_path = FieldStudyCertificate.generate(
            str(capture), release_version='dev', reports_dir=str(tmp_path / 'reports'))
        cert = json.loads(cert_path.read_text())

        assert cert['overall_status'] == 'PASSED'
        assert cert['fee_source']['by_source'] == {FeeSource.VENUE.value: 1,
                                                   FeeSource.ESTIMATE.value: 1}
        assert len(cert['warnings']) == 1
        assert cert['warnings'][0].startswith('ESTIMATED FEES: 1 of 2 fills')
        printed = capsys.readouterr().out
        assert 'venue 1/2 fills' in printed
        assert '⚠️  ESTIMATED FEES: 1 of 2 fills' in printed

    @pytest.mark.parametrize(
        'fee_report', [report for report in FeeReport if report is not FeeReport.STATUS_ANSWER],
        ids=lambda report: report.value)
    def test_an_estimate_where_the_venue_states_none_there_is_by_design(self, tmp_path,
                                                                        fee_report):
        analysis = FieldStudyCertificate.analyze(str(_passing_capture(
            tmp_path, fee_report, _fee_fill(2, FeeSource.ESTIMATE), venue_fee_expected=False)))

        assert analysis['fee_source']['by_source'][FeeSource.ESTIMATE.value] == 1
        assert analysis['warnings'] == []

    def test_an_estimate_in_a_dry_run_is_by_design(self, tmp_path):
        """The venue declares the fee in its answer; a dry run's answers are our simulator's."""
        analysis = FieldStudyCertificate.analyze(str(_passing_capture(
            tmp_path, FeeReport.STATUS_ANSWER, _fee_fill(2, FeeSource.ESTIMATE),
            venue_fee_expected=False)))

        assert analysis['fee_source']['venue_fee_expected'] is False
        assert analysis['warnings'] == []

    @pytest.mark.parametrize('fee_report', [FeeReport.STATUS_ANSWER, None],
                             ids=['declared', 'undeclared'])
    def test_an_estimate_without_the_executor_s_expectation_is_not_judged(self, tmp_path,
                                                                          fee_report):
        """A capture from before the expectation was stamped: the declaration decides nothing."""
        analysis = FieldStudyCertificate.analyze(str(_passing_capture(
            tmp_path, fee_report, _fee_fill(2, FeeSource.ESTIMATE))))

        assert analysis['fee_source']['venue_fee_expected'] is None
        assert analysis['warnings'] == []

    def test_a_capture_from_before_the_source_was_recorded(self, tmp_path, capsys):
        """No source on any fill, no declaration in the header: still PASSED, and said so."""
        lines = []
        for raw in (_FIXTURES / 'pass_run.jsonl').read_text(encoding='utf-8').splitlines():
            record = json.loads(raw)
            record.pop('fee_report', None)
            record.pop('venue_fee_expected', None)
            (record.get('extra') or {}).pop('fee_source', None)
            lines.append(json.dumps(record))
        capture = tmp_path / 'field_study.jsonl'
        capture.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        cert = json.loads(FieldStudyCertificate.generate(
            str(capture), release_version='dev',
            reports_dir=str(tmp_path / 'reports')).read_text())

        assert cert['overall_status'] == 'PASSED'
        assert cert['fee_source'] == {
            'status': 'not_recorded', 'fills': 2,
            'by_source': {FeeSource.VENUE.value: 0, FeeSource.ESTIMATE.value: 0},
            'not_recorded': 2, 'fee_report': None, 'venue_fee_expected': None}
        assert cert['warnings'] == []
        assert 'USD (event_sum) · source not recorded' in capsys.readouterr().out

    def test_a_fill_without_its_source_beside_recorded_ones(self, tmp_path):
        analysis = FieldStudyCertificate.analyze(str(_passing_capture(
            tmp_path, FeeReport.STATUS_ANSWER, _fee_fill(2, FeeSource.VENUE), _fee_fill(3, None))))

        block = analysis['fee_source']
        assert (block['status'], block['fills'], block['not_recorded']) == (
            'partly_recorded', 2, 1)

    def test_a_capture_without_a_fill(self, tmp_path):
        analysis = FieldStudyCertificate.analyze(str(_passing_capture(
            tmp_path, FeeReport.STATUS_ANSWER)))

        assert (analysis['fee_source']['status'], analysis['fee_source']['fills']) == (
            'no_fills', 0)
        assert analysis['warnings'] == []

    def test_an_identity_warning_fails_it_and_stands_before_the_fee_warning(self, tmp_path):
        """A release declared from a tree that says otherwise fails; the fee warning still shows."""
        capture = _passing_capture(tmp_path, FeeReport.STATUS_ANSWER,
                                   _fee_fill(2, FeeSource.ESTIMATE), venue_fee_expected=True)

        cert = json.loads(FieldStudyCertificate.generate(
            str(capture), release_version='0.0.1',
            reports_dir=str(tmp_path / 'reports')).read_text())

        assert cert['overall_status'] == 'FAILED'
        assert cert['warnings'][0].startswith('VERSION MISMATCH')
        assert cert['warnings'][-1].startswith('ESTIMATED FEES: 1 of 1 fills')
