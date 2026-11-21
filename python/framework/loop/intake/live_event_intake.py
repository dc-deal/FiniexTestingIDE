"""
FiniexTestingIDE - Live Event Intake (#461)

The AutoTrader's timeline: the ticks on the market-data lane, the venue's answers on the execution
lane and the session's timers, merged by their stamp and handed over one instant at a time — the
loop waits until the next one is due and is woken when something arrives.
"""

import queue
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.intake.lane_arrivals import LaneArrivals
from python.framework.loop.intake.loop_wake import LoopWake
from python.framework.types.loop_types.live_intake_stats_types import LiveIntakeStats
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.loop_types.timer_kind_types import TimerKind
from python.framework.types.market_types.market_data_types import (
    TickArrival,
    tick_event_msc,
    tick_receipt_msc,
)

# What the wait took off the tick queue when it ran out without anything arriving
_NOTHING = object()


class LiveEventIntake:
    """
    The AutoTrader's timeline: what arrived on two lanes and what fell due, one instant at a time.

    Two lanes with two loss policies (#461), and one queue the loop blocks on — the tick queue,
    which every arrival wakes by itself and the execution lane wakes with a marker (`LoopWake`).
    The market-data lane is the tick queue, read whole on every pass; each item on it is one
    arrival — a venue message's trades live, an arrival millisecond in a replay — and is never
    split. Its backlog is coalesced into one slice so the algo computes once, on the newest tick,
    while the broker path and the bars see every tick; the execution lane is the executor's
    own inbox, which this never reads — it learns only that answers wait and since when, and hands
    over an instant whose resolution drains them, so no answer is ever dropped or merged.

    Strictly by time: a backlog is split at a timer's due time, so the reconciliation, the state
    persistence and a state frame's period run at their time even while the market is fast. The
    heartbeat is the exception, a gap timer that every tick and every heartbeat start again, so it
    runs only after an interval without ticks, as it always has; the venue's answers and the other
    timers do not hold it off. In a replay a timer falls due once the replay has reached it, never
    on a deadline the replay runs behind.

    Every stamp lies on the receipt axis — the wall clock live, a replay's own time in a mock — and
    never goes back: an arrival read later with an earlier stamp joins the running instant and is
    counted. Waits and lags are measured on the monotonic clock.
    """

    def __init__(
        self,
        tick_queue: queue.Queue,
        market_arrivals: LaneArrivals,
        execution_arrivals: LaneArrivals,
        sequencer: EventSequencer,
        axis_now: Callable[[], int],
        heartbeat_interval_ms: int,
        coalesce_backlog: bool,
        logger: AbstractLogger,
    ):
        """
        Args:
            tick_queue: The market-data lane, filled by the tick source's thread with one tuple
                of ticks per arrival; None ends it, and a `LoopWake` the execution lane put on it
                wakes the loop
            market_arrivals: What the tick source noted as it put a frame's ticks — the
                monotonic arrival a backlog's lag is measured from
            execution_arrivals: What the request processor noted as it put an answer in the
                executor's inbox
            sequencer: The session's counter for an event's submission order
            axis_now: The receipt axis now, in UTC epoch milliseconds — the wall clock live, the
                replay's own time in a mock
            heartbeat_interval_ms: The quiet interval after which a heartbeat falls due
            coalesce_backlog: True live — a backlog is lateness, and the algo computes once on
                its newest tick; False for a replay, whose queue fills because it runs faster
                than real time, not because the algo fell behind: each of its arrivals — the
                ticks of one arrival millisecond, as a backtest's slice — is a slice of its own,
                and never depends on how fast the machine is (#368)
            logger: The session's logger
        """
        self._tick_queue = tick_queue
        self._market_arrivals = market_arrivals
        self._execution_arrivals = execution_arrivals
        self._sequencer = sequencer
        self._axis_now = axis_now
        self._heartbeat_interval_s = heartbeat_interval_ms / 1000.0
        self._coalesce_backlog = coalesce_backlog
        self._logger = logger
        # The timers armed on a schedule of their own: due stamp on the axis, and its monotonic
        # deadline, by which the wait is measured
        self._armed: Dict[TimerKind, Tuple[int, float]] = {}
        self._last_handed: Optional[int] = None
        self._last_handed_mono = time.monotonic()
        # The last instant with ticks or the heartbeat: the quiet interval counts from it, so the
        # venue's answers and the other timers between two ticks never hold the heartbeat off
        self._quiet_since: Optional[int] = None
        self._quiet_since_mono = self._last_handed_mono
        # The two as one value, replaced whole, for a reader on another thread
        self._handed_at: Optional[Tuple[int, float]] = None
        # Each instant with what its slice says about lateness: the ticks the algo will not
        # compute on, and how long the oldest arrival waited
        self._ready: Deque[Tuple[Tuple[LoopEvent, ...], int, float]] = deque()
        self._handed_backlog: Tuple[int, float] = (0, 0.0)
        self._ended = False
        self._stats = LiveIntakeStats()

    def arm(self, timer: TimerKind, due_msc: int) -> None:
        """
        Schedule a timer at a stamp of the receipt axis, replacing one of its kind already armed.

        Args:
            timer: What it is for; the heartbeat schedules itself and is not armed here
            due_msc: When it falls due, on the receipt axis
        """
        if timer is TimerKind.HEARTBEAT:
            raise ValueError('The heartbeat is a gap timer the intake schedules itself')
        deadline_mono = time.monotonic() + (due_msc - self._axis_now()) / 1000.0
        self._armed[timer] = (due_msc, deadline_mono)

    def get_stats(self) -> LiveIntakeStats:
        """
        Returns:
            What the intake counted and measured so far
        """
        return self._stats

    def get_position_msc(self) -> Optional[int]:
        """
        Where the loop stands on the receipt axis: the last instant it was handed, advanced by the
        time since on the monotonic clock. Safe to read from another thread.

        A replay's own time is the front of its source — the last tick it put on the queue — and a
        loop that runs behind a replay stands earlier; a stamp taken from the front would move the
        clock past ticks still waiting, so a replay's session stamps by this instead.

        Returns:
            UTC epoch milliseconds; None before the first instant
        """
        handed_at = self._handed_at
        if handed_at is None:
            return None
        stamp, handed_mono = handed_at
        return stamp + round((time.monotonic() - handed_mono) * 1000)

    def get_handed_backlog(self) -> Tuple[int, float]:
        """
        What the instant handed over last says about lateness — the clipping monitor's input.

        Returns:
            The ticks of its slice the algo does not compute on — live, those of every arrival
            before the newest, which waited behind a busy loop; none in a replay, which is never
            behind — and how long the slice's oldest arrival waited before the loop took it, in
            milliseconds on the monotonic clock. (0, 0.0) for an instant without ticks
        """
        return self._handed_backlog

    def next_instant(self) -> Optional[Tuple[LoopEvent, ...]]:
        """
        Hand over the next instant, waiting until one is due or something arrives.

        Returns:
            The instant's events, all with one stamp; None once the market-data lane has ended and
            everything it held was handed over
        """
        while not self._ready:
            if self._ended:
                return None
            timeout = self._seconds_to_next_deadline()
            try:
                first = (self._tick_queue.get(timeout=timeout) if timeout > 0
                         else self._tick_queue.get_nowait())
            except queue.Empty:
                first = _NOTHING
            self._collect(first)
        events, skipped, wait_ms = self._ready.popleft()
        self._handed_backlog = (skipped, wait_ms)
        self._last_handed = events[0].ts_init
        self._last_handed_mono = time.monotonic()
        self._handed_at = (self._last_handed, self._last_handed_mono)
        if any(isinstance(event, DataSliceEvent)
               or (isinstance(event, TimeEvent) and event.timer is TimerKind.HEARTBEAT)
               for event in events):
            self._quiet_since = self._last_handed
            self._quiet_since_mono = self._last_handed_mono
        self._stats.instants += 1
        return events

    # =========================================================================
    # One pass over the lanes
    # =========================================================================

    def _seconds_to_next_deadline(self) -> float:
        """
        How long the loop may wait: until the earliest armed timer or the heartbeat.

        Returns:
            Seconds on the monotonic clock, zero when something is due already
        """
        deadline = self._quiet_since_mono + self._heartbeat_interval_s
        for _, armed_mono in self._armed.values():
            deadline = min(deadline, armed_mono)
        return max(0.0, deadline - time.monotonic())

    def _collect(self, first: object) -> None:
        """
        Read both lanes and the timers once, and queue the instants they make, in stamp order.

        Args:
            first: What the wait took off the tick queue — an arrival, a wake, the lane's end, or
                nothing when the wait ran out
        """
        arrivals = self._drain_arrivals(first)
        market = self._market_arrivals.take()
        execution = self._execution_arrivals.take()
        now_mono = time.monotonic()
        if not self._coalesce_backlog:
            # A replay — the one intake that never coalesces — runs at its own speed
            self._hold_timers_ahead_of_the_replay(arrivals, now_mono)
        due = sorted(((due_msc, kind) for kind, (due_msc, armed_mono) in self._armed.items()
                      if armed_mono <= now_mono), key=lambda item: item[0])
        for _, kind in due:
            del self._armed[kind]

        instants: List[Tuple[int, List[LoopEvent], List[TickArrival]]] = []
        cursor = 0
        for due_msc, kind in due:
            # The backlog up to the timer's due time leaves first, then the timer at its time; an
            # arrival is never split, and leaves before the timer when its newest tick does
            end = cursor
            while end < len(arrivals) and self._slice_stamp(arrivals[end:end + 1]) <= due_msc:
                end += 1
            for group in self._slices(arrivals[cursor:end]):
                instants.append((self._slice_stamp(group), [], group))
            cursor = end
            instants.append((due_msc, [self._timer(due_msc, kind)], []))
            self._stats.timers_fired += 1
        for group in self._slices(arrivals[cursor:]):
            instants.append((self._slice_stamp(group), [], group))

        if execution is not None:
            receipt, _, count = execution
            self._stats.execution_arrivals += count
            # The answers join the first instant at or after their receipt; their resolution runs
            # after its ticks. Arrived after everything else here, they are an instant of their own
            target = next((entry for entry in instants if entry[0] >= receipt), None)
            if target is None:
                instants.append((receipt, [], []))
                target = instants[-1]
            target[1].append(ResolutionEvent(receipt, receipt, self._sequencer.next_seq()))

        if (not any(slice_arrivals for _, _, slice_arrivals in instants)
                and self._heartbeat_is_due(now_mono)):
            # Due one interval after the last instant with ticks or the last heartbeat, and
            # stamped when it is read: a quiet stretch follows the axis — the wall clock live —
            # and never drifts behind it by what each heartbeat's own work took. Its due time is
            # capped by the reading: a replay whose source runs behind the loop reads before the
            # interval has passed on its own time, and a due past the reading would move the
            # clock a full interval ahead of the replay at every heartbeat; the forward-only
            # clamp holds it at the last instant instead. It never splits a backlog, so reading
            # it late orders nothing wrongly
            read_msc = self._axis_now()
            interval_ms = round(self._heartbeat_interval_s * 1000)
            due_msc = (read_msc if self._quiet_since is None
                       else min(read_msc, self._quiet_since + interval_ms))
            instants.append((read_msc, [self._timer(due_msc, TimerKind.HEARTBEAT, read_msc)], []))
            self._stats.timers_fired += 1

        lag_ms = (now_mono - market[1]) * 1000.0 if market is not None else 0.0
        for stamp, events, slice_arrivals in instants:
            self._queue_instant(stamp, events, slice_arrivals, lag_ms)

    def _hold_timers_ahead_of_the_replay(self, arrivals: List[TickArrival],
                                         now_mono: float) -> None:
        """
        Keep a replay's timer from falling due before the replay has reached it. A timer's deadline
        assumes the axis moves at real speed; a replay the source throttles runs behind it, and a
        timer fired on that deadline would stamp its due ahead of ticks not yet put — moving the
        clock, and a day boundary, past them. A deadline that passed before the timeline reached
        the due — the newest tick this pass took, or the last instant handed — moves by the
        distance still ahead.

        Args:
            arrivals: The arrivals this pass took
            now_mono: The pass's monotonic reading
        """
        if not any(armed_mono <= now_mono for _, armed_mono in self._armed.values()):
            return
        reached = self._slice_stamp(arrivals) if arrivals else None
        if self._last_handed is not None:
            reached = self._last_handed if reached is None else max(reached, self._last_handed)
        if reached is None:
            return
        for kind, (due_msc, armed_mono) in list(self._armed.items()):
            if armed_mono <= now_mono and due_msc > reached:
                self._armed[kind] = (due_msc, now_mono + (due_msc - reached) / 1000.0)

    @staticmethod
    def _slice_stamp(group: List[TickArrival]) -> int:
        """
        A slice's stamp: its newest receipt — in a replay the receipt of the tick that opened its
        arrival millisecond, which a straggler joining it never lowers, as a backtest stamps its
        slice; live the last arrival's newest tick.

        Args:
            group: The slice's arrivals

        Returns:
            The stamp on the receipt axis
        """
        return max(tick_receipt_msc(tick) for arrival in group for tick in arrival)

    def _slices(self, arrivals: List[TickArrival]) -> List[List[TickArrival]]:
        """
        Cut a run of arrivals into slices: the whole backlog live, each arrival on its own in a
        replay — there one arrival is the ticks of one arrival millisecond, as in a backtest.

        Args:
            arrivals: Arrivals in the order they were put, between two timers

        Returns:
            The slices, none for no arrivals
        """
        if not arrivals:
            return []
        if self._coalesce_backlog:
            return [arrivals]
        return [[arrival] for arrival in arrivals]

    def _drain_arrivals(self, first: object) -> List[TickArrival]:
        """
        Take every arrival the market-data lane holds now, in the order they were put; wakes are
        dropped.

        Args:
            first: What the wait already took off the lane

        Returns:
            The arrivals; the lane's end is noted when its sentinel is read
        """
        arrivals: List[TickArrival] = []
        item = first
        while True:
            if item is None:
                self._ended = True
                return arrivals
            if isinstance(item, tuple):
                if item:
                    arrivals.append(item)
            elif item is not _NOTHING and not isinstance(item, LoopWake):
                raise TypeError(
                    f'The tick queue holds one tuple of ticks per arrival, a wake or its end; '
                    f'got {type(item).__name__}')
            try:
                item = self._tick_queue.get_nowait()
            except queue.Empty:
                return arrivals

    def _heartbeat_is_due(self, now_mono: float) -> bool:
        """
        Whether a heartbeat interval has passed since the last instant with ticks or the last
        heartbeat.

        Args:
            now_mono: The pass's monotonic reading

        Returns:
            True when the heartbeat falls due
        """
        return now_mono - self._quiet_since_mono >= self._heartbeat_interval_s

    def _timer(self, due_msc: int, kind: TimerKind, read_msc: Optional[int] = None) -> TimeEvent:
        """
        A timer made as it falls due: never stamped before its due time.

        Args:
            due_msc: When it fell due, on the receipt axis — its `ts_event`
            kind: What it is for
            read_msc: When it was read, where that is its stamp — the heartbeat's; a timer that
                splits a backlog stands at its due time

        Returns:
            The event
        """
        stamp = due_msc if read_msc is None else max(read_msc, due_msc)
        return TimeEvent(due_msc, stamp, self._sequencer.next_seq(), kind)

    def _queue_instant(self, stamp: int, events: List[LoopEvent], arrivals: List[TickArrival],
                       lag_ms: float) -> None:
        """
        Queue one instant at its stamp, never earlier than the instant before it.

        Args:
            stamp: Its stamp on the receipt axis, before the forward-only clamp
            events: Its timer and resolution events
            arrivals: Its slice's arrivals, possibly none
            lag_ms: How long the oldest arrival of this pass waited, on the monotonic clock; at
                most that for a slice after the first of one pass over the lanes
        """
        ticks = [tick for arrival in arrivals for tick in arrival]
        floor = self._ready[-1][0][0].ts_init if self._ready else self._last_handed
        if floor is not None and stamp < floor:
            # An arrival read later and stamped earlier — the threads' order, not the market's.
            # A timer that fell due while the loop was busy is late, not reordered
            if ticks or any(isinstance(event, ResolutionEvent) for event in events):
                self._stats.reordered_stamps += 1
            stamp = floor
        instant: List[LoopEvent] = []
        skipped = 0
        wait_ms = 0.0
        if ticks:
            instant.append(DataSliceEvent(tick_event_msc(ticks[-1]), stamp,
                                          self._sequencer.next_seq(), tuple(ticks)))
            self._stats.max_slice_ticks = max(self._stats.max_slice_ticks, len(ticks))
            wait_ms = lag_ms
            if len(arrivals) > 1:
                # The backlog: every arrival before the newest waited behind the busy loop, and
                # the algo computes on the newest tick alone. The newest arrival's own ticks came
                # together — one venue message — so nothing held them up
                self._stats.coalesced_slices += 1
                skipped = len(ticks) - len(arrivals[-1])
        for event in events:
            event.ts_init = stamp
            instant.append(event)
        self._ready.append((tuple(instant), skipped, wait_ms))
