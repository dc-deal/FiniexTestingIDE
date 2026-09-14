"""
Pending-Order Latency — Clock Choice

An order's latency is a DURATION, and a duration must not be computed from two
wall-clock readings: NTP can step that clock backwards inside the submit-to-fill
window, and the negative value that follows lands in a min/max aggregate, where a
single impossible number reads like a venue fault rather than a clock fault.

These tests pin the property rather than the implementation: the measurement is
independent of the wall clock, and an order that carries no monotonic stamp reports
the latency as UNMEASURED instead of substituting a wrong one.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderTiming,
)
from python.framework.types.trading_env_types.order_types import OrderDirection


def _pending(submitted_at=None, submitted_monotonic=None) -> PendingOrder:
    """Builds a pending order carrying only the stamps under test.

    Args:
        submitted_at: Wall-clock submission stamp, or None.
        submitted_monotonic: Monotonic submission stamp, or None.

    Returns:
        A PendingOrder with those timing fields set.
    """
    return PendingOrder(
        pending_order_id='ORD-LAT',
        timing=PendingOrderTiming(
            submitted_at=submitted_at,
            submitted_monotonic=submitted_monotonic,
        ),
    )


class TestTheLatencyIsMeasuredOnTheMonotonicClock:
    """The duration comes from the monotonic clock, never from the wall clock."""

    def test_the_elapsed_monotonic_difference_becomes_milliseconds(self):
        """A 0.25 s monotonic difference is reported as 250 ms."""
        pending = _pending(submitted_monotonic=1000.0)

        with patch('python.framework.trading_env.live.live_trade_executor.time.monotonic',
                   return_value=1000.25):
            latency = LiveTradeExecutor._calculate_pending_latency_ms(pending)

        assert latency == 250.0

    def test_a_wall_clock_that_stepped_BACKWARDS_cannot_produce_a_negative_latency(self):
        """The defect this file exists for.

        `submitted_at` lies in the FUTURE relative to the current wall clock — exactly
        what an NTP step backwards leaves behind. The old implementation subtracted
        those two readings and returned a negative latency; the monotonic pair cannot.
        """
        submitted_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        pending = _pending(submitted_at=submitted_at, submitted_monotonic=500.0)

        with patch('python.framework.trading_env.live.live_trade_executor.time.monotonic',
                   return_value=500.4):
            latency = LiveTradeExecutor._calculate_pending_latency_ms(pending)

        # approx, because 500.4 - 500.0 is not exact in binary — the same arithmetic
        # this file's sibling concern is about.
        assert latency == pytest.approx(400.0)
        assert latency > 0

    def test_the_wall_clock_stamp_is_not_read_at_all(self):
        """Two orders whose wall-clock stamps differ by an hour measure the same."""
        early = _pending(datetime(2020, 1, 1, tzinfo=timezone.utc), submitted_monotonic=10.0)
        late = _pending(datetime(2030, 1, 1, tzinfo=timezone.utc), submitted_monotonic=10.0)

        with patch('python.framework.trading_env.live.live_trade_executor.time.monotonic',
                   return_value=11.0):
            assert (LiveTradeExecutor._calculate_pending_latency_ms(early)
                    == LiveTradeExecutor._calculate_pending_latency_ms(late)
                    == 1000.0)


class TestAnUnmeasurableLatencyIsReportedAsUnmeasured:
    """No stamp means no number — never a wall-clock substitute."""

    def test_without_a_monotonic_stamp_the_result_is_None(self):
        """A wall-clock stamp alone is not enough to measure a duration."""
        pending = _pending(submitted_at=datetime.now(timezone.utc))

        assert LiveTradeExecutor._calculate_pending_latency_ms(pending) is None

    def test_an_order_with_no_stamps_at_all_is_None(self):
        """The pre-submission state carries neither stamp."""
        assert LiveTradeExecutor._calculate_pending_latency_ms(_pending()) is None


class TestEveryLiveSubmissionCarriesTheStamp:
    """The guard against a future submission site forgetting it."""

    def test_register_pending_open_stamps_the_monotonic_clock(self, request_processor):
        """An opened order can be measured the moment it is registered."""
        request_processor.register_pending_open(
            order_id='ORD-MONO-1',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref='MOCK-MONO-1',
        )

        pending = request_processor.get_by_broker_ref('MOCK-MONO-1')
        assert pending.timing.submitted_monotonic is not None
        assert LiveTradeExecutor._calculate_pending_latency_ms(pending) >= 0.0
