"""
The loop's event vocabulary (#457): the kinds, the events that carry them, which stamp orders a
run, and the phases of an instant.

Nothing in the loops uses these yet; what is held here is the contract the dispatcher (#458) and
the two drivers will be built on, so it is readable in one place before anything depends on it.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from python.framework.types.loop_types import loop_event_types
from python.framework.types.loop_types.event_kind_types import EventKind, EventOrdering
from python.framework.types.loop_types.instant_phase_types import InstantPhase
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.market_types.market_data_types import TickData


def _tick(msc: int) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(msc / 1000, tz=timezone.utc),
        symbol='BTCUSD', bid=50000.0, ask=50001.0, time_msc=msc, collected_msc=msc)


class TestEveryKindHasItsEvent:
    """Each kind of the enum is carried by exactly one event class, and each class names a kind."""

    def test_the_kinds_and_the_event_classes_match_both_ways(self):
        # `dataclass(slots=True)` builds each class anew, and the class it replaced can still be
        # listed as a subclass; the one the module exports is the one that counts
        event_classes = [event_class for event_class in LoopEvent.__subclasses__()
                         if getattr(loop_event_types, event_class.__name__, None) is event_class]
        declared = [event_class.KIND for event_class in event_classes]

        assert sorted(declared, key=lambda kind: kind.value) == sorted(
            EventKind, key=lambda kind: kind.value), (
            'a kind without an event class, or two classes for one kind')

    def test_an_event_says_its_kind(self):
        assert TimeEvent(ts_event=1, ts_init=1, seq=0).kind is EventKind.TIME
        assert ResolutionEvent(ts_event=1, ts_init=1, seq=0).kind is EventKind.RESOLUTION
        assert DataSliceEvent(ts_event=1, ts_init=1, seq=0, ticks=(_tick(1),)).kind \
            is EventKind.DATA_SLICE


class TestEachOrderingNamesItsStamp:
    """
    Which stamp a run orders by is said in one place, `EventOrdering.pick`; the timeline and the
    slicer both ask it.
    """

    def test_every_ordering_picks_one_of_the_two(self):
        for ordering in EventOrdering:
            assert ordering.pick('source', 'receipt') in ('source', 'receipt'), ordering

    def test_a_backtest_goes_by_the_source_stamp_and_a_live_session_by_its_receipt(self):
        assert EventOrdering.BY_TS_EVENT.pick('source', 'receipt') == 'source'
        assert EventOrdering.BY_TS_INIT.pick('source', 'receipt') == 'receipt'


class TestAnEventIsFinalOnceMade:
    """Stamps and seq are fixed when an event is made; the timeline never rewrites them."""

    def test_an_event_cannot_be_changed(self):
        event = TimeEvent(ts_event=1_000, ts_init=1_000, seq=3)

        with pytest.raises(FrozenInstanceError):
            event.ts_event = 2_000  # type: ignore[misc]

    def test_a_slice_without_a_tick_is_refused(self):
        """An instant without data carries no data slice."""
        with pytest.raises(ValueError):
            DataSliceEvent(ts_event=1, ts_init=1, seq=0, ticks=())


class TestThePhasesOfAnInstant:
    """
    The order of the phases is the contract (#457): the world moves first, so a fill due in the
    same millisecond as a tick fills at that tick's price.
    """

    def test_the_declaration_order_is_the_phase_order(self):
        assert list(InstantPhase) == [
            InstantPhase.MARKET_DATA,
            InstantPhase.SIGNALS,
            InstantPhase.BROKER_RESOLUTIONS,
            InstantPhase.EVENT_DELIVERY,
            InstantPhase.ALGO_PASS,
            InstantPhase.ORDER_EXECUTION,
            InstantPhase.TRAILING_DRAIN,
        ]

    def test_prices_are_applied_before_any_resolution(self):
        phases = list(InstantPhase)

        assert phases.index(InstantPhase.MARKET_DATA) < phases.index(
            InstantPhase.BROKER_RESOLUTIONS) < phases.index(InstantPhase.ALGO_PASS)
