"""
FiniexTestingIDE - Sim Pass Hooks Tests (#458)

The backtest runner's own steps inside the loop core's tick pass: one equity sample serves the
drawdown and the booking period, and the market-data status follows the stress windows.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from python.framework.process.sim_pass_hooks import SimPassHooks
from python.framework.types.market_types.market_data_types import TickData

_TICK = TickData(timestamp=datetime(2026, 4, 27, 7, 0, tzinfo=timezone.utc), symbol='BTCUSD',
                 bid=50_000.0, ask=50_001.0)


class _Portfolio:
    def __init__(self) -> None:
        self.samples = 0

    def sample_equity(self) -> float:
        self.samples += 1
        return 10_250.0


class _Booking:
    def __init__(self) -> None:
        self.observed: List[Optional[float]] = []

    def observe_tick(self, equity: Optional[float]) -> None:
        self.observed.append(equity)


class _Simulator:
    def get_market_data_status(self) -> str:
        return 'status'


class _Driver:
    def __init__(self) -> None:
        self.ticks: List[datetime] = []

    def on_tick(self, timestamp: datetime) -> None:
        self.ticks.append(timestamp)

    def get_active_label(self) -> str:
        # As the real driver: the window is active only once the tick has moved it
        return 'feed dies' if self.ticks else ''


class _Tracker:
    def __init__(self) -> None:
        self.calls: List[Tuple[datetime, str, str]] = []

    def on_tick(self, timestamp: datetime, status: str, label: str) -> None:
        self.calls.append((timestamp, status, label))


def _hooks(profiling_enabled: bool, driver: Optional[_Driver] = None):
    portfolio, booking, tracker = _Portfolio(), _Booking(), _Tracker()
    times, counts = (defaultdict(float), defaultdict(int)) if profiling_enabled else ({}, {})
    hooks = SimPassHooks(_Simulator(), portfolio, booking, driver, tracker, profiling_enabled,
                         times, counts)
    return hooks, portfolio, booking, tracker, times, counts


class TestTheEquitySample:

    def test_one_sample_serves_the_drawdown_and_the_booking_period(self):
        hooks, portfolio, booking, _, _, _ = _hooks(profiling_enabled=False)

        hooks.observe_market()

        assert portfolio.samples == 1
        assert booking.observed == [10_250.0]

    def test_with_profiling_the_sample_is_timed_under_its_own_key(self):
        hooks, _, _, _, times, counts = _hooks(profiling_enabled=True)

        hooks.observe_market()
        hooks.observe_market()

        assert counts == {'equity_sample': 2}
        assert list(times) == ['equity_sample']

    def test_without_profiling_nothing_is_timed(self):
        hooks, _, _, _, times, counts = _hooks(profiling_enabled=False)

        hooks.observe_market()

        assert (times, counts) == ({}, {})


class TestTheMarketDataStatus:

    def test_the_stress_window_moves_before_the_status_is_recorded(self):
        driver = _Driver()
        hooks, _, _, tracker, _, _ = _hooks(profiling_enabled=False, driver=driver)

        hooks.prepare_algo(_TICK)

        assert driver.ticks == [_TICK.timestamp]
        assert tracker.calls == [(_TICK.timestamp, 'status', 'feed dies')]

    def test_without_a_stress_window_the_status_carries_no_label(self):
        hooks, _, _, tracker, _, _ = _hooks(profiling_enabled=False)

        hooks.prepare_algo(_TICK)

        assert tracker.calls == [(_TICK.timestamp, 'status', '')]
