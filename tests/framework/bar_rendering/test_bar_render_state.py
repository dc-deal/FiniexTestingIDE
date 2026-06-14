"""
BarRenderState Signal Tests.

Verifies the bar renderer surfaces its bar-close transition as a typed
BarRenderState that the controller accumulates and hands over once per algo
pass. This is the producer side of the ON_BAR_CLOSE worker recompute feature:
the worker orchestrator consumes this state to gate recompute, so the close set
must be correct, cleared on consume, carried over clipped ticks, and selective
per timeframe.
"""

from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.market_types.market_data_types import BarRenderState, TickData


# =============================================================================
# HELPERS
# =============================================================================

class _TimeframeStubWorker:
    """Minimal worker surface — only the timeframe requirement the controller reads."""

    def __init__(self, timeframes: List[str]):
        self._timeframes = timeframes

    def get_required_timeframes(self) -> List[str]:
        return self._timeframes


def _controller(timeframes: List[str]) -> BarRenderingController:
    """Build a controller rendering the given timeframes (no disk logger)."""
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([_TimeframeStubWorker(timeframes)])
    return controller


def _tick(ts: datetime, bid: float = 42000.0, symbol: str = 'BTCUSD') -> TickData:
    """One synthetic tick."""
    return TickData(timestamp=ts, symbol=symbol, bid=bid, ask=bid + 1.0, volume=0.1)


# Aligned to a 10:00 UTC bar boundary
_START = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


# =============================================================================
# TESTS
# =============================================================================

class TestBarRenderStateType:
    """The typed state package itself."""

    def test_default_is_empty(self):
        """A fresh BarRenderState carries no closed timeframes."""
        assert BarRenderState().closed_timeframes == set()


class TestConsume:
    """Accumulate / consume / clear semantics at the controller."""

    def test_no_close_yields_empty_state(self):
        """Ticks within the first forming bar close nothing."""
        controller = _controller(['M5'])
        controller.process_tick(_tick(_START))
        controller.process_tick(_tick(_START + timedelta(seconds=30)))
        assert controller.consume_bar_render_state().closed_timeframes == set()

    def test_crossing_a_boundary_reports_the_closed_timeframe(self):
        """Crossing into the next M5 period closes the M5 bar."""
        controller = _controller(['M5'])
        controller.process_tick(_tick(_START))                          # opens 10:00 bar
        controller.process_tick(_tick(_START + timedelta(minutes=5)))   # crosses → closes 10:00
        assert controller.consume_bar_render_state().closed_timeframes == {'M5'}

    def test_consume_clears(self):
        """A second consume right after a close returns an empty state."""
        controller = _controller(['M5'])
        controller.process_tick(_tick(_START))
        controller.process_tick(_tick(_START + timedelta(minutes=5)))
        first = controller.consume_bar_render_state()
        assert first.closed_timeframes == {'M5'}
        assert controller.consume_bar_render_state().closed_timeframes == set()

    def test_clipped_tick_carry_over(self):
        """
        A close on a tick whose algo pass is skipped (clipped) is not lost.

        The renderer runs for every tick; only the non-clipped pass consumes. So a
        boundary crossing that is followed by an un-consumed ('clipped') tick must
        still surface on the next consume.
        """
        controller = _controller(['M5'])
        controller.process_tick(_tick(_START))                          # opens 10:00
        controller.process_tick(_tick(_START + timedelta(minutes=5)))   # closes 10:00 (algo pass clipped → no consume)
        controller.process_tick(_tick(_START + timedelta(minutes=5, seconds=30)))  # intra-bar, no close
        # First consume after the skip still carries the earlier close.
        assert controller.consume_bar_render_state().closed_timeframes == {'M5'}

    def test_two_closes_accumulate_until_consumed(self):
        """Multiple closes between consumes accumulate into one state."""
        controller = _controller(['M5'])
        controller.process_tick(_tick(_START))                            # opens 10:00
        controller.process_tick(_tick(_START + timedelta(minutes=5)))     # closes 10:00
        controller.process_tick(_tick(_START + timedelta(minutes=10)))    # closes 10:05
        assert controller.consume_bar_render_state().closed_timeframes == {'M5'}


class TestMultiTimeframeSelectivity:
    """Only the timeframe that actually closed appears in the state."""

    def test_finer_close_does_not_report_coarser(self):
        """An M5 boundary that is not an M15 boundary closes only M5."""
        controller = _controller(['M5', 'M15'])
        controller.process_tick(_tick(_START))                          # opens 10:00 M5 + M15
        controller.process_tick(_tick(_START + timedelta(minutes=5)))   # crosses M5 (10:05), still inside M15 10:00-10:15
        assert controller.consume_bar_render_state().closed_timeframes == {'M5'}

    def test_coarser_boundary_reports_both(self):
        """The 10:15 boundary closes both M5 (10:10) and M15 (10:00)."""
        controller = _controller(['M5', 'M15'])
        controller.process_tick(_tick(_START))                          # opens 10:00
        controller.process_tick(_tick(_START + timedelta(minutes=15)))  # crosses 10:15 → M5 + M15 close
        assert controller.consume_bar_render_state().closed_timeframes == {'M5', 'M15'}
