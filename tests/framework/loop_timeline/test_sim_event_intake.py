"""
FiniexTestingIDE - Sim Event Intake Tests (#460)

The backtest's timeline: the ticks grouped by when they arrived, the simulator's due times and a
heartbeat logic's timers, merged by stamp and handed over one instant at a time.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.intake.sim_event_intake import SimEventIntake
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.market_types.market_data_types import TickData

_START = 1_780_000_000_000


def _tick(arrival_offset: int, event_offset: Optional[int] = None) -> TickData:
    """
    A tick that arrived at an offset from the start.

    Args:
        arrival_offset: Its arrival, in ms after the start
        event_offset: Its venue stamp, in ms after the start; 20 ms before its arrival when left out

    Returns:
        The tick
    """
    event = _START + (arrival_offset - 20 if event_offset is None else event_offset)
    return TickData(timestamp=datetime.fromtimestamp(event / 1000, tz=timezone.utc),
                    symbol='BTCUSD', bid=60_000.0, ask=60_001.0, time_msc=event,
                    collected_msc=_START + arrival_offset)


class _Simulator:
    """
    The due times as the simulator answers them; the instant resolves what is due by its stamp,
    as the loop core does, and an order can be sent at an instant.

    Args:
        dues: The due times standing at the start, in ms after the start
    """

    def __init__(self, dues: Optional[List[int]] = None) -> None:
        self.dues = [_START + due for due in dues or []]
        # in ms after the start: the instant an order is sent at → when it comes due
        self.send_at: dict = {}

    def peek_next_due_msc(self) -> Optional[int]:
        return min(self.dues) if self.dues else None

    def run(self, stamp: int) -> None:
        self.dues = [due for due in self.dues if due > stamp]
        if stamp - _START in self.send_at:
            self.dues.append(_START + self.send_at.pop(stamp - _START))


def _instants(ticks: List[TickData], simulator: _Simulator, timer_ms: int = 0,
              threshold_ms: float = 300_000.0,
              logger: Optional[MagicMock] = None) -> List[Tuple[int, Tuple[type, ...]]]:
    """
    Every instant the intake hands over, run as the loop core would.

    Args:
        ticks: The ticks
        simulator: The due times
        timer_ms: The heartbeat interval, 0 for none
        threshold_ms: The gap threshold
        logger: The scenario log the intake reports to; a stand-in when left out

    Returns:
        Each instant's stamp, as an offset from the start, and the classes of its events
    """
    intake = SimEventIntake(ticks, simulator, EventSequencer(), timer_ms, threshold_ms,
                            logger if logger is not None else MagicMock())
    handed = []
    for events in intake:
        stamp = events[0].ts_init
        handed.append((stamp - _START, tuple(type(event) for event in events)))
        simulator.run(stamp)
    return handed


def _slice_sizes(ticks: List[TickData]) -> List[int]:
    """
    How many ticks each slice holds.

    Args:
        ticks: The ticks

    Returns:
        The sizes, in order
    """
    intake = SimEventIntake(ticks, _Simulator(), EventSequencer(), 0, 0.0, MagicMock())
    sizes = []
    for events in intake:
        slice_event: LoopEvent = events[0]
        assert isinstance(slice_event, DataSliceEvent)
        sizes.append(len(slice_event.ticks))
    return sizes


class TestTheTicksBecomeSlices:

    def test_the_ticks_that_arrived_in_one_millisecond_are_one_slice(self):
        assert _slice_sizes([_tick(0), _tick(0), _tick(0), _tick(5), _tick(9)]) == [3, 1, 1]

    def test_a_slice_is_stamped_with_the_arrival_and_its_last_tick_s_venue_stamp(self):
        ticks = [_tick(10, event_offset=1), _tick(10, event_offset=4)]
        (events,) = list(SimEventIntake(ticks, _Simulator(), EventSequencer(), 0, 0.0,
                                        MagicMock()))
        assert (events[0].ts_init, events[0].ts_event) == (_START + 10, _START + 4)

    def test_an_arrival_that_steps_back_joins_the_running_instant(self):
        # Two files meet: the later one's first arrival lies before the earlier one's last
        assert _slice_sizes([_tick(0), _tick(10), _tick(7), _tick(20)]) == [1, 2, 1]

    def test_a_tick_without_an_arrival_orders_by_its_venue_stamp(self):
        tick = _tick(0, event_offset=3)
        tick = TickData(timestamp=tick.timestamp, symbol='BTCUSD', bid=1.0, ask=1.0,
                        time_msc=tick.time_msc, collected_msc=0)
        (events,) = list(SimEventIntake([tick], _Simulator(), EventSequencer(), 0, 0.0,
                                        MagicMock()))
        assert events[0].ts_init == _START + 3

    def test_the_last_tick_handed_over_is_named_by_its_index(self):
        intake = SimEventIntake([_tick(0), _tick(0), _tick(5)], _Simulator(), EventSequencer(),
                                0, 0.0, MagicMock())
        seen = []
        for _ in intake:
            seen.append(intake.get_last_tick_index())
        assert seen == [1, 2]


class TestTheDueTimes:

    def test_a_due_time_between_two_slices_is_an_instant_of_its_own(self):
        handed = _instants([_tick(0), _tick(100)], _Simulator([40]))
        assert handed == [(0, (DataSliceEvent,)), (40, (ResolutionEvent,)),
                          (100, (DataSliceEvent,))]

    def test_a_due_time_on_a_slice_s_stamp_joins_the_slice(self):
        handed = _instants([_tick(0), _tick(100)], _Simulator([100]))
        assert handed == [(0, (DataSliceEvent,)), (100, (DataSliceEvent, ResolutionEvent))]

    def test_an_order_sent_in_an_instant_comes_due_before_a_later_one(self):
        # A close is due at +80; at +5 an order with 20 ms latency is sent: due at +25, first
        simulator = _Simulator([80])
        simulator.send_at[5] = 25
        handed = _instants([_tick(0), _tick(5), _tick(200)], simulator)
        assert [stamp for stamp, _ in handed] == [0, 5, 25, 80, 200]

    def test_nothing_comes_due_after_the_last_tick(self):
        handed = _instants([_tick(0), _tick(100)], _Simulator([150]))
        assert [stamp for stamp, _ in handed] == [0, 100]

    def test_a_due_time_left_standing_by_its_own_instant_is_not_handed_over_again(self):
        simulator = _Simulator([40])
        simulator.run = lambda stamp: None      # resolves nothing
        handed = _instants([_tick(0), _tick(100)], simulator)
        assert [stamp for stamp, _ in handed] == [0, 40, 100]

    def test_a_due_time_before_the_instant_handed_is_reported_once_and_counted(self):
        """
        A due time at +40 the simulator never resolves is still standing at the slice at +200,
        though +100 was handed over since: it resolves there, late, and the scenario's log is
        told once — the first with its stamps — and the intake counts every one (#460).
        """
        simulator = _Simulator([40])
        simulator.run = lambda stamp: None      # resolves nothing
        logger = MagicMock()
        intake = SimEventIntake([_tick(0), _tick(100), _tick(200), _tick(300)], simulator,
                                EventSequencer(), 0, 300_000.0, logger)
        handed = [events[0].ts_init - _START for events in intake]

        assert handed == [0, 40, 100, 200, 300]
        assert intake.get_late_due_count() == 2
        message, = [call.args[0] for call in logger.error.call_args_list]
        assert 'lies before the instant already handed over' in message
        assert '60 ms late' in message

    def test_an_order_sent_where_a_fill_resolved_comes_due_a_millisecond_later(self):
        """
        A fill resolves at +40, and its hook sends an order with the least latency a run accepts,
        1 ms: it comes due at +41, an instant of its own before the next slice (#460).
        """
        simulator = _Simulator([40])
        simulator.send_at[40] = 41
        handed = _instants([_tick(0), _tick(100)], simulator)
        assert handed == [(0, (DataSliceEvent,)), (40, (ResolutionEvent,)),
                          (41, (ResolutionEvent,)), (100, (DataSliceEvent,))]

    def test_so_does_one_the_algo_sends_in_a_slice_that_resolved(self):
        simulator = _Simulator([100])
        simulator.send_at[100] = 101
        handed = _instants([_tick(0), _tick(100), _tick(200)], simulator)
        assert handed == [(0, (DataSliceEvent,)), (100, (DataSliceEvent, ResolutionEvent)),
                          (101, (ResolutionEvent,)), (200, (DataSliceEvent,))]


class TestTheTimers:

    def test_a_timer_every_interval_inside_a_quiet_stretch(self):
        handed = _instants([_tick(0), _tick(3500)], _Simulator(), timer_ms=1000)
        assert [stamp for stamp, _ in handed] == [0, 1000, 2000, 3000, 3500]

    def test_none_across_a_stretch_longer_than_the_threshold(self):
        handed = _instants([_tick(0), _tick(400_000)], _Simulator(), timer_ms=1000)
        assert [stamp for stamp, _ in handed] == [0, 400_000]

    def test_a_timer_and_a_due_time_on_one_stamp_are_one_instant(self):
        handed = _instants([_tick(0), _tick(2500)], _Simulator([1000]), timer_ms=1000)
        assert handed[1] == (1000, (TimeEvent, ResolutionEvent))
