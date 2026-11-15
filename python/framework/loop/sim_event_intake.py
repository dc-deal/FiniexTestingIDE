"""
FiniexTestingIDE - Sim Event Intake (#460)

The backtest's timeline: the ticks, the simulator's due times and a heartbeat logic's timers,
merged by their stamp and handed over one instant at a time.
"""

from datetime import datetime, timezone
from typing import Iterator, List, Optional, Sequence, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.tick_slicer import tick_event_msc, tick_receipt_msc
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.market_types.market_data_types import TickData


class SimEventIntake:
    """
    The events of one backtest scenario, in the order its clock meets them.

    The backtest orders by when a tick arrived (#460): a tick's recorded arrival time, its event
    time where its file recorded none it can trust (data preparation clears those). Ticks that
    arrived in the same millisecond are one data slice. The simulator's next due time — a fill
    or a scheduled modify or cancel — is an instant of its own when it falls between two slices,
    and joins the slice when it falls on its stamp. A heartbeat logic gets a timer every interval
    inside a quiet stretch no longer than the gap threshold (#208), and none across a longer one.
    The intake asks for the due time again after every instant, so an order sent in one is due
    in time, and it ends with the last slice: nothing past the last tick comes due in the run.

    The merge compares stamps directly rather than through the event timeline of #457: a heap
    costs about 1.5 µs a tick where every tick is its own instant, the whole budget of #460
    (measured with `python/experiments/cost_probes/probe_sim_intake_cost.py`).

    Args:
        ticks: The scenario's ticks, in arrival order
        simulator: The scenario's executor, asked for its next due time
        sequencer: The scenario's sequencer; each event takes the next number when it is made
        timer_interval_ms: The heartbeat interval, or 0 when the logic takes no heartbeat
        gap_threshold_ms: The longest quiet stretch timers are made in
        logger: The scenario's log, told when a due time lies before the instant already handed
    """

    def __init__(
        self,
        ticks: Sequence[TickData],
        simulator: TradeSimulator,
        sequencer: EventSequencer,
        timer_interval_ms: int,
        gap_threshold_ms: float,
        logger: AbstractLogger,
    ):
        self._ticks = ticks
        self._simulator = simulator
        self._sequencer = sequencer
        self._timer_interval_ms = timer_interval_ms
        self._gap_threshold_ms = gap_threshold_ms
        self._logger = logger
        self._last_tick_index = -1
        self._late_due_count = 0

    def get_late_due_count(self) -> int:
        """
        How many due times lay before the instant already handed over, and resolved late.

        Returns:
            The count; 0 in a consistent run
        """
        return self._late_due_count

    def get_last_tick_index(self) -> int:
        """
        Where in the tick array the last tick handed over sits.

        Returns:
            Its index, or -1 before the first slice
        """
        return self._last_tick_index

    def __iter__(self) -> Iterator[Tuple[LoopEvent, ...]]:
        """
        The instants, one at a time; each is the tuple of events that share its stamp.

        Returns:
            The iterator; the caller runs each instant before asking for the next
        """
        ticks = self._ticks
        count = len(ticks)
        sequencer = self._sequencer
        simulator = self._simulator
        cursor = 0
        last_data_stamp: Optional[int] = None
        handed: Optional[int] = None
        # Where a due time was last handed over. A due time on that stamp again is one its instant
        # left standing, and it waits for the next slice: an order sent in an instant always comes
        # due later, because its latency is at least a millisecond and a modify or cancel takes
        # one (#460).
        resolved_at: Optional[int] = None
        # Each tick's ordering stamp is read once, as the walk reaches it. A file's arrival times
        # never step back (the import refuses that), but two files can meet with the later one's
        # first arrival before the earlier one's last; such a tick joins the running instant, as a
        # live session's clock moves forward only.
        data_stamp = tick_receipt_msc(ticks[0]) if count else 0
        while cursor < count:
            end = cursor + 1
            next_stamp: Optional[int] = None
            while end < count:
                stamp = tick_receipt_msc(ticks[end])
                if stamp > data_stamp:
                    next_stamp = stamp
                    break
                end += 1

            timers = self._timers_between(last_data_stamp, data_stamp)
            timer_index = 0
            while True:
                # The answer as given: once the loop ends, no instant has run since it was asked,
                # so the slice reuses it rather than asking again
                asked = due = simulator.peek_next_due_msc()
                if due is not None and handed is not None and due < handed:
                    # Nothing sent in an instant comes due before it — an order's latency is at
                    # least a millisecond — so the simulator's due times are inconsistent. The
                    # order resolves now, late, and the run says so (#460): an error, so the run
                    # ends as finished with errors; the first with its stamps, the rest counted
                    self._late_due_count += 1
                    if self._late_due_count == 1:
                        self._logger.error(
                            f'Due time {_utc_text(due)} lies before the instant already handed '
                            f'over ({_utc_text(handed)}) — it resolves now, {handed - due} ms '
                            f"late; the simulator's due times are inconsistent")
                    due = handed
                if due is not None and due == resolved_at:
                    due = None
                due_before = due is not None and due < data_stamp
                next_timer = timers[timer_index] if timer_index < len(timers) else None
                if not due_before and next_timer is None:
                    break
                stamp = min(due, next_timer) if due_before and next_timer is not None else (
                    due if due_before else next_timer)
                events: List[LoopEvent] = []
                if next_timer == stamp:
                    events.append(TimeEvent(stamp, stamp, sequencer.next_seq()))
                    timer_index += 1
                if due_before and due == stamp:
                    events.append(ResolutionEvent(stamp, stamp, sequencer.next_seq()))
                    resolved_at = stamp
                handed = stamp
                yield tuple(events)

            if end == cursor + 1:
                group: Tuple[TickData, ...] = (ticks[cursor],)
            else:
                group = tuple(ticks[cursor:end])
            data = DataSliceEvent(tick_event_msc(group[-1]), data_stamp, sequencer.next_seq(),
                                  group)
            self._last_tick_index = end - 1
            handed = data_stamp
            if asked is not None and asked <= data_stamp:
                resolved_at = data_stamp
                yield (data, ResolutionEvent(data_stamp, data_stamp, sequencer.next_seq()))
            else:
                yield (data,)
            last_data_stamp = data_stamp
            cursor = end
            if next_stamp is not None:
                data_stamp = next_stamp

    def _timers_between(self, previous: Optional[int], following: int) -> List[int]:
        """
        The timers a heartbeat logic gets in the quiet stretch between two slices.

        Args:
            previous: The earlier slice's stamp, or None before the first slice
            following: The later slice's stamp

        Returns:
            The timer stamps, earliest first; none without a heartbeat, before the first slice,
            or across a stretch longer than the gap threshold
        """
        interval = self._timer_interval_ms
        if interval <= 0 or previous is None:
            return []
        gap = following - previous
        if gap <= interval or gap > self._gap_threshold_ms:
            return []
        timers = []
        stamp = previous + interval
        while stamp < following:
            timers.append(stamp)
            stamp += interval
        return timers


def _utc_text(msc: int) -> str:
    """
    An epoch millisecond as a UTC time a reader can place.

    Args:
        msc: Milliseconds since the epoch, UTC

    Returns:
        The time, to the millisecond
    """
    return datetime.fromtimestamp(msc / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
