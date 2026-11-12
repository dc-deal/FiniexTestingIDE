"""
FiniexTestingIDE - Tick Slicer (#457)

Turns a stream of ticks into data slices: every tick of one instant in one event. Also the one
place that says which of a tick's stamps is its source stamp and which its receipt stamp.
"""

from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, List, Optional

from python.framework.loop.event_sequencer import EventSequencer
from python.framework.types.loop_types.event_kind_types import EventOrdering
from python.framework.types.loop_types.loop_event_types import DataSliceEvent
from python.framework.types.market_types.market_data_types import TickData

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def tick_event_msc(tick: TickData) -> int:
    """
    A tick's source stamp — when it happened at its venue, in UTC epoch milliseconds (`ts_event`).

    The venue's own stamp, `time_msc`; a backtest's clock follows the same value, since a replayed
    tick's `timestamp` is derived from it. A tick that carries none (0 = not available) falls back
    to its `timestamp`, which every source sets, converted exactly rather than through a float.

    Args:
        tick: The tick

    Returns:
        Its source stamp
    """
    if tick.time_msc > 0:
        return tick.time_msc
    return (tick.timestamp - _EPOCH) // _ONE_MS


def tick_receipt_msc(tick: TickData) -> int:
    """
    A tick's receipt stamp, in UTC epoch milliseconds (`ts_init`).

    `collected_msc`, whose clock depends on where the tick came from:
    - an archived tick — every tick of a backtest or a mock session — carries the receipt of the
      collector that recorded it, not the clock of the run replaying it;
    - a live tick carries the session's own wall clock, read once per trade while a message is
      parsed, so one message holding several trades can carry more than one millisecond.

    A tick recorded without one (0 = not recorded, as in files written before the arrival time was
    kept) has no receipt of its own, and its source stamp stands in, so the two agree.

    Args:
        tick: The tick

    Returns:
        Its receipt stamp
    """
    if tick.collected_msc > 0:
        return tick.collected_msc
    return tick_event_msc(tick)


def slice_ticks(
    ticks: Iterable[TickData],
    ordering: EventOrdering,
    sequencer: EventSequencer,
) -> Iterator[DataSliceEvent]:
    """
    Bundle consecutive ticks that share the run's ordering stamp into one data slice each.

    The ordering stamp is the source stamp in a backtest, whose clock is data time, and the receipt
    stamp in a live session — so a backtest and a live session can cut the same ticks into
    different instants (#460 weighs that). Consecutive, not sorted: the input is a stream in
    arrival order, and a slice never reaches back past a tick with another stamp. Every tick lands
    in exactly one slice, a clipped one included — clipping skips the algo's work for an instant,
    it never drops data. For the stamp not ordering the run, the slice takes the latest of its
    ticks', since the instant was complete when its last tick was.

    Args:
        ticks: The ticks in arrival order
        ordering: The stamp that groups them — the one the run's timeline orders by
        sequencer: The run's sequencer; each slice takes the next number when it is made

    Returns:
        The slices, in the order of the stream
    """
    ordering_stamp = ordering.pick(tick_event_msc, tick_receipt_msc)
    group: List[TickData] = []
    group_stamp: Optional[int] = None
    for tick in ticks:
        stamp = ordering_stamp(tick)
        if group and stamp != group_stamp:
            yield _slice_of(group, sequencer)
            group = []
        group.append(tick)
        group_stamp = stamp
    if group:
        yield _slice_of(group, sequencer)


def slice_of_tick(tick: TickData, sequencer: EventSequencer) -> DataSliceEvent:
    """
    One tick as a data slice of its own, for a drive that hands the loop core one tick at a time.

    `slice_ticks` groups the ticks that share a stamp into one slice, so the algo computes once
    for all of them; a drive that keeps today's one pass per tick (#458) uses this instead, and
    its passes compute exactly as before.

    Args:
        tick: The tick
        sequencer: The run's sequencer; the slice takes its next number

    Returns:
        The slice, carrying the tick's two stamps
    """
    return DataSliceEvent(tick_event_msc(tick), tick_receipt_msc(tick), sequencer.next_seq(),
                          (tick,))


def _slice_of(group: List[TickData], sequencer: EventSequencer) -> DataSliceEvent:
    """
    Make the data slice of one instant's ticks.

    Args:
        group: The instant's ticks, at least one, in arrival order
        sequencer: The run's sequencer

    Returns:
        The slice, stamped with the latest source and receipt stamp among its ticks
    """
    return DataSliceEvent(
        ts_event=max(tick_event_msc(tick) for tick in group),
        ts_init=max(tick_receipt_msc(tick) for tick in group),
        seq=sequencer.next_seq(),
        ticks=tuple(group),
    )
