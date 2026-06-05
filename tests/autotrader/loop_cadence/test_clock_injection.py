"""
Loop Cadence — Canonical Clock Injection (#360)

The executor clock is no longer frozen to the last tick. on_tick sets it from the
tick timestamp; set_current_time injects the between-tick (ghost/heartbeat) time so
phase/op timeouts track real elapsed time during idle periods.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution


class TestClockInjection:
    """get_current_time reflects the loop-injected clock, not a frozen tick."""

    def test_raises_before_any_injection(self):
        """get_current_time raises until the loop injects a time (no tick yet)."""
        executor = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL).create_executor()
        with pytest.raises(RuntimeError):
            executor.get_current_time()

    def test_set_current_time_is_returned(self):
        """A ghost/heartbeat injection is returned verbatim by get_current_time."""
        executor = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL).create_executor()
        now = datetime(2026, 6, 3, 18, 0, 0, tzinfo=timezone.utc)
        executor.set_current_time(now)
        assert executor.get_current_time() == now

    def test_on_tick_sets_clock_from_tick(self):
        """A real tick sets the clock from its timestamp (the tick owns its time)."""
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        ts = datetime(2026, 6, 3, 18, 5, 0, tzinfo=timezone.utc)
        mock.feed_tick(executor, bid=49999.0, ask=50001.0, timestamp=ts)
        assert executor.get_current_time() == ts

    def test_clock_advances_past_frozen_tick_on_heartbeat(self):
        """
        After a real tick, a heartbeat injection advances the clock — it is NOT
        frozen to the last tick timestamp (the #360 fix that lets phase timeouts
        track real elapsed time during a tick gap).
        """
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        tick_ts = datetime(2026, 6, 3, 18, 5, 0, tzinfo=timezone.utc)
        mock.feed_tick(executor, bid=49999.0, ask=50001.0, timestamp=tick_ts)

        # 45 s tick gap — the loop fires a heartbeat and injects wall-clock.
        later = tick_ts + timedelta(seconds=45)
        executor.set_current_time(later)
        assert executor.get_current_time() == later
        # The last real tick is unchanged (price source stays last-known).
        assert executor.get_current_price('BTCUSD') == (49999.0, 50001.0)
