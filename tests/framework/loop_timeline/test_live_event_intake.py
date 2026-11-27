"""
The AutoTrader's intake (#461): two lanes and the session's timers, handed over one instant at a
time, strictly by stamp, never going back.
"""

import queue
import threading
import time
from datetime import datetime, timezone
from typing import List

import pytest

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.intake.lane_arrivals import LaneArrivals
from python.framework.loop.intake.live_event_intake import LiveEventIntake
from python.framework.loop.intake.loop_wake import LoopWake
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.loop_types.timer_kind_types import TimerKind
from python.framework.types.market_types.market_data_types import TickData

_AXIS = 1_784_000_000_000


class _Lanes:
    """The two lanes and the axis a test drives by hand."""

    def __init__(self, heartbeat_interval_ms: int = 60_000, coalesce_backlog: bool = True):
        self.ticks = queue.Queue()
        self.wake = LoopWake(self.ticks)
        self.market = LaneArrivals()
        self.execution = LaneArrivals()
        self.axis = [_AXIS]
        self.intake = LiveEventIntake(
            self.ticks, self.market, self.execution, EventSequencer(),
            axis_now=lambda: self.axis[0], heartbeat_interval_ms=heartbeat_interval_ms,
            coalesce_backlog=coalesce_backlog, logger=get_global_logger())

    def put(self, *receipts: int) -> None:
        """Put ticks received at these stamps, each in a message of its own, then wake."""
        for receipt in receipts:
            self.ticks.put((_tick(receipt),))
        self.market.note(receipts[-1])

    def put_arrival(self, *receipts: int) -> None:
        """Put ticks that arrived together — one venue message, one replayed millisecond."""
        self.ticks.put(tuple(_tick(receipt) for receipt in receipts))
        self.market.note(receipts[-1])

    def answer(self, receipt: int) -> None:
        """An answer from the venue lands in the executor's inbox."""
        self.execution.note(receipt)
        self.wake.notify()

    def end(self) -> None:
        self.ticks.put(None)

    def instants(self, count: int) -> List[tuple]:
        return [self.intake.next_instant() for _ in range(count)]


def _tick(receipt: int) -> TickData:
    return TickData(timestamp=datetime.fromtimestamp(receipt / 1000, tz=timezone.utc),
                    symbol='BTCUSD', bid=100.0, ask=100.1, time_msc=receipt - 20,
                    collected_msc=receipt)


def _receipts(event: DataSliceEvent) -> List[int]:
    return [tick.collected_msc for tick in event.ticks]


class TestTheMarketDataLane:

    def test_a_backlog_is_one_slice_the_algo_computes_on_once(self):
        lanes = _Lanes()
        lanes.put(_AXIS + 1, _AXIS + 2, _AXIS + 5)

        (instant,) = lanes.instants(1)

        (data,) = instant
        assert _receipts(data) == [_AXIS + 1, _AXIS + 2, _AXIS + 5]
        assert data.ts_init == _AXIS + 5
        stats = lanes.intake.get_stats()
        assert (stats.coalesced_slices, stats.max_slice_ticks) == (1, 3)
        assert lanes.intake.get_handed_backlog()[0] == 2, 'the two arrivals before the newest'

    def test_the_ticks_of_one_arrival_are_one_slice_and_no_backlog(self):
        lanes = _Lanes()
        lanes.put_arrival(_AXIS + 1, _AXIS + 1)

        lanes.instants(1)

        assert lanes.intake.get_stats().coalesced_slices == 0
        assert lanes.intake.get_handed_backlog()[0] == 0

    def test_a_venue_message_read_across_a_millisecond_is_no_backlog(self):
        """Its trades are stamped as they are parsed; it still arrived at once, behind nothing."""
        lanes = _Lanes()
        lanes.put_arrival(_AXIS + 1, _AXIS + 1, _AXIS + 2)

        (instant,) = lanes.instants(1)

        assert instant[0].ts_init == _AXIS + 2
        assert lanes.intake.get_stats().coalesced_slices == 0
        assert lanes.intake.get_handed_backlog()[0] == 0

    def test_the_messages_waiting_behind_a_pass_are_the_backlog(self):
        """Read in one millisecond after a busy pass, they still count by message."""
        lanes = _Lanes()
        lanes.put_arrival(_AXIS + 7, _AXIS + 7)
        lanes.put_arrival(_AXIS + 7)
        lanes.put_arrival(_AXIS + 7, _AXIS + 7, _AXIS + 7)

        lanes.instants(1)

        assert lanes.intake.get_handed_backlog()[0] == 3, 'the first two messages, not the newest'

    def test_a_replay_hands_each_arrival_as_its_own_slice(self):
        """A replay's queue fills because it runs faster than real time, not because it is late."""
        lanes = _Lanes(coalesce_backlog=False)
        lanes.put_arrival(_AXIS + 1, _AXIS + 1)
        lanes.put_arrival(_AXIS + 2)
        lanes.put_arrival(_AXIS + 5)

        slices = [instant[0] for instant in lanes.instants(3)]

        assert [_receipts(event) for event in slices] == [
            [_AXIS + 1, _AXIS + 1], [_AXIS + 2], [_AXIS + 5]]
        assert lanes.intake.get_stats().coalesced_slices == 0
        assert lanes.intake.get_handed_backlog()[0] == 0

    def test_a_straggler_keeps_its_replay_slices_stamp(self):
        """The receipt of the tick that opened the slice, as a backtest stamps it."""
        lanes = _Lanes(coalesce_backlog=False)
        lanes.put_arrival(_AXIS + 0)
        lanes.put_arrival(_AXIS + 15, _AXIS + 7)

        first, second = lanes.instants(2)

        assert first[0].ts_init == _AXIS + 0
        assert second[0].ts_init == _AXIS + 15
        assert _receipts(second[0]) == [_AXIS + 15, _AXIS + 7]
        assert lanes.intake.get_stats().reordered_stamps == 0

    def test_a_single_tick_on_the_lane_is_refused(self):
        """A tick put on its own would be read as nothing; the lane says so instead."""
        lanes = _Lanes()
        lanes.ticks.put(_tick(_AXIS + 1))

        with pytest.raises(TypeError):
            lanes.intake.next_instant()

    def test_the_lane_ends_after_its_last_ticks(self):
        lanes = _Lanes()
        lanes.put(_AXIS + 1)
        lanes.end()

        assert isinstance(lanes.intake.next_instant()[0], DataSliceEvent)
        assert lanes.intake.next_instant() is None


class TestTimersRunAtTheirTime:

    def test_a_due_timer_splits_the_backlog_at_its_due_time(self):
        lanes = _Lanes()
        lanes.axis[0] = _AXIS + 10
        lanes.intake.arm(TimerKind.RECONCILE, _AXIS + 3)
        lanes.put(_AXIS + 1, _AXIS + 2, _AXIS + 4, _AXIS + 6)

        first, timer, second = lanes.instants(3)

        assert _receipts(first[0]) == [_AXIS + 1, _AXIS + 2]
        assert isinstance(timer[0], TimeEvent) and timer[0].timer is TimerKind.RECONCILE
        assert timer[0].ts_init == timer[0].ts_event == _AXIS + 3
        assert _receipts(second[0]) == [_AXIS + 4, _AXIS + 6]

    def test_a_due_timer_never_splits_an_arrival(self):
        """A venue message whose trades straddle the due leaves whole, after the timer."""
        lanes = _Lanes(heartbeat_interval_ms=1_000)
        lanes.axis[0] = _AXIS + 10
        lanes.intake.arm(TimerKind.RECONCILE, _AXIS + 3)
        lanes.put_arrival(_AXIS + 1)
        lanes.put_arrival(_AXIS + 2, _AXIS + 4)

        first, timer, second = lanes.instants(3)

        assert _receipts(first[0]) == [_AXIS + 1]
        assert isinstance(timer[0], TimeEvent) and timer[0].timer is TimerKind.RECONCILE
        assert timer[0].ts_init == timer[0].ts_event == _AXIS + 3
        assert _receipts(second[0]) == [_AXIS + 2, _AXIS + 4]
        assert second[0].ts_init == _AXIS + 4
        assert lanes.intake.get_handed_backlog()[0] == 0

    def test_a_timer_is_never_stamped_before_its_due_time(self):
        lanes = _Lanes()
        lanes.put(_AXIS + 9)
        lanes.instants(1)
        lanes.intake.arm(TimerKind.PERSISTENCE, _AXIS - 1_000)

        (timer,) = lanes.intake.next_instant()

        assert timer.ts_init >= timer.ts_event
        assert timer.ts_init == _AXIS + 9
        assert lanes.intake.get_stats().reordered_stamps == 0

    def test_the_heartbeat_falls_due_after_a_quiet_interval(self):
        lanes = _Lanes(heartbeat_interval_ms=20)
        lanes.put(_AXIS + 1)
        lanes.instants(1)
        lanes.axis[0] = _AXIS + 1 + 20          # the axis moves on, as a wall clock does

        (heartbeat,) = lanes.intake.next_instant()

        assert heartbeat.timer is TimerKind.HEARTBEAT
        assert heartbeat.ts_init == heartbeat.ts_event == _AXIS + 1 + 20

    def test_a_heartbeat_read_before_its_interval_holds_at_the_last_instant(self):
        """A replay its source runs behind: the heartbeat never moves the clock ahead of it."""
        lanes = _Lanes(heartbeat_interval_ms=20, coalesce_backlog=False)
        lanes.put(_AXIS + 1)
        lanes.instants(1)                        # the axis stays at _AXIS: the replay is behind

        (heartbeat,) = lanes.intake.next_instant()

        assert heartbeat.ts_init == _AXIS + 1
        assert heartbeat.ts_event <= heartbeat.ts_init
        assert lanes.intake.get_stats().reordered_stamps == 0

    def test_the_venues_answers_do_not_hold_the_heartbeat_off(self):
        """Answers keep coming between two ticks; the heartbeat falls due as it always did."""
        lanes = _Lanes(heartbeat_interval_ms=100)
        lanes.put(_AXIS + 1)
        lanes.instants(1)
        stop = threading.Event()

        def answer_every_30_ms() -> None:
            # The axis follows the time that passes, as a wall clock does; an answer every third
            # step
            step = 0
            while not stop.wait(0.01):
                lanes.axis[0] += 10
                step += 1
                if step % 3 == 0:
                    lanes.answer(lanes.axis[0])

        threading.Thread(target=answer_every_30_ms, daemon=True).start()
        try:
            heartbeat = None
            for _ in range(40):
                instant = lanes.intake.next_instant()
                heartbeat = next((event for event in instant if isinstance(event, TimeEvent)), None)
                if heartbeat is not None:
                    break
        finally:
            stop.set()

        assert heartbeat is not None and heartbeat.timer is TimerKind.HEARTBEAT
        assert _AXIS + 1 + 60 <= heartbeat.ts_event <= _AXIS + 1 + 100, (
            'due one interval after the last tick, whatever answered in between')

    def test_the_heartbeat_joins_a_pass_that_holds_only_answers(self):
        """An answer in every pass over the lanes: the heartbeat still falls due beside it."""
        lanes = _Lanes(heartbeat_interval_ms=20)
        lanes.put(_AXIS + 1)
        lanes.instants(1)
        heartbeat = None
        for _ in range(20):
            time.sleep(0.005)
            lanes.axis[0] += 5
            lanes.answer(lanes.axis[0])
            instant = lanes.intake.next_instant()
            heartbeat = heartbeat or next(
                (event for event in instant if isinstance(event, TimeEvent)), None)

        assert heartbeat is not None and heartbeat.timer is TimerKind.HEARTBEAT, (
            'a heartbeat that waits for a pass with nothing in it never comes while answers do')

    def test_a_steady_stream_never_lets_the_heartbeat_fall_due(self):
        lanes = _Lanes(heartbeat_interval_ms=200)
        handed = []
        for step in range(6):
            lanes.put(_AXIS + step)
            handed.extend(lanes.intake.next_instant())
            time.sleep(0.05)

        assert all(isinstance(event, DataSliceEvent) for event in handed)

    def test_a_replays_timer_waits_until_the_replay_has_reached_it(self):
        """The deadline passed, but the replay has not reached the due: no clock ahead of it."""
        lanes = _Lanes(coalesce_backlog=False)
        lanes.put(_AXIS + 1)
        lanes.instants(1)
        lanes.axis[0] = _AXIS + 10
        lanes.intake.arm(TimerKind.RECONCILE, _AXIS + 5)
        lanes.put(_AXIS + 2)

        (first,) = lanes.instants(1)
        time.sleep(0.01)
        lanes.put(_AXIS + 4, _AXIS + 6)
        rest = lanes.instants(3)

        assert isinstance(first[0], DataSliceEvent) and first[0].ts_init == _AXIS + 2
        assert [instant[0].ts_init for instant in rest] == [_AXIS + 4, _AXIS + 5, _AXIS + 6]
        assert isinstance(rest[1][0], TimeEvent) and rest[1][0].timer is TimerKind.RECONCILE

    def test_the_heartbeat_schedules_itself(self):
        with pytest.raises(ValueError):
            _Lanes().intake.arm(TimerKind.HEARTBEAT, _AXIS)


class TestTheExecutionLane:

    def test_answers_join_the_first_instant_at_or_after_their_receipt(self):
        lanes = _Lanes()
        lanes.answer(_AXIS + 2)
        lanes.put(_AXIS + 1, _AXIS + 3)

        (instant,) = lanes.instants(1)

        assert [type(event) for event in instant] == [DataSliceEvent, ResolutionEvent]
        assert instant[1].ts_event == _AXIS + 2 and instant[1].ts_init == _AXIS + 3

    def test_an_answer_with_nothing_else_is_an_instant_of_its_own(self):
        lanes = _Lanes()
        lanes.answer(_AXIS + 7)

        (instant,) = lanes.instants(1)

        assert [type(event) for event in instant] == [ResolutionEvent]
        assert lanes.intake.get_stats().execution_arrivals == 1

    def test_answers_are_never_merged_away_only_noted_together(self):
        """Two answers arrive in one pass: one resolution drains both from the inbox, both count."""
        lanes = _Lanes()
        lanes.answer(_AXIS + 4)
        lanes.answer(_AXIS + 2)

        (instant,) = lanes.instants(1)

        assert [type(event) for event in instant] == [ResolutionEvent]
        assert instant[0].ts_event == _AXIS + 2, 'the earlier receipt'
        assert lanes.intake.get_stats().execution_arrivals == 2

    def test_an_answer_wakes_the_loop_at_once(self):
        lanes = _Lanes(heartbeat_interval_ms=60_000)
        threading.Timer(0.02, lambda: lanes.answer(_AXIS + 1)).start()
        started = time.monotonic()

        lanes.intake.next_instant()

        assert time.monotonic() - started < 5.0


class TestTheLoopsPosition:

    def test_there_is_none_before_the_first_instant(self):
        assert _Lanes().intake.get_position_msc() is None

    def test_it_is_the_last_instant_handed_advanced_by_the_time_since(self):
        lanes = _Lanes()
        lanes.put(_AXIS + 7)
        lanes.instants(1)
        time.sleep(0.02)

        position = lanes.intake.get_position_msc()

        assert _AXIS + 7 + 15 <= position < _AXIS + 7 + 5_000


class TestTheStampsNeverGoBack:

    def test_a_wall_clock_stepped_back_moves_no_stamp_back(self):
        """NTP steps the clock back a minute: a tick and a heartbeat read on it hold at the last
        instant."""
        lanes = _Lanes(heartbeat_interval_ms=20)
        lanes.put(_AXIS + 100)
        lanes.instants(1)
        lanes.axis[0] = _AXIS - 60_000
        lanes.put(_AXIS - 59_990)

        (data,) = lanes.intake.next_instant()
        (heartbeat,) = lanes.intake.next_instant()

        assert data.ts_init == _AXIS + 100
        assert heartbeat.timer is TimerKind.HEARTBEAT
        assert heartbeat.ts_init == _AXIS + 100, 'it holds at the last instant'
        assert heartbeat.ts_event <= heartbeat.ts_init
        assert lanes.intake.get_stats().reordered_stamps == 1

    def test_an_arrival_stamped_before_the_last_instant_joins_and_is_counted(self):
        lanes = _Lanes()
        lanes.put(_AXIS + 10)
        lanes.instants(1)
        lanes.put(_AXIS + 4)

        (instant,) = lanes.instants(1)

        assert instant[0].ts_init == _AXIS + 10
        assert lanes.intake.get_stats().reordered_stamps == 1
