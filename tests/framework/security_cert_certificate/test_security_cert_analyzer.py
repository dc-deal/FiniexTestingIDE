"""
FiniexTestingIDE - Security Study Certificate Analyzer Tests (#358)

The certificate judges five stage runs together. A passing study passes, and each way a study
can fail fails exactly the check that names it: a missing or out-of-order stage, two commits,
uncommitted code, simulated orders, a phase verdict the order stream contradicts, a breaker record
that does not say what the stage was there to provoke, a restart that measured against another
baseline, and anything left resting at the venue.
"""

import json
from datetime import datetime, timedelta
from typing import Dict

from python.framework.reporting.certificates.security_cert_certificate import (
    SecurityCertCertificate,
)
from python.framework.types.acceptance_study_types.security_cert_types import (
    RuleCertification,
    SecurityStageId,
)
from python.framework.types.autotrader_types.safety_session_types import FlattenOutcome
from python.framework.types.persistence_types import BaselineOrigin
from python.framework.types.run_kind_types import OrdersTo
from python.framework.types.run_origin_types import CodeIdentity, RepositoryState
from tests.framework.security_cert_certificate.conftest import StageSpec

_S = SecurityStageId


def _failed(study: Dict[SecurityStageId, StageSpec], build) -> set:
    """The checks a study fails."""
    return set(SecurityCertCertificate.analyze(build(study))['failed_checks'])


class TestAPassingStudy:

    def test_it_passes_and_every_live_rule_holds(self, passing_study, build):
        analysis = SecurityCertCertificate.analyze(build(passing_study))
        assert analysis['overall_status'] == 'PASSED', analysis['failed_checks']
        live = [r for r in analysis['rules'] if r['certification'] == RuleCertification.LIVE]
        assert live and all(r['status'] == 'passed' for r in live)

    def test_a_rule_not_provoked_live_says_how_it_is_answered(self, passing_study, build):
        rows = SecurityCertCertificate.analyze(build(passing_study))['rules']
        others = [r for r in rows if r['certification'] != RuleCertification.LIVE]
        assert others and all(r['status'] in ('offline', 'margin') for r in others)


class TestTheStudyAsAWhole:

    def test_a_missing_stage_fails(self, passing_study, build):
        del passing_study[_S.RESTART]
        assert 'study:all_stages' in _failed(passing_study, build)

    def test_a_stage_that_started_before_the_one_before_ended_fails(self, passing_study, build):
        passing_study[_S.SOFT].started = '2026-10-14T10:10:00+00:00'  # inside the guards' hour
        assert 'study:in_order' in _failed(passing_study, build)

    def test_two_commits_fail(self, passing_study, build):
        header = passing_study[_S.HARD_KEPT].header
        passing_study[_S.HARD_KEPT].header = header.model_copy(update={'git_commit': 'def5678'})
        assert 'study:one_commit' in _failed(passing_study, build)

    def test_uncommitted_code_fails(self, passing_study, build):
        header = passing_study[_S.SOFT].header
        dirty = CodeIdentity(framework=RepositoryState(root='.', commit='abc1234', dirty=True))
        passing_study[_S.SOFT].header = header.model_copy(update={'code_identity': dirty})
        assert 'study:one_commit' in _failed(passing_study, build)

    def test_simulated_orders_fail(self, passing_study, build):
        header = passing_study[_S.GUARDS].header
        passing_study[_S.GUARDS].header = header.model_copy(
            update={'orders_to': OrdersTo.SIMULATED})
        assert 'study:real_orders' in _failed(passing_study, build)

    def test_an_order_left_resting_at_the_end_fails(self, passing_study, build):
        end = next(line for line in passing_study[_S.HARD_RESTART].lines
                   if line['event_type'] == 'broker_snapshot' and line['phase'] == 'session_end')
        end['extra']['order_count'] = 1
        assert 'study:flat_at_end' in _failed(passing_study, build)


class TestAPhaseVerdictIsConfirmedByTheStream:

    def test_a_denial_with_another_reason_in_the_stream_fails(self, passing_study, build):
        line = next(ln for ln in passing_study[_S.GUARDS].lines
                    if ln['event_type'] == 'order_rejected' and ln['phase'] == 'g_cooled_buy')
        line['extra']['reason'] = 'insufficient_funds'
        assert 'phase:g_cooled_buy' in _failed(passing_study, build)

    def test_a_suppressed_submit_that_reached_the_venue_fails(self, passing_study, build):
        lines = passing_study[_S.SOFT].lines
        index = next(i for i, ln in enumerate(lines)
                     if ln['event_type'] == 'phase_result' and ln['phase'] == 's_blocked_buy')
        lines.insert(index, {'event_type': 'order_filled', 'phase': 's_blocked_buy',
                             'plane': 'bot', 'status': 'filled', 'extra': {'action': 'open'}})
        assert 'phase:s_blocked_buy' in _failed(passing_study, build)

    def test_a_failed_phase_fails(self, passing_study, build):
        line = next(ln for ln in passing_study[_S.GUARDS].lines
                    if ln['event_type'] == 'phase_result' and ln['phase'] == 'g_rearmed')
        line['status'] = 'fail'
        assert 'phase:g_rearmed' in _failed(passing_study, build)

    def test_a_hard_stop_that_never_came_fails_its_wait(self, passing_study, build):
        line = next(ln for ln in passing_study[_S.HARD_KEPT].lines
                    if ln['event_type'] == 'phase_result' and ln['phase'] == 'h_await_stop')
        line.update(status='fail', extra={**line['extra'],
                                          'reason': 'the session was not ended by the safety layer'})
        assert 'phase:h_await_stop' in _failed(passing_study, build)

    def test_a_wait_the_breaker_does_not_confirm_fails(self, passing_study, build):
        # The phase passes on an EMERGENCY end; only the breaker's record says it was its stop.
        safety = passing_study[_S.HARD_KEPT].safety
        passing_study[_S.HARD_KEPT].safety = safety.model_copy(
            update={'flatten_fired': False, 'flatten_outcome': None, 'flatten_kept': []})
        assert 'phase:h_await_stop' in _failed(passing_study, build)

    def test_a_wait_with_no_result_is_not_reached(self, passing_study, build):
        lines = passing_study[_S.HARD_RESTART].lines
        passing_study[_S.HARD_RESTART].lines = [
            ln for ln in lines
            if not (ln['event_type'] == 'phase_result' and ln['phase'] == 'hr_await_stop')]
        assert 'phase:hr_await_stop' in _failed(passing_study, build)


class TestTheBreakersOwnRecord:

    def test_a_soft_block_that_does_not_name_the_daily_limits_fails(self, passing_study, build):
        safety = passing_study[_S.SOFT].safety
        passing_study[_S.SOFT].safety = safety.model_copy(
            update={'reason_at_end': 'max_drawdown_pct (0.3% > 0.0%) + max_drawdown_abs (0.07 > 0.00)'})
        assert 'safety:soft_reasons' in _failed(passing_study, build)

    def test_a_restart_that_measured_against_another_baseline_fails(self, passing_study, build):
        safety = passing_study[_S.RESTART].safety
        moved = safety.baseline.model_copy(update={'taken_at_utc': '2026-10-14T12:00:01+00:00'})
        passing_study[_S.RESTART].safety = safety.model_copy(update={'baseline': moved})
        assert 'safety:restart_baseline_restored' in _failed(passing_study, build)

    def test_a_restart_whose_daily_limits_fired_fails(self, passing_study, build):
        safety = passing_study[_S.RESTART].safety
        passing_study[_S.RESTART].safety = safety.model_copy(
            update={'reason_at_end': safety.reason_at_end + ' + max_daily_loss_abs (0.07 > 0.00)'})
        assert 'safety:restart_reasons' in _failed(passing_study, build)

    def test_a_hard_stop_that_sold_where_it_should_keep_fails(self, passing_study, build):
        safety = passing_study[_S.HARD_KEPT].safety
        passing_study[_S.HARD_KEPT].safety = safety.model_copy(
            update={'flatten_outcome': FlattenOutcome.COMPLETE, 'flatten_kept': []})
        assert 'safety:hard_kept' in _failed(passing_study, build)

    def test_a_restart_after_the_hard_stop_on_a_fresh_baseline_fails(self, passing_study, build):
        safety = passing_study[_S.HARD_RESTART].safety
        fresh = safety.baseline.model_copy(update={'origin': BaselineOrigin.FIRST_TICK,
                                                   'taken_at_utc': '2026-10-14T14:00:01+00:00'})
        passing_study[_S.HARD_RESTART].safety = safety.model_copy(
            update={'baseline': fresh, 'baseline_restored': False})
        assert 'safety:hard_restart_fired' in _failed(passing_study, build)

    def test_a_hard_stop_that_sold_nothing_fails(self, passing_study, build):
        lines = passing_study[_S.HARD_RESTART].lines
        passing_study[_S.HARD_RESTART].lines = [
            ln for ln in lines
            if not (ln['event_type'] == 'order_filled'
                    and ln.get('extra', {}).get('action') == 'close')]
        assert 'safety:hard_restart_complete' in _failed(passing_study, build)

    def test_a_missing_safety_record_fails_the_stage_s_rules(self, passing_study, build):
        passing_study[_S.SOFT].safety = None
        analysis = SecurityCertCertificate.analyze(build(passing_study))
        statuses = {r['rule']: r['status'] for r in analysis['rules']}
        assert analysis['overall_status'] == 'FAILED'
        assert statuses['soft_block_on_the_account_value'] == 'not_measured'


class TestOneStageAtATime:
    """What the study runner asks after each stage, before the next one may cost money."""

    def test_a_passing_stage_has_no_failures(self, passing_study, build):
        captures = build(passing_study)
        assert SecurityCertCertificate.stage_failures(_S.GUARDS, captures[_S.GUARDS]) == []

    def test_a_failed_phase_is_named_with_its_reason(self, passing_study, build):
        line = next(ln for ln in passing_study[_S.SOFT].lines
                    if ln['event_type'] == 'phase_result' and ln['phase'] == 's_start_flat')
        line['status'] = 'fail'
        line['extra']['reason'] = 'the account holds 0.00020000 BTC'
        captures = build(passing_study)
        failures = SecurityCertCertificate.stage_failures(_S.SOFT, captures[_S.SOFT])
        assert failures == ['phase:s_start_flat: fail: the account holds 0.00020000 BTC']

    def test_a_capture_cut_at_its_end_fails_the_stage(self, passing_study, build):
        """The session died while writing its end: the runner must not spend on the next stage."""
        captures = build(passing_study)
        path = captures[_S.SOFT]
        text = path.read_text(encoding='utf-8').rstrip('\n')
        path.write_text(text[:-5], encoding='utf-8')

        failures = SecurityCertCertificate.stage_failures(_S.SOFT, path)

        assert failures == [
            'capture: its last line was cut off — the session stopped while writing it',
            'capture: the session did not close it — it stopped before its end']

    def test_a_capture_the_session_never_closed_fails_the_stage(self, passing_study, build):
        passing_study[_S.SOFT].lines[:] = [
            line for line in passing_study[_S.SOFT].lines if line['event_type'] != 'session_end']
        captures = build(passing_study)

        failures = SecurityCertCertificate.stage_failures(_S.SOFT, captures[_S.SOFT])

        assert failures == ['capture: the session did not close it — it stopped before its end']


class TestTheCertificateIsBoundToItsStages:
    """The certificate names every stage run it rests on and counts its validity from the first."""

    def test_every_stage_is_evidence_and_the_validity_counts_from_the_first(
            self, passing_study, build, tmp_path):
        cert_path = SecurityCertCertificate.generate(
            build(passing_study), release_version='dev', reports_dir=str(tmp_path / 'reports'))
        cert = json.loads(cert_path.read_text(encoding='utf-8'))

        assert [run['run_id'] for run in cert['evidence_runs']] == [
            f'run_{stage.value}' for stage in _S]
        first = min(datetime.fromisoformat(run['started']) for run in cert['evidence_runs'])
        assert datetime.fromisoformat(cert['valid_until']) == first + timedelta(days=90)
