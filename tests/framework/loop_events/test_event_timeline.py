"""
The loop's one timeline (#457): earliest stamp first, then submission order, never a kind first.

The stamp is `ts_event` in a backtest and `ts_init` live. Events sharing a stamp are one instant
and leave together, in the order they were submitted, and time handed over never runs backwards.
"""

import random
from datetime import datetime, timezone
from typing import List

import pytest

from python.framework.exceptions.loop_event_errors import PastLoopEventError
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.event_timeline import EventTimeline
from python.framework.loop.tick_slicer import slice_ticks
from python.framework.types.loop_types.event_kind_types import EventKind, EventOrdering
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    ResolutionEvent,
    TimeEvent,
)
from python.framework.types.market_types.market_data_types import TickData


def _tick(msc: int, is_clipped: bool = False) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(msc / 1000, tz=timezone.utc),
        symbol='BTCUSD', bid=50000.0, ask=50001.0, time_msc=msc, collected_msc=msc,
        is_clipped=is_clipped)


def _drain(timeline: EventTimeline) -> List[LoopEvent]:
    events = []
    while (event := timeline.pop()) is not None:
        events.append(event)
    return events


class TestTheEarliestStampLeavesFirst:

    def test_events_pushed_in_any_order_leave_in_stamp_order(self):
        """Fifty shuffles of one set of events, each drained in the same order."""
        sequencer = EventSequencer()
        events = [TimeEvent(ts_event=stamp, ts_init=stamp, seq=sequencer.next_seq())
                  for stamp in range(0, 10_000, 250)]
        expected = sorted(events, key=lambda event: (event.ts_event, event.seq))
        shuffler = random.Random(457)
        for _ in range(50):
            pushed = list(events)
            shuffler.shuffle(pushed)
            timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
            for event in pushed:
                timeline.push(event)

            assert _drain(timeline) == expected

    def test_peek_leaves_the_event_in_place(self):
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        timeline.push(TimeEvent(ts_event=500, ts_init=500, seq=0))

        assert timeline.peek() is timeline.peek()
        assert len(timeline) == 1

    def test_an_empty_timeline_answers_nothing(self):
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)

        assert timeline.peek() is None
        assert timeline.pop() is None
        assert timeline.pop_instant() == ()
        assert len(timeline) == 0


class TestOneStampIsOneInstant:

    def test_the_instant_leaves_whole_in_submission_order(self):
        """
        A tick slice, a fill due and a timer at one stamp: they leave together, in the order they
        were made, whatever order they were pushed in. A driver that stops after an instant has
        therefore finished it — the timeline's half of #457's session-end edge.
        """
        sequencer = EventSequencer()
        data = DataSliceEvent(ts_event=1_000, ts_init=1_000, seq=sequencer.next_seq(),
                              ticks=(_tick(1_000),))
        due = ResolutionEvent(ts_event=1_000, ts_init=1_000, seq=sequencer.next_seq())
        timer = TimeEvent(ts_event=1_000, ts_init=1_000, seq=sequencer.next_seq())
        later = TimeEvent(ts_event=1_500, ts_init=1_500, seq=sequencer.next_seq())
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        for event in (later, timer, data, due):
            timeline.push(event)

        assert timeline.pop_instant() == (data, due, timer)
        assert timeline.pop_instant() == (later,)
        assert timeline.pop_instant() == ()

    def test_a_clipped_slice_leaves_with_the_fill_due_beside_it(self):
        """
        Clipping skips the algo's work for an instant; it never drops an event (#457, the clipped
        slice with a resolution due). Which phases then run is the dispatcher's (#458).
        """
        sequencer = EventSequencer()
        clipped = next(slice_ticks([_tick(2_000, is_clipped=True)], EventOrdering.BY_TS_EVENT,
                                   sequencer))
        due = ResolutionEvent(ts_event=2_000, ts_init=2_000, seq=sequencer.next_seq())
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        timeline.push(due)
        timeline.push(clipped)

        assert timeline.pop_instant() == (clipped, due)

    def test_no_kind_goes_before_another(self):
        """
        The same two kinds at one stamp, made in both orders: the submission order decides each
        time, never the kind.
        """
        for first_kind in (EventKind.TIME, EventKind.RESOLUTION):
            sequencer = EventSequencer()
            if first_kind is EventKind.TIME:
                first = TimeEvent(ts_event=700, ts_init=700, seq=sequencer.next_seq())
                second = ResolutionEvent(ts_event=700, ts_init=700, seq=sequencer.next_seq())
            else:
                first = ResolutionEvent(ts_event=700, ts_init=700, seq=sequencer.next_seq())
                second = TimeEvent(ts_event=700, ts_init=700, seq=sequencer.next_seq())
            timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
            timeline.push(second)
            timeline.push(first)

            assert timeline.pop_instant() == (first, second)

    def test_two_events_of_one_kind_at_one_stamp_leave_by_seq(self):
        """
        `pop` hands out equal stamps by seq. Which fills one resolution event resolves, and in
        which order, is the pending-order manager's answer (`test_latency_due_peek.py`).
        """
        sequencer = EventSequencer()
        made_first = ResolutionEvent(ts_event=2_000, ts_init=2_000, seq=sequencer.next_seq())
        made_second = ResolutionEvent(ts_event=2_000, ts_init=2_000, seq=sequencer.next_seq())
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        timeline.push(made_second)
        timeline.push(made_first)

        assert timeline.pop() is made_first
        assert timeline.pop() is made_second


class TestTheTwoStamps:
    """
    A fill the venue executed at .300 that reached us at .450, and a timer due at .400. A backtest
    replays when things happened; a live session can only order by what it has received.
    """

    @staticmethod
    def _events():
        fill = ResolutionEvent(ts_event=300, ts_init=450, seq=0)
        timer = TimeEvent(ts_event=400, ts_init=400, seq=1)
        return fill, timer

    def test_a_backtest_orders_by_when_it_happened(self):
        fill, timer = self._events()
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        timeline.push(timer)
        timeline.push(fill)

        assert [timeline.pop(), timeline.pop()] == [fill, timer]

    def test_a_live_session_orders_by_when_it_arrived(self):
        fill, timer = self._events()
        timeline = EventTimeline(EventOrdering.BY_TS_INIT)
        timeline.push(fill)
        timeline.push(timer)

        assert [timeline.pop(), timeline.pop()] == [timer, fill]

    def test_the_other_stamp_travels_as_data(self):
        fill, _ = self._events()
        timeline = EventTimeline(EventOrdering.BY_TS_INIT)
        timeline.push(fill)

        popped = timeline.pop()
        assert (popped.ts_event, popped.ts_init) == (300, 450)
        assert timeline.get_ordering() is EventOrdering.BY_TS_INIT

    def test_a_live_instant_is_what_arrived_together(self):
        """Events received in one millisecond are one instant live, whatever their source stamps."""
        arrived_together_a = ResolutionEvent(ts_event=100, ts_init=900, seq=0)
        arrived_together_b = TimeEvent(ts_event=500, ts_init=900, seq=1)
        arrived_later = ResolutionEvent(ts_event=500, ts_init=950, seq=2)
        timeline = EventTimeline(EventOrdering.BY_TS_INIT)
        for event in (arrived_later, arrived_together_b, arrived_together_a):
            timeline.push(event)

        assert timeline.pop_instant() == (arrived_together_a, arrived_together_b)
        assert timeline.pop_instant() == (arrived_later,)
        assert timeline.pop_instant() == ()

    def test_a_backtest_instant_is_what_happened_together(self):
        """The same three events, ordered by their source stamps, cut into other instants."""
        happened_first = ResolutionEvent(ts_event=100, ts_init=900, seq=0)
        happened_together_a = TimeEvent(ts_event=500, ts_init=900, seq=1)
        happened_together_b = ResolutionEvent(ts_event=500, ts_init=950, seq=2)
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        for event in (happened_together_b, happened_together_a, happened_first):
            timeline.push(event)

        assert timeline.pop_instant() == (happened_first,)
        assert timeline.pop_instant() == (happened_together_a, happened_together_b)


class TestTimeHandedOverNeverRunsBack:
    """
    Once an instant is handed over, an event stamped before it is refused: a dispatcher sets the
    clock from what it is handed, and the clock only moves forward.
    """

    @pytest.mark.parametrize('ordering', list(EventOrdering))
    def test_an_event_stamped_before_the_last_instant_is_refused(self, ordering):
        timeline = EventTimeline(ordering)
        timeline.push(TimeEvent(ts_event=2_000, ts_init=2_000, seq=0))
        timeline.pop_instant()

        with pytest.raises(PastLoopEventError, match='1500'):
            timeline.push(TimeEvent(ts_event=1_500, ts_init=1_500, seq=1))
        assert len(timeline) == 0, 'the refused event was taken anyway'

    def test_a_single_pop_counts_as_handed_over_too(self):
        timeline = EventTimeline(EventOrdering.BY_TS_INIT)
        timeline.push(ResolutionEvent(ts_event=900, ts_init=1_200, seq=0))
        timeline.pop()

        with pytest.raises(PastLoopEventError):
            timeline.push(ResolutionEvent(ts_event=1_000, ts_init=1_100, seq=1))

    def test_an_event_at_the_stamp_just_handed_over_forms_a_later_instant(self):
        """Taken, not refused: whether a dispatcher may meet it is #458's to settle."""
        timeline = EventTimeline(EventOrdering.BY_TS_EVENT)
        first = TimeEvent(ts_event=2_000, ts_init=2_000, seq=0)
        timeline.push(first)
        assert timeline.pop_instant() == (first,)
        again = ResolutionEvent(ts_event=2_000, ts_init=2_000, seq=1)

        timeline.push(again)

        assert timeline.pop_instant() == (again,)
