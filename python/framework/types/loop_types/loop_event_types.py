"""
FiniexTestingIDE - Loop Events (#457)

The typed events of the loop's one timeline. Each carries two stamps and its submission order;
the subclass says what happened and carries what the work of its kind needs.
"""

from dataclasses import dataclass
from typing import ClassVar, Tuple

from python.framework.types.loop_types.event_kind_types import EventKind
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.signal_data_types import SignalSnapshot


@dataclass(slots=True)
class LoopEvent:
    """
    One thing that happened, placed on the loop's one timeline.

    Two stamps travel on every event: the stamp it carries from its source, and the stamp this
    process gave it when it received or made it. Which of the two orders the timeline is the run's
    `EventOrdering`; the other stays as data. Both are UTC epoch milliseconds, the resolution the
    archive records ticks in, so the timeline compares integers. Not instantiated itself — every
    event is one of the kinds below.

    Not frozen, and nothing writes one after it is made: one is built for every instant a
    backtest replays, and the frozen form cost more than the loop core's whole budget per tick
    (measured for #458, `python/experiments/cost_probes/probe_loop_dispatch_cost.py`).

    Args:
        ts_event: Its source stamp — a tick's venue stamp, a timer's due instant
        ts_init: Its receipt stamp — a tick's collection stamp, the instant an event was made
        seq: Its submission order, from the run's `EventSequencer`: events with an equal stamp
            leave in this order. It breaks ties after the stamp; unlike an order event's `seq`, it
            is not the order of a stream on its own
    """
    KIND: ClassVar[EventKind]

    ts_event: int
    ts_init: int
    seq: int

    @property
    def kind(self) -> EventKind:
        """
        What kind of event this is — the source it came from.

        Returns:
            The kind its class declares
        """
        return self.KIND


@dataclass(slots=True)
class DataSliceEvent(LoopEvent):
    """
    Every tick of one instant, in the order they arrived.

    A sweep through the book prints several trades with one stamp; nothing can happen between
    them, so they are one instant. The broker path sees every tick of the slice in order — a stop
    triggering at the sweep's extreme must not be hidden — and the algo computes once, on the
    state after the last.

    Args:
        ticks: The instant's ticks, at least one
    """
    KIND: ClassVar[EventKind] = EventKind.DATA_SLICE

    ticks: Tuple[TickData, ...]

    def __post_init__(self) -> None:
        """
        Refuse a slice without a tick: an instant without data carries no data slice.
        """
        if not self.ticks:
            raise ValueError('A data slice holds at least one tick')


@dataclass(slots=True)
class TimeEvent(LoopEvent):
    """
    A timer or a heartbeat falls due at its instant, with or without data at the same instant.

    Where a data slice shares the instant, the algo has already computed in it: the timer's
    cadence work — reconciliation, state persistence, the staleness check — still runs, and the
    heartbeat pass does not (#457, a timer coinciding with a data slice). Its `ts_event` is the
    instant it is due at; in a replay that instant lies on the replay's own time, never on the
    machine's clock.
    """
    KIND: ClassVar[EventKind] = EventKind.TIME


@dataclass(slots=True)
class ResolutionEvent(LoopEvent):
    """
    A pending order at the broker comes due.

    It names no order: which orders are due at its instant, and in which order they resolve, is
    the pending-order manager's answer, and a fill resolves at the prices of the instant it lands
    in.
    """
    KIND: ClassVar[EventKind] = EventKind.RESOLUTION


@dataclass(slots=True)
class SignalEvent(LoopEvent):
    """
    An external signal snapshot becomes visible to the run.

    Args:
        snapshot: The snapshot. It becomes visible where today's signal gate admits it: at its
            resolution key (`SignalSnapshot.get_resolution_key` — `available_msc`, else
            `collected_msc` for snapshots from before that field), held so that no snapshot is
            visible before the one preceding it. Which stamps the event carries as `ts_event`
            and `ts_init` is set where signal events are made (#463)
    """
    KIND: ClassVar[EventKind] = EventKind.SIGNAL

    snapshot: SignalSnapshot
