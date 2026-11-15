"""
FiniexTestingIDE - Timeline Determinism Tests (#460)

A backtest on the timeline driver comes out the same every time it runs: the same ticks give the
same orders, fills and trades, and nothing the run records depends on the wall clock or on how fast
the machine is. The fixture puts every source of the timeline to work at once — ticks sharing a
millisecond, arrival stamps that differ from the venue's, orders with a seeded latency that come due
between two ticks, and a heartbeat logic's timers in the quiet stretches.
"""

import dataclasses
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import ProcessTickLoopResult
from tests.shared.booking_day_harness import SYMBOL, simulation_result

_START = datetime(2026, 4, 1, 10, tzinfo=timezone.utc)
_TICK_COUNT = 1200
_FEED_DELAY_MS = 25


def _ticks() -> List[TickData]:
    """
    A seeded walk: about a third of the ticks share the millisecond of the one before, the rest
    follow after a pause of up to three seconds, and every tick arrives 25 ms after the venue
    stamped it.

    Returns:
        The ticks, in arrival order
    """
    rng = random.Random(460)
    ticks: List[TickData] = []
    venue_msc = int(_START.timestamp() * 1000)
    bid = 60_000.0
    for index in range(_TICK_COUNT):
        if index and rng.random() >= 0.33:
            venue_msc += rng.randint(50, 3_000)
        bid = round(bid + rng.uniform(-15.0, 15.0), 1)
        ticks.append(TickData(
            timestamp=datetime.fromtimestamp(venue_msc / 1000, tz=timezone.utc), symbol=SYMBOL,
            bid=bid, ask=bid + 1.0, volume=1.0, time_msc=venue_msc,
            collected_msc=venue_msc + _FEED_DELAY_MS))
    return ticks


def _run() -> ProcessTickLoopResult:
    """
    One backtest of the fixture, with a seeded latency of 20 to 400 ms and a timer every 700 ms.

    Returns:
        What the loop returns
    """
    return simulation_result(_ticks(), spot_mode=False, decision_events=True,
                             inbound_latency_ms=(20, 400), heartbeat_interval_ms=700,
                             tick_loop_profiling=True)


def _record(result: ProcessTickLoopResult) -> Dict[str, Any]:
    """
    What a run records about its trading, as plain values.

    Args:
        result: The loop's result

    Returns:
        The orders, their events, the trades and the booking periods
    """
    return {
        'orders': [dataclasses.asdict(order) for order in result.order_history],
        'order_events': [dataclasses.asdict(event) for event in result.order_events],
        'trades': [dataclasses.asdict(trade) for trade in result.trade_history],
        'booking_periods': [dataclasses.asdict(period) for period in result.booking_periods],
    }


@pytest.fixture(autouse=True)
def _wants_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The probe opts in to heartbeats, so the timers take part.

    Args:
        monkeypatch: pytest's patcher
    """
    monkeypatch.setattr(DeterministicProbe, 'wants_heartbeat', lambda logic: True)


class TestTheSameRunTwice:

    def test_the_fixture_is_worth_comparing(self):
        """A comparison over a run that traded nothing would prove nothing."""
        result = _run()
        arrivals = {datetime.fromtimestamp(tick.collected_msc / 1000, tz=timezone.utc)
                    for tick in _ticks()}
        assert len(result.trade_history) >= 1
        assert result.profiling_data.profile_counts['worker_decision'] < _TICK_COUNT, (
            'no slice held more than one tick')
        assert any(trade.entry_time not in arrivals or trade.exit_time not in arrivals
                   for trade in result.trade_history), 'no order filled between two ticks'

    def test_gives_the_same_orders_fills_and_trades(self):
        assert _record(_run()) == _record(_run())


class TestNoWallClockReachesTheRecord:

    def test_a_clock_that_jumps_about_changes_nothing(self, monkeypatch: pytest.MonkeyPatch):
        """
        The wall clock and the performance counters answer nonsense — jumping forward and back by
        up to an hour — and the run records exactly what it records with the real ones.
        """
        expected = _record(_run())
        rng = random.Random(7)
        base = time.time()

        def _nonsense() -> float:
            return base + rng.uniform(-3_600.0, 3_600.0)

        monkeypatch.setattr(time, 'time', _nonsense)
        monkeypatch.setattr(time, 'perf_counter', _nonsense)
        monkeypatch.setattr(time, 'monotonic', _nonsense)

        assert _record(_run()) == expected

    def test_every_time_the_record_holds_lies_in_the_data_s_span(self):
        """
        Every instant the run records — an order's, an event's, a trade's, a booking period's —
        lies between the first tick and the last one's arrival plus the longest latency. A wall
        clock read into the record would land at the time the test ran, years away.
        """
        ticks = _ticks()
        first = ticks[0].timestamp
        last = (datetime.fromtimestamp(ticks[-1].collected_msc / 1000, tz=timezone.utc)
                + timedelta(milliseconds=400))
        found = list(_instants_in(_record(_run())))
        assert found, 'the record holds no time at all'
        outside = [(path, value) for path, value in found if not first <= value <= last]
        assert not outside, f'{len(outside)} times outside the data, first: {outside[0]}'


def _instants_in(value: Any, path: str = ''):
    """
    Every datetime inside a plain record, with where it sits.

    Args:
        value: The record or a part of it
        path: Where the part sits

    Returns:
        The (path, datetime) pairs, as an iterator
    """
    if isinstance(value, datetime):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _instants_in(item, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _instants_in(item, f'{path}[{index}]')
