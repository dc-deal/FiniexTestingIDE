"""
Ticks become data slices (#457): every tick of one instant in one event.

A sweep through the book prints several trades with one stamp. Today each runs its own algo pass at
the same clock time; as one slice, the broker path still sees every tick in order and the algo
computes once. This suite holds the grouping and the two stamps a slice carries.
"""

from datetime import datetime, timezone
from typing import List

from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.tick_slicer import (
    slice_of_tick,
    slice_ticks,
    tick_event_msc,
    tick_receipt_msc,
)
from python.framework.types.loop_types.event_kind_types import EventOrdering
from python.framework.types.loop_types.loop_event_types import DataSliceEvent
from python.framework.types.market_types.market_data_types import TickData


def _tick(time_msc: int, collected_msc: int = 0, bid: float = 50000.0,
          is_clipped: bool = False) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc),
        symbol='BTCUSD', bid=bid, ask=bid + 1.0, time_msc=time_msc,
        collected_msc=collected_msc, is_clipped=is_clipped)


def _slices(ticks: List[TickData], ordering: EventOrdering) -> List[DataSliceEvent]:
    return list(slice_ticks(ticks, ordering, EventSequencer()))


class TestOneInstantIsOneSlice:

    def test_a_sweep_through_the_book_is_one_slice(self):
        """The archive's own example (#457): three BTCUSD trades with one stamp, three prices."""
        sweep = [_tick(1784737896774, 1784737896774, bid=price)
                 for price in (89288.2, 89288.6, 89289.1)]

        slices = _slices(sweep, EventOrdering.BY_TS_INIT)

        assert len(slices) == 1
        assert slices[0].ticks == tuple(sweep), 'every price of the sweep, in arrival order'

    def test_a_new_stamp_starts_a_new_slice(self):
        ticks = [_tick(1_000), _tick(1_000), _tick(1_250), _tick(1_400), _tick(1_400)]

        slices = _slices(ticks, EventOrdering.BY_TS_EVENT)

        assert [len(each.ticks) for each in slices] == [2, 1, 2]
        assert [each.ts_event for each in slices] == [1_000, 1_250, 1_400]

    def test_a_slice_never_reaches_back(self):
        """Equal stamps that are not neighbours stay apart: the input is a stream, not a sort."""
        ticks = [_tick(1_000), _tick(1_250), _tick(1_000)]

        assert len(_slices(ticks, EventOrdering.BY_TS_EVENT)) == 3

    def test_a_clipped_tick_stays_in_its_slice(self):
        """Clipping skips the algo's work for an instant; it never drops data (#293's bug class)."""
        ticks = [_tick(1_000), _tick(1_000, is_clipped=True), _tick(1_000)]

        slices = _slices(ticks, EventOrdering.BY_TS_EVENT)

        assert len(slices) == 1
        assert sum(1 for tick in slices[0].ticks if tick.is_clipped) == 1

    def test_each_slice_takes_the_next_number(self):
        sequencer = EventSequencer()
        sequencer.next_seq()        # an event made before the slices

        slices = list(slice_ticks([_tick(1_000), _tick(2_000)], EventOrdering.BY_TS_EVENT,
                                  sequencer))

        assert [each.seq for each in slices] == [1, 2]


class TestTheOrderingDecidesTheGrouping:
    """Two trades the venue stamped alike, received 40 ms apart."""

    TICKS = [_tick(5_000, collected_msc=5_120), _tick(5_000, collected_msc=5_160)]

    def test_a_backtest_groups_by_the_source_stamp(self):
        slices = _slices(self.TICKS, EventOrdering.BY_TS_EVENT)

        assert len(slices) == 1
        assert (slices[0].ts_event, slices[0].ts_init) == (5_000, 5_160), (
            'the instant was complete when its last tick arrived')

    def test_a_live_session_groups_by_the_receipt_stamp(self):
        slices = _slices(self.TICKS, EventOrdering.BY_TS_INIT)

        assert [(each.ts_event, each.ts_init) for each in slices] == [(5_000, 5_120), (5_000, 5_160)]

    def test_a_receipt_slice_takes_the_latest_source_stamp_of_its_ticks(self):
        """Four trades received in one millisecond, their venue stamps in no particular order."""
        ticks = [_tick(980, collected_msc=1_000), _tick(960, collected_msc=1_000),
                 _tick(990, collected_msc=1_000), _tick(970, collected_msc=1_000)]

        slices = _slices(ticks, EventOrdering.BY_TS_INIT)

        assert [(each.ts_event, each.ts_init, len(each.ticks)) for each in slices] == [
            (990, 1_000, 4)]


class TestATicksTwoStamps:

    def test_the_source_stamp_is_the_venue_stamp_not_the_timestamp(self):
        """A tick whose timestamp differs from its venue stamp: the venue stamp is the source stamp."""
        tick = TickData(
            timestamp=datetime.fromtimestamp(1784737896.0, tz=timezone.utc), symbol='BTCUSD',
            bid=1.0, ask=2.0, time_msc=1784737896774, collected_msc=1784737896900)

        assert tick_event_msc(tick) == 1784737896774

    def test_a_tick_without_a_venue_stamp_takes_its_timestamp_to_the_millisecond(self):
        tick = TickData(
            timestamp=datetime(2026, 4, 27, 7, 38, 8, 773_000, tzinfo=timezone.utc),
            symbol='BTCUSD', bid=1.0, ask=1.0)

        assert tick_event_msc(tick) == 1777275488773

    def test_the_receipt_stamp_is_the_collection_stamp(self):
        assert tick_receipt_msc(_tick(1_000, collected_msc=1_080)) == 1_080

    def test_a_tick_recorded_without_its_arrival_was_received_when_it_happened(self):
        assert tick_receipt_msc(_tick(1_000)) == 1_000

    def test_a_tick_with_neither_stamp_takes_its_timestamp_for_both(self):
        """No venue stamp and no arrival: the receipt falls back through the source stamp."""
        tick = TickData(
            timestamp=datetime(2026, 4, 27, 7, 38, 8, 773_000, tzinfo=timezone.utc),
            symbol='BTCUSD', bid=1.0, ask=1.0)

        assert tick_receipt_msc(tick) == 1777275488773


class TestOneTickAsASlice:
    """A drive that hands the loop core one tick at a time (#458) makes a slice of each."""

    def test_a_tick_becomes_a_slice_of_its_own_with_both_stamps(self):
        tick = _tick(5_000, collected_msc=5_040)

        event = slice_of_tick(tick, EventSequencer())

        assert event.ticks == (tick,)
        assert (event.ts_event, event.ts_init, event.seq) == (5_000, 5_040, 0)

    def test_ticks_sharing_a_stamp_stay_apart(self):
        """Unlike `slice_ticks`: one slice per tick, so a pass per tick computes as before."""
        sequencer = EventSequencer()

        events = [slice_of_tick(tick, sequencer) for tick in (_tick(1_000), _tick(1_000))]

        assert [len(event.ticks) for event in events] == [1, 1]
        assert [event.seq for event in events] == [0, 1]
