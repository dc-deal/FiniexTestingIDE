"""
Cold Start — Carried Readings Follow the Run Kind (#626)

The pure rule behind the restore: whether a session continues the drawdown curve and the risk
baseline its carry-over holds, whether it may write its own over them, and the sentence that says
why it starts fresh. The wiring that applies it is pinned in `test_boot_wiring.py`.
"""

from typing import Optional

import pytest

from python.framework.autotrader.cold_start.carried_readings import (
    carried_readings_verdict,
    describe_readings_verdict,
    may_write_readings,
)
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    ColdStartPayload,
    ReadingsVerdict,
    RiskBaseline,
)
from python.framework.types.run_kind_types import OrdersTo

SIM = OrdersTo.SIMULATED
VENUE = OrdersTo.VENUE


def _carried(orders_to: Optional[OrdersTo], readings: bool = True) -> ColdStartPayload:
    """
    A carry-over whose readings a session of the given kind wrote.

    Args:
        orders_to: The stamp beside the readings; None for a document from before it existed
        readings: Whether it holds readings at all

    Returns:
        The payload
    """
    baseline = RiskBaseline(kind=BaselineKind.SESSION_FIXED, value=10_000.0,
                            taken_at_utc='2026-11-03T22:10:41+00:00',
                            origin=BaselineOrigin.FIRST_TICK) if readings else None
    return ColdStartPayload(risk_baseline=baseline, readings_orders_to=orders_to)


class TestTheVerdict:

    @pytest.mark.parametrize('writer, session, verdict', [
        (SIM, SIM, ReadingsVerdict.CONTINUED),
        (VENUE, VENUE, ReadingsVerdict.CONTINUED),
        (SIM, VENUE, ReadingsVerdict.RUN_KIND_CHANGED),
        (VENUE, SIM, ReadingsVerdict.RUN_KIND_CHANGED),
        (None, VENUE, ReadingsVerdict.NOT_RECORDED),
        (None, SIM, ReadingsVerdict.NOT_RECORDED),
    ])
    def test_the_readings_follow_the_run_kind(self, writer, session, verdict):
        assert carried_readings_verdict(_carried(writer), session, False, True) is verdict

    def test_the_switch_comes_first(self):
        assert (carried_readings_verdict(_carried(VENUE), VENUE, False, False)
                is ReadingsVerdict.SWITCHED_OFF)

    def test_nothing_carried_is_nothing_to_continue(self):
        assert (carried_readings_verdict(_carried(SIM, readings=False), VENUE, True, True)
                is ReadingsVerdict.NONE_CARRIED)

    def test_a_new_deployment_starts_fresh_whatever_wrote_them(self):
        assert (carried_readings_verdict(_carried(VENUE), VENUE, True, True)
                is ReadingsVerdict.NEW_DEPLOYMENT)


class TestWhoMayWrite:

    def test_a_rehearsal_never_writes_over_real_money(self):
        assert may_write_readings(_carried(VENUE), SIM, False) is False

    def test_unless_it_begins_a_new_deployment(self):
        assert may_write_readings(_carried(VENUE), SIM, True) is True

    @pytest.mark.parametrize('writer, session', [
        (SIM, SIM), (SIM, VENUE), (VENUE, VENUE), (None, SIM), (None, VENUE)])
    def test_every_other_session_writes_its_own(self, writer, session):
        assert may_write_readings(_carried(writer), session, False) is True


class TestWhatIsSaid:

    def test_real_money_after_a_rehearsal_names_both_and_the_rule(self):
        text = describe_readings_verdict(ReadingsVerdict.RUN_KIND_CHANGED, _carried(SIM), VENUE)
        assert 'a rehearsal (orders simulated)' in text
        assert 'places real orders' in text
        assert 'never linked' in text

    def test_a_rehearsal_after_real_money_says_they_wait(self):
        text = describe_readings_verdict(ReadingsVerdict.RUN_KIND_CHANGED, _carried(VENUE), SIM)
        assert 'a session that placed real orders' in text
        assert 'next session that places real orders' in text

    def test_an_unrecorded_carry_over_says_so(self):
        text = describe_readings_verdict(ReadingsVerdict.NOT_RECORDED, _carried(None), VENUE)
        assert 'does not say' in text

    @pytest.mark.parametrize('verdict', [
        ReadingsVerdict.CONTINUED, ReadingsVerdict.NONE_CARRIED, ReadingsVerdict.SWITCHED_OFF])
    def test_nothing_is_said_where_nothing_needs_saying(self, verdict):
        assert describe_readings_verdict(verdict, _carried(VENUE), VENUE) == ''
