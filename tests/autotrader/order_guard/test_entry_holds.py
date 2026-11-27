"""
The entry holds of a session, and the lift routes each reason declares (#499).

A reason's lift routes are declared beside the reason, and a declaration earns its place only when it
is complete in both directions: every route a reason declares has the code that performs it — a route
nobody performs would leave a hold that never lifts — and every lift the code performs is one its
reason declares.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from python.framework.trading_env.entry_holds import EntryHolds
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason

_AT = datetime(2026, 11, 24, 1, 5, tzinfo=timezone.utc)
_LATER = _AT + timedelta(hours=3)
_PYTHON = Path(__file__).resolve().parents[3] / 'python'
# A lift the code performs, named as one call: `.lift(EntryHoldReason.X, EntryHoldLift.Y`
_LIFT_CALL = re.compile(r'\.lift\(\s*EntryHoldReason\.([A-Z_]+),\s*EntryHoldLift\.([A-Z_]+)')


def _lifts_in_the_code() -> set:
    """Every (reason, route) a lift call in the framework names."""
    found = set()
    for path in _PYTHON.rglob('*.py'):
        for reason, route in _LIFT_CALL.findall(path.read_text(encoding='utf-8')):
            found.add((EntryHoldReason[reason], EntryHoldLift[route]))
    return found


class TestTheRegister:

    def test_a_hold_stands_until_it_is_lifted(self):
        holds = EntryHolds()
        assert holds.hold(EntryHoldReason.BOOT_PREMISE, _AT, 'orders of another owner')
        assert holds.is_held(EntryHoldReason.BOOT_PREMISE)
        assert [h.detail for h in holds.get_active()] == ['orders of another owner']

        lifted = holds.lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OBSERVED, _LATER)

        assert (lifted.lifted_at, lifted.lifted_by) == (_LATER, EntryHoldLift.OBSERVED)
        assert holds.get_active() == ()
        assert holds.get_all() == (lifted,), 'the lifted hold stays in the record'

    def test_a_second_hold_of_a_standing_reason_keeps_the_first_start(self):
        holds = EntryHolds()
        holds.hold(EntryHoldReason.BOOT_PREMISE, _AT, 'first')
        assert not holds.hold(EntryHoldReason.BOOT_PREMISE, _LATER, 'second')
        assert [(h.held_at, h.detail) for h in holds.get_all()] == [(_AT, 'first')]

    def test_lifting_nothing_answers_nothing(self):
        assert EntryHolds().lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OPERATOR, _AT) is None

    def test_a_hold_lifted_once_can_hold_again(self):
        holds = EntryHolds()
        holds.hold(EntryHoldReason.BOOT_PREMISE, _AT, 'first')
        holds.lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OBSERVED, _LATER)
        assert holds.hold(EntryHoldReason.BOOT_PREMISE, _LATER, 'again')
        assert len(holds.get_all()) == 2 and len(holds.get_active()) == 1


class TestEveryReasonDeclaresHowItEnds:

    @pytest.mark.parametrize('reason', list(EntryHoldReason))
    def test_every_reason_has_a_route(self, reason):
        assert reason.lift_routes(), f'a {reason.value} hold would never lift'

    def test_a_lift_by_an_undeclared_route_is_refused(self):
        undeclared = [(reason, route) for reason in EntryHoldReason for route in EntryHoldLift
                      if route not in reason.lift_routes()]
        for reason, route in undeclared:
            with pytest.raises(ValueError):
                EntryHolds().lift(reason, route, _AT)

    def test_every_declared_route_is_performed_by_the_code(self):
        declared = {(reason, route) for reason in EntryHoldReason for route in reason.lift_routes()}
        missing = declared - _lifts_in_the_code()
        assert not missing, f'declared, and nothing performs it: {sorted(missing)}'

    def test_every_lift_the_code_performs_is_declared(self):
        undeclared = {(reason, route) for reason, route in _lifts_in_the_code()
                      if route not in reason.lift_routes()}
        assert not undeclared, f'performed, and its reason does not declare it: {sorted(undeclared)}'
