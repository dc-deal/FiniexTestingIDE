"""
The entry hold a start places, and how it ends (#499).

A start that finds orders of another owner on this bot's instrument, while the account is declared
exclusive, starts and holds its entries: its resting entries are cancelled with the hold, the operator
hears it out of band, and an attended start may lift it — which accepts those orders for the next start
too. Otherwise the hold lifts on the reconcile cycle that shows those orders gone, and never on one that
could not read the venue.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.safety.boot_entry_hold import hold_entries_at_boot
from python.framework.autotrader.safety.boot_premise_lifter import BootPremiseLifter
from python.framework.reporting.builders.safety_report_builder import build_safety_report_from_session
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.autotrader_types.cold_start_types import BootPremiseViolation
from python.framework.types.autotrader_types.safety_session_types import SafetySessionRecord
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.autotrader_defaults_config_types import SafetyConfig
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder, ReconciliationResult
from python.framework.types.persistence_types import BaselineKind, BaselineOrigin, RiskBaseline
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderType,
)
from tests.autotrader.cold_start.conftest import RecordingLogger

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True])
_VIOLATION = BootPremiseViolation(
    symbol='BTCUSD', foreign_refs=['OQ5A1-HUMAN'],
    detail='1 order(s) of another owner rest on BTCUSD while the account is declared exclusive '
           '(OQ5A1-HUMAN) — cancel them at the venue, or start with --attended and lift the hold')


def _broker_order(broker_ref: str, symbol: str = 'BTCUSD') -> BrokerOrder:
    """A resting order as the venue reports it."""
    return BrokerOrder(broker_ref=broker_ref, symbol=symbol, direction=OrderDirection.LONG,
                       order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING,
                       price=40000.0, client_order_id=None)


def _executor(spot_mode: bool = False):
    """A live session's executor, before its first market data."""
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, spot_mode=spot_mode)
    return mock, mock.create_executor()


class TestTheStartHoldsItsEntries:

    @ACCOUNT_MODELS
    def test_an_unattended_start_holds_and_is_heard(self, spot_mode):
        mock, executor = _executor(spot_mode)
        alerts, logger = MagicMock(), RecordingLogger()

        accepted = hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=True,
                                        operator_alerts=alerts, logger=logger)

        assert accepted is None
        (hold,) = executor.get_entry_holds().get_active()
        assert (hold.reason, hold.held_at, hold.detail) == (
            EntryHoldReason.BOOT_PREMISE, None, _VIOLATION.detail), 'dated from the start'
        kind, level, title, detail = alerts.send.call_args.args
        assert (kind, level) == (OperatorAlertKind.ENTRIES_HELD, OperatorAlertLevel.CRITICAL)
        assert 'BTCUSD' in title and 'lifts by itself' in detail
        assert any('Entries held' in e for e in logger.errors), 'into the error pot'

    def test_without_a_reconcile_the_alert_says_only_the_operator_lifts_it(self):
        mock, executor = _executor()
        alerts = MagicMock()
        hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=False,
                             operator_alerts=alerts, logger=RecordingLogger())
        assert '--attended' in alerts.send.call_args.args[3]

    @ACCOUNT_MODELS
    def test_its_resting_entries_go_with_the_hold(self, spot_mode):
        mock, executor = _executor(spot_mode)
        executor.adopt_resting_orders([('ord_btcusd_7', _broker_order('OQ7X2A-OURS'))])

        hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=True,
                             operator_alerts=MagicMock(), logger=RecordingLogger())
        mock.feed_tick(executor)

        assert executor.get_active_orders() == []
        (ended,) = [r for r in executor.get_order_history() if r.order_id == 'ord_btcusd_7']
        assert (ended.initiator, ended.end_reason) == (
            OrderInitiator.FRAMEWORK, OrderEndReason.ENTRIES_HELD), 'never "the strategy cancelled"'


class TestAnAttendedStartMayLiftIt:

    def test_a_no_keeps_the_hold(self):
        mock, executor = _executor()
        accepted = hold_entries_at_boot(_VIOLATION, executor, attended=True, reconciled=True,
                                        operator_alerts=MagicMock(), logger=RecordingLogger(),
                                        ask=lambda prompt: '')
        assert accepted is None
        assert executor.get_entry_holds().is_held(EntryHoldReason.BOOT_PREMISE)

    def test_a_yes_lifts_it_and_accepts_the_orders_for_the_next_start(self):
        mock, executor = _executor()
        alerts = MagicMock()
        questions = []

        accepted = hold_entries_at_boot(_VIOLATION, executor, attended=True, reconciled=True,
                                        operator_alerts=alerts, logger=RecordingLogger(),
                                        ask=lambda prompt: questions.append(prompt) or 'y')

        assert 'OQ5A1-HUMAN' in questions[0] and '[y/N]' in questions[0]
        assert accepted.broker_refs == ['OQ5A1-HUMAN'] and accepted.accepted_at_utc
        (hold,) = executor.get_entry_holds().get_all()
        assert (hold.lifted_by, hold.lifted_at) == (EntryHoldLift.OPERATOR, None)
        assert alerts.send.call_args.args[0] is OperatorAlertKind.ENTRIES_LIFTED

    def test_a_yes_keeps_the_orders_accepted_at_an_earlier_start(self):
        """The carry-over holds one set: replacing it would hold over the earlier orders again."""
        mock, executor = _executor()
        violation = BootPremiseViolation(symbol='BTCUSD', foreign_refs=['OQ9Z4C-NEW'],
                                         detail=_VIOLATION.detail, accepted_refs=['OQ5A1-HUMAN'])

        accepted = hold_entries_at_boot(violation, executor, attended=True, reconciled=True,
                                        operator_alerts=MagicMock(), logger=RecordingLogger(),
                                        ask=lambda prompt: 'y')

        assert accepted.broker_refs == ['OQ5A1-HUMAN', 'OQ9Z4C-NEW']


class TestTheHoldLiftsWhenItsOrdersAreGone:

    def _held(self):
        mock, executor = _executor()
        hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=True,
                             operator_alerts=MagicMock(), logger=RecordingLogger())
        alerts = MagicMock()
        return executor, alerts, BootPremiseLifter(_VIOLATION, executor, alerts, RecordingLogger())

    def test_a_cycle_that_still_shows_them_keeps_it(self):
        executor, alerts, lifter = self._held()
        lifter.observe(ReconciliationResult(ghost_orders=[_broker_order('OQ5A1-HUMAN')]))
        assert executor.get_entry_holds().is_held(EntryHoldReason.BOOT_PREMISE)
        alerts.send.assert_not_called()

    def test_a_cycle_that_could_not_read_the_venue_keeps_it(self):
        executor, alerts, lifter = self._held()
        lifter.observe(ReconciliationResult(skipped_reason='broker truth unreachable'))
        assert executor.get_entry_holds().is_held(EntryHoldReason.BOOT_PREMISE)

    def test_a_completed_cycle_without_them_lifts_it(self):
        executor, alerts, lifter = self._held()
        now = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
        executor.set_current_time(now)

        # Another owner's order elsewhere, or a new one, is not what this hold was placed over
        lifter.observe(ReconciliationResult(ghost_orders=[_broker_order('OQ8B3-ETH', 'ETHUSD')]))

        (hold,) = executor.get_entry_holds().get_all()
        assert (hold.lifted_by, hold.lifted_at) == (EntryHoldLift.OBSERVED, now)
        assert alerts.send.call_args.args[:2] == (OperatorAlertKind.ENTRIES_LIFTED,
                                                  OperatorAlertLevel.INFO)

    def test_a_lifted_hold_is_not_lifted_twice(self):
        executor, alerts, lifter = self._held()
        lifter.observe(ReconciliationResult())
        lifter.observe(ReconciliationResult())
        assert alerts.send.call_count == 1


def _measured() -> SafetySessionRecord:
    """What the event loop measured, from a baseline taken at the first tick."""
    return SafetySessionRecord(enabled=True, baseline=RiskBaseline(
        kind=BaselineKind.SESSION_FIXED, value=1000.0, taken_at_utc='2026-11-24T00:00:01+00:00',
        origin=BaselineOrigin.FIRST_TICK), final_value=1000.0)


class TestTheRecordNamesEveryHold:

    def test_a_held_session_names_its_hold_beside_its_figures(self):
        mock, executor = _executor()
        hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=True,
                             operator_alerts=MagicMock(), logger=RecordingLogger())

        report = build_safety_report_from_session(
            'run_1', _measured(), SafetyConfig(), 'BTCUSD',
            entry_holds=executor.get_entry_holds().get_all())

        assert report.entry_holds_key == ['reason', 'held_at']
        (row,) = report.entry_holds
        assert (row.reason, row.held_at, row.lifted_by) == (EntryHoldReason.BOOT_PREMISE, None, None)
        assert (report.entry_hold_count, report.entries_held_at_end) == (1, True)
        assert report.baseline_value == 1000.0, 'the figures are the measured ones'

    def test_a_lifted_hold_states_how_and_when(self):
        mock, executor = _executor()
        hold_entries_at_boot(_VIOLATION, executor, attended=False, reconciled=True,
                             operator_alerts=MagicMock(), logger=RecordingLogger())
        lifted_at = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
        executor.get_entry_holds().lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OBSERVED, lifted_at)

        report = build_safety_report_from_session(
            'run_1', _measured(), SafetyConfig(), 'BTCUSD',
            entry_holds=executor.get_entry_holds().get_all())

        (row,) = report.entry_holds
        assert (row.lifted_by, row.lifted_at) == (EntryHoldLift.OBSERVED, lifted_at.isoformat())
        assert report.entries_held_at_end is False
