"""
What a boot may do with the carry-over it just read (#358, #598).

The store writes by reading first, so whatever a boot ignores on read, its first write replaces.
These tests hold the five rules that stand between that and a session trading against a state
that is not its own: a declared continuation reads a carry-over at all, finds what it continues,
takes a partner's document only from a run that completed — nobody replaces another profile's
document while it still speaks for the venue — and a real-money start without a usable
carry-over goes ahead only when declared the bot's first.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pytest

from python.framework.exceptions.persistence_errors import (
    CarryOverContinuationError,
    CarryOverForeignClaimsError,
    CarryOverRequiredError,
    FirstStartContradictedError,
)
from python.framework.reporting.builders.booking_period_builder import (
    PeriodSnapshot,
    derive_booking_period,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.persistence_types import CarryOverLoad, CarryOverLoadOutcome
from python.framework.types.run_results_types import PeriodCloseReason, RunProvenance
from python.framework.acceptance_studies import security_cert_runner
from python.framework.acceptance_studies.security_cert_runner import starts_without_a_carry_over
from python.framework.validators.carry_over_continuity_validator import (
    validate_carry_over_continuity,
)

_PATH = 'data/runtime/cold_start_state/scsafe01_btcusd.json'


class _Ledger:
    """
    A ledger holding rows for the given runs, and recording which runs it was asked about.

    Args:
        completed: The run ids that reached their ledger row
    """

    def __init__(self, completed: Optional[List[str]] = None):
        self._completed = set(completed or ())
        self.asked: List[str] = []

    def read(self, run_id: Optional[str] = None) -> pd.DataFrame:
        self.asked.append(run_id)
        return pd.DataFrame({'run_id': [run_id] if run_id in self._completed else []})


def _load(outcome: CarryOverLoadOutcome, writer: Optional[str] = 'cert_soft',
          run: Optional[str] = 'run_soft', positions: int = 0, keys: int = 0) -> CarryOverLoad:
    """What a read found."""
    return CarryOverLoad(_PATH, outcome, writer, run, positions, keys)


def _check(load: Optional[CarryOverLoad], profile: str = 'cert_restart',
           carry_over_from: str = 'cert_soft', cold_start: bool = True,
           skipped_by: Optional[str] = None, ledger: Optional[_Ledger] = None,
           real_money: bool = False, first_start: bool = False) -> None:
    """Run the check the way the startup does."""
    validate_carry_over_continuity(profile, carry_over_from, cold_start, skipped_by, load,
                                   ledger if ledger is not None else _Ledger(['run_soft']),
                                   real_money=real_money, first_start=first_start)


class TestADeclaredContinuationReadsACarryOver:

    def test_a_disabled_cold_start_is_refused(self):
        with pytest.raises(CarryOverContinuationError, match='disables its cold start'):
            _check(None, cold_start=False)

    def test_a_study_that_skips_adoption_is_refused(self):
        with pytest.raises(CarryOverContinuationError, match='Field Study'):
            _check(_load(CarryOverLoadOutcome.ACCEPTED), skipped_by='Field Study')


class TestADeclaredContinuationFindsWhatItContinues:

    @pytest.mark.parametrize('outcome, said', [
        (CarryOverLoadOutcome.ABSENT, 'no carry-over document at all'),
        (CarryOverLoadOutcome.UNREADABLE, 'cannot be read'),
        (CarryOverLoadOutcome.OTHER_SCHEMA, 'another format'),
    ])
    def test_nothing_usable_is_refused_and_names_the_stage_to_run_first(self, outcome, said):
        with pytest.raises(CarryOverContinuationError) as raised:
            _check(_load(outcome, writer=None, run=None))
        message = str(raised.value)
        assert said in message and "Run 'cert_soft' first" in message and _PATH in message

    def test_a_document_another_profile_wrote_is_refused_and_named(self):
        # Even one that claims nothing: the later stage would start fresh, which is what the
        # declaration says it must not do.
        with pytest.raises(CarryOverContinuationError, match="last written by 'cert_copy'"):
            _check(_load(CarryOverLoadOutcome.FOREIGN, writer='cert_copy'))

    def test_its_own_document_from_an_earlier_run_is_accepted_without_asking_the_ledger(self):
        ledger = _Ledger()
        _check(_load(CarryOverLoadOutcome.ACCEPTED, writer='cert_restart'), ledger=ledger)
        assert ledger.asked == []


class TestAPartnersDocumentComesFromACompletedRun:

    def test_a_completed_run_is_accepted(self):
        _check(_load(CarryOverLoadOutcome.ACCEPTED))

    def test_a_run_with_no_ledger_row_is_refused_as_not_completed(self):
        # Still running, or ended without its close — they look the same, so neither is said.
        with pytest.raises(CarryOverContinuationError) as raised:
            _check(_load(CarryOverLoadOutcome.ACCEPTED), ledger=_Ledger())
        message = str(raised.value)
        assert 'run_soft, which has not completed' in message
        assert 'aborted' not in message and 'crashed' not in message

    def test_a_run_that_recorded_no_id_is_refused(self):
        with pytest.raises(CarryOverContinuationError, match='recorded no id'):
            _check(_load(CarryOverLoadOutcome.ACCEPTED, run=None))

    def test_the_earlier_stage_holds_the_later_one_to_it_too(self):
        # A rerun of the earlier stage while the later one may still be trading the same book.
        with pytest.raises(CarryOverContinuationError, match='not completed'):
            _check(_load(CarryOverLoadOutcome.ACCEPTED, writer='cert_restart', run='run_restart'),
                   profile='cert_soft', carry_over_from='', ledger=_Ledger())


class TestAnotherProfilesClaimsStopTheBoot:

    @pytest.mark.parametrize('positions, keys', [(1, 0), (0, 2), (1, 2)])
    def test_an_open_position_or_a_session_key_refuses(self, positions, keys):
        load = _load(CarryOverLoadOutcome.FOREIGN, writer='dot_old_name', run='run_old',
                     positions=positions, keys=keys)
        with pytest.raises(CarryOverForeignClaimsError) as raised:
            _check(load, profile='dot_new_name', carry_over_from='')
        message = str(raised.value)
        assert "profile 'dot_old_name' (run run_old)" in message
        assert f'{positions} open position(s) and {keys} session key(s)' in message
        assert "restore `profile_name: 'dot_old_name'`" in message

    def test_a_document_that_claims_nothing_may_be_started_over(self):
        # What a dry session writes — no key, no book; nothing at the venue depends on it.
        _check(_load(CarryOverLoadOutcome.FOREIGN, writer='dot_old_name'),
               profile='dot_new_name', carry_over_from='')

    def test_an_undeclared_profile_with_its_own_document_passes(self):
        _check(_load(CarryOverLoadOutcome.ACCEPTED, writer='dot_live', positions=1, keys=3),
               profile='dot_live', carry_over_from='')

    def test_nothing_read_is_nothing_to_check(self):
        _check(None, profile='dot_live', carry_over_from='')


def test_the_ledger_is_asked_by_run_id():
    """The completion rule is the run completion audit's: a run is complete once it has a row."""
    ledger = _Ledger(['run_soft'])
    _check(_load(CarryOverLoadOutcome.ACCEPTED), ledger=ledger)
    assert ledger.asked == ['run_soft']


def test_a_real_ledger_without_the_run_refuses(tmp_path):
    """The same answer from the ledger the startup builds, empty because nothing was booked."""
    with pytest.raises(CarryOverContinuationError, match='not completed'):
        validate_carry_over_continuity(
            'cert_restart', 'cert_soft', True, None, _load(CarryOverLoadOutcome.ACCEPTED),
            RunResultsLedger(tmp_path / 'ledger'))


def test_a_partner_whose_ledger_holds_only_sealed_days_refuses(tmp_path):
    """
    A session books each trading day as it seals (#476), so a partner still running — or one
    that died after a seal — has ledger rows. None of them is its close, and two sessions must
    not manage one book.
    """
    ledger = RunResultsLedger(tmp_path / 'ledger')
    monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
    ledger.write_sealed_periods(
        RunProvenance(
            param_hash='hash', status='ok', error=None, run_id='run_soft', run_timestamp=monday,
            scenario_set_name='cert_soft', app_version='1.4.0', git_commit='abc1234',
            git_branch='dev', git_dirty=False, decision_logic_type='CORE/aggressive_trend',
            decision_version='1.0.0', worker_versions={}, strategy_config_json='{}',
            symbols=['BTCUSD'], data_broker_type='kraken_spot'),
        [derive_booking_period('cert_soft', 1, monday, monday + timedelta(days=1),
                               PeriodCloseReason.ANCHOR, [], PeriodSnapshot(currency='USD'))])

    with pytest.raises(CarryOverContinuationError, match='not completed'):
        validate_carry_over_continuity(
            'cert_restart', 'cert_soft', True, None, _load(CarryOverLoadOutcome.ACCEPTED), ledger)


class TestARealMoneyStartNeedsACarryOverOrItsDeclaration:
    """#598: a start in the wrong data home, or a deploy into a tree that found nothing, is refused."""

    @staticmethod
    def _own(outcome: CarryOverLoadOutcome) -> CarryOverLoad:
        return CarryOverLoad(_PATH, outcome, 'mybot_profile', 'run_before', 1, 2,
                             saved_at_utc='2026-10-20T18:00:00+00:00')

    @pytest.mark.parametrize('outcome, said', [
        (CarryOverLoadOutcome.ABSENT, 'no carry-over document at all'),
        (CarryOverLoadOutcome.UNREADABLE, 'cannot be read'),
        (CarryOverLoadOutcome.OTHER_SCHEMA, 'in another format'),
        (CarryOverLoadOutcome.FOREIGN, 'written by another profile'),
    ])
    def test_nothing_usable_is_refused_unless_declared_a_first_start(self, outcome, said):
        load = CarryOverLoad(_PATH, outcome)
        with pytest.raises(CarryOverRequiredError, match=said) as refused:
            _check(load, profile='mybot_profile', carry_over_from='', real_money=True)
        assert '--first-start' in str(refused.value) and 'FINIEX_DATA_HOME' in str(refused.value)
        _check(load, profile='mybot_profile', carry_over_from='', real_money=True, first_start=True)

    def test_a_declared_first_start_that_finds_a_usable_carry_over_is_refused(self):
        with pytest.raises(FirstStartContradictedError, match='run_before'):
            _check(self._own(CarryOverLoadOutcome.ACCEPTED), profile='mybot_profile',
                   carry_over_from='', real_money=True, first_start=True)

    def test_a_usable_carry_over_continues_without_a_declaration(self):
        _check(self._own(CarryOverLoadOutcome.ACCEPTED), profile='mybot_profile',
               carry_over_from='', real_money=True)

    def test_a_dry_or_mock_session_still_starts_fresh(self):
        _check(CarryOverLoad(_PATH, CarryOverLoadOutcome.ABSENT), profile='mybot_profile',
               carry_over_from='', real_money=False)

    def test_a_study_that_skips_adoption_checks_its_own_flat_book(self):
        _check(CarryOverLoad(_PATH, CarryOverLoadOutcome.ABSENT), profile='field_study',
               carry_over_from='', skipped_by='Field Study', real_money=True)

    def test_a_session_without_a_cold_start_reads_nothing_and_is_not_covered(self):
        _check(None, profile='mybot_profile', carry_over_from='', cold_start=False, real_money=True)


class TestTheSecurityStudyDeclaresItsFirstStart:
    """The runner declares a first start only where a real-money stage finds nothing of its own."""

    @pytest.fixture
    def real_money(self, monkeypatch):
        """The operator's market configuration allows real orders; the tracked one does not."""
        monkeypatch.setattr(security_cert_runner, 'resolve_dry_run', lambda config: False)

    def test_a_fresh_stage_on_a_new_installation_declares_it(self, real_money):
        assert starts_without_a_carry_over(
            Path('configs/autotrader_profiles/security_cert/security_cert_1_guards.json'))

    def test_a_stage_that_continues_another_declares_nothing(self, real_money):
        assert not starts_without_a_carry_over(
            Path('configs/autotrader_profiles/security_cert/security_cert_3_restart.json'))

    def test_a_stage_the_market_configuration_does_not_let_trade_declares_nothing(self):
        """Its own start refuses the conflict, so the runner has nothing to declare."""
        assert not starts_without_a_carry_over(
            Path('configs/autotrader_profiles/security_cert/security_cert_1_guards.json'))

    def test_the_mock_rehearsal_declares_nothing(self):
        assert not starts_without_a_carry_over(
            Path('configs/autotrader_profiles/mock/security_cert/security_cert_1_guards.json'))
