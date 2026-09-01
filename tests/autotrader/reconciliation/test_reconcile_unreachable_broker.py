"""
The reconcile cycle survives an unreachable broker (#473).

Before this, `get_broker_orders()` had no guard and neither did its caller in the tick
loop, so one transient 502 from a public venue propagated to `autotrader_main` and ended
the session in an emergency shutdown. On a thirty-day unattended run that is not an edge
case — it is a Tuesday.

The answer is not a retry inside the loop. `reconcile()` is already cadenced, so the
cadence IS the ladder: the cycle is skipped, reported, and the next one comes at the
normal interval. A TERMINAL failure still propagates, because a refused credential is not
something to keep quiet about.
"""

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ReconciliationDefaults,
)
from tests.autotrader.reconciliation.conftest import make_pending


class Unreachable:
    """Adapter stand-in whose truth pull fails the way a venue outage does."""

    def __init__(self, error: BaseException, fail_times: int = 10_000):
        self._error = error
        self._remaining = fail_times
        self.calls = 0

    def get_broker_orders(self):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return []

    def get_broker_positions(self):
        return []

    def get_broker_balances(self):
        return {}


def test_transient_failure_does_not_raise(make_reconciler):
    adapter = Unreachable(ConnectionAttemptFailedError('HTTP 502 from /0/private/OpenOrders'))
    rec = make_reconciler(adapter, active_orders=[make_pending('o1', 'O1')])

    result = rec.reconcile(current_tick=1)

    assert result.skipped_reason is not None
    assert '502' in result.skipped_reason


def test_skipped_cycle_verifies_nothing(make_reconciler):
    # A skipped cycle must not look clean: nothing was compared, so claiming the local
    # shadow matches broker truth would be a statement we did not earn.
    adapter = Unreachable(ConnectionAttemptFailedError('connection refused'))
    rec = make_reconciler(adapter, active_orders=[make_pending('o1', 'O1')])

    result = rec.reconcile(current_tick=1)

    assert result.is_clean is False
    assert result.ghost_orders == []
    assert result.orphan_orders == []


def test_skip_advances_the_cadence(make_reconciler):
    # Without this an unreachable venue would be re-attempted on every heartbeat — a retry
    # storm wearing a cadence for a hat.
    cfg = ReconciliationDefaults(enabled=True, interval_ticks=10, min_interval_seconds=9999.0)
    adapter = Unreachable(ConnectionAttemptFailedError('HTTP 503'))
    rec = make_reconciler(adapter, config=cfg)

    rec.reconcile(current_tick=10)

    assert not rec.is_due(15)
    assert rec.is_due(20)


def test_skip_is_counted_and_surfaced(make_reconciler):
    # §473: "gave up" must never look like "still checking". A reconcile count climbing
    # against a dead venue is exactly that disguise.
    adapter = Unreachable(ConnectionAttemptFailedError('HTTP 502'))
    rec = make_reconciler(adapter)

    rec.reconcile(current_tick=1)
    rec.reconcile(current_tick=2)

    counters = rec.get_display_counters()
    assert counters['reconcile_skipped'] == 2
    assert '502' in counters['reconcile_skipped_reason']
    assert rec.get_skipped_count() == 2


def test_recovers_on_the_next_cycle(mock_adapter, make_reconciler):
    # The point of skipping rather than dying: the session is still there when the venue
    # comes back.
    adapter = Unreachable(ConnectionAttemptFailedError('HTTP 502'), fail_times=1)
    rec = make_reconciler(adapter)

    first = rec.reconcile(current_tick=1)
    second = rec.reconcile(current_tick=2)

    assert first.skipped_reason is not None
    assert second.skipped_reason is None
    assert second.is_clean is True


def test_terminal_failure_still_propagates(make_reconciler):
    # A refused credential is not a blip. Swallowing it would report the venue's outage
    # for our own misconfiguration, quietly, for thirty days.
    adapter = Unreachable(ConnectionAttemptFailedError('HTTP 401', terminal=True))
    rec = make_reconciler(adapter)

    with pytest.raises(ConnectionAttemptFailedError):
        rec.reconcile(current_tick=1)


def test_unknown_exception_still_propagates(make_reconciler):
    # An unregistered exception is most likely our own defect, and a defect that only
    # ever produces a skipped cycle is a defect nobody finds.
    adapter = Unreachable(ValueError('parser bug'))
    rec = make_reconciler(adapter)

    with pytest.raises(ValueError):
        rec.reconcile(current_tick=1)
