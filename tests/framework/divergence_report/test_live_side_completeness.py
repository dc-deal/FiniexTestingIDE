"""
Which trading days of a live deployment are compared, and why each other one is not (#512).

Each sealed day's order-event record is checked against its ledger row — complete, incomplete with
every count named, or not checked with why, never a pass. Every cause that holds on a day is named:
a kill, a stop, an opening no session saw, a day the loop did not run through, a seal missing,
sealed twice, overlapping sessions. The days after a break are compared again only from the first
opening at which both sides hold the same position — which a backtest of the window decides.
"""

from datetime import timedelta
from typing import List

from python.framework.reporting.builders.divergence.live_segment_builder import derive_segments
from python.framework.reporting.builders.divergence.live_trading_day_builder import closing_of
from python.framework.types.api.divergence_report_types import (
    DayCompletenessState,
    DayExclusionReason,
    DeclaredStop,
    LiveSegment,
    TradingDayClassification,
)
from python.framework.types.api.report_types import WarningsErrorsOutcome
from python.framework.types.divergence_capture_types import BoundaryPosition
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.validators.divergence_day_validator import (
    check_day_completeness,
    closing_positions_of,
    live_side_of,
    resolve_resumption,
)
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    FIRST_DAY,
    FIRST_TICK,
    LOT_STEP,
    SYMBOL,
    day_record,
    days_from,
    deployment,
    event,
    ledger_row,
    records_of,
    session,
)

_R = DayExclusionReason


def _side(*sessions, records=None, until=None, stops=()) -> List[LiveSegment]:
    """Segment and classify a deployment of the given sessions."""
    capture = deployment(*sessions)
    segments, named = derive_segments(capture, until=until, declared_stops=stops)
    day_records = {}
    for captured in sessions:
        day_records.update(records_of(captured))
    day_records.update(records or {})
    return live_side_of(capture, segments, named, day_records, until).segments


def _at(day_offset: int, hours: float) -> object:
    """An instant inside a day of the deployment, counted from its first."""
    return FIRST_TICK.replace(minute=0) + timedelta(days=day_offset, hours=hours)


def _reasons(day: TradingDayClassification) -> set:
    return set(day.exclusions)


class TestADaysRecordAgainstItsRow:

    @staticmethod
    def _row(**counts):
        closing = closing_of(FIRST_DAY, CRYPTO_ANCHOR)
        return ledger_row('s1', 1, FIRST_TICK, closing, counts=counts), closing

    def test_complete(self):
        row, closing = self._row()
        completeness = check_day_completeness(row, day_record('s1', FIRST_DAY), closing)
        assert completeness.state is DayCompletenessState.COMPLETE
        assert completeness.uncompared_columns == ['sl_tp_triggered']

    def test_incomplete_names_every_count(self):
        row, closing = self._row(orders_submitted=2, orders_cancelled=1)
        completeness = check_day_completeness(row, day_record('s1', FIRST_DAY), closing)
        assert completeness.state is DayCompletenessState.INCOMPLETE
        assert {(m.column, m.recorded, m.counted) for m in completeness.mismatches} == {
            ('orders_submitted', 1, 2), ('orders_cancelled', 0, 1)}

    def test_a_partial_fill_the_venue_then_ended_is_complete(self):
        row, closing = self._row()
        noon = closing - timedelta(hours=12)
        records = day_record('s1', FIRST_DAY, events=[
            event(1, OrderEventType.SUBMITTED, noon),
            event(2, OrderEventType.PARTIALLY_FILLED, noon),
            event(3, OrderEventType.CANCELLED, noon, cum_lots=0.04)])
        assert check_day_completeness(row, records, closing).state is DayCompletenessState.COMPLETE

    def test_every_way_it_cannot_be_checked(self):
        row, closing = self._row()
        after = closing + timedelta(seconds=5)
        cases = {
            'no day record': (row, None),
            'cannot be read': (
                row, day_record('s1', FIRST_DAY, order_events_unreadable='bad footer')),
            'names the day': (row, day_record('s1', FIRST_DAY,
                                              order_events_label=FIRST_DAY + timedelta(days=1))),
            'cut off mid-write': (
                row, day_record('s1', FIRST_DAY, order_events_cut_last_line=True)),
            'after the day closed': (
                row, day_record('s1', FIRST_DAY, order_events_last_time=after)),
            'carries no orders_adopted': (
                ledger_row('s1', 1, FIRST_TICK, closing, counts={'orders_adopted': None}),
                day_record('s1', FIRST_DAY)),
            'deleted by a prune': (
                ledger_row('s1', 1, FIRST_TICK, closing, pruned_at='2027-01-10T00:00:00+00:00'),
                day_record('s1', FIRST_DAY)),
        }
        for said, (checked_row, records) in cases.items():
            completeness = check_day_completeness(checked_row, records, closing)
            assert completeness.state is DayCompletenessState.NOT_CHECKED, said
            assert said in completeness.not_checked_because, completeness.not_checked_because


class TestTheThirtyDayRunAsPlanned:
    """One session, still running: every sealed day compared, the end not reached yet."""

    def test_every_sealed_day_is_compared(self):
        until = closing_of(FIRST_DAY + timedelta(days=29), CRYPTO_ANCHOR)
        segment, = _side(session('s1', FIRST_TICK, days_from(FIRST_DAY, 10)), until=until)
        assert len(segment.days) == 10 and all(day.included for day in segment.days)
        assert segment.days[0].first_of_segment and segment.days[0].opening == FIRST_TICK
        assert segment.session_ends[0].kind.value == 'open'

    def test_the_first_day_is_exempt_from_its_opening(self):
        """The first tick comes five minutes after midnight; day 1's decision is compared."""
        segment, = _side(session('s1', FIRST_TICK, days_from(FIRST_DAY, 2)))
        assert segment.days[0].included


class TestAKilledSession:

    def test_a_kill_and_a_restart_the_same_day(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3), ended_at=_at(3, 10))
        restarted = session('s2', _at(3, 11), days_from(FIRST_DAY + timedelta(days=3), 3),
                            first_period_no=4)
        segment, = _side(killed, restarted)
        day4, day5, day6 = segment.days[3:6]
        assert _R.SESSION_KILLED_IN_DAY in _reasons(day4)
        assert _R.NO_SESSION_AT_OPENING not in _reasons(day5), 'the restart ran when day 5 opened'
        assert _reasons(day5) == _reasons(day6) == {_R.RESUMPTION_PENDING}
        assert all(day.included for day in segment.days[:3])

    def test_a_kill_and_a_restart_two_days_later(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_at(2, 10))
        restarted = session('s2', _at(4, 9), days_from(FIRST_DAY + timedelta(days=4), 2),
                            first_period_no=3)
        segment, = _side(killed, restarted)
        killed_day, empty_day, restart_day = segment.days[2:5]
        assert {_R.SESSION_KILLED_IN_DAY, _R.NOT_SEALED_AT_BOUNDARY} <= _reasons(killed_day)
        assert {_R.NO_SESSION_AT_OPENING, _R.NOT_SEALED_AT_BOUNDARY} <= _reasons(empty_day)
        assert {_R.NO_SESSION_AT_OPENING, _R.RESUMPTION_PENDING} <= _reasons(restart_day)

    def test_an_emergency_shutdown_is_a_kill_and_quoted(self):
        crashed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_at(2, 10),
                          closed=True, outcome=WarningsErrorsOutcome(
                              shutdown_mode='emergency', emergency_reason='feed lost'))
        restarted = session('s2', _at(2, 11), days_from(FIRST_DAY + timedelta(days=2), 2),
                            first_period_no=4)
        day = _side(crashed, restarted)[0].days[2]
        assert _R.SESSION_KILLED_IN_DAY in _reasons(day)
        assert any('feed lost' in detail for detail in day.details)

    def test_an_orderly_stop_in_the_day_is_named_as_a_stop(self):
        stopped = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_at(2, 10),
                          closed=True)
        restarted = session('s2', _at(2, 11), days_from(FIRST_DAY + timedelta(days=2), 2),
                            first_period_no=4)
        assert _R.SESSION_STOPPED_IN_DAY in _reasons(_side(stopped, restarted)[0].days[2])


class TestALateSealOverSeveralDays:
    """A machine that slept: a row for every day passed, one day record (the first)."""

    def test_the_days_the_loop_did_not_run_are_named(self):
        slept = session('s1', FIRST_TICK, days_from(FIRST_DAY, 6))
        skipped = [FIRST_DAY + timedelta(days=3), FIRST_DAY + timedelta(days=4)]
        capture = deployment(slept)
        segments, named = derive_segments(capture)
        records = records_of(slept, skip=skipped)
        segment, = live_side_of(capture, segments, named, records).segments
        assert all(_R.LOOP_DID_NOT_RUN in _reasons(segment.days[offset]) for offset in (3, 4))
        assert _R.COMPLETENESS_NOT_CHECKED not in _reasons(segment.days[3]), 'one cause, named once'
        assert _reasons(segment.days[5]) == {_R.RESUMPTION_PENDING}

    def test_an_order_stream_that_stopped_is_not_a_loop_that_stopped(self):
        """Another stream sealed the day, so the loop ran; only the day's order record is gone."""
        ran = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3))
        day = FIRST_DAY + timedelta(days=1)
        without_orders = day_record('s1', day, events=[], order_events_path=None,
                                    order_events_label=None)
        checked = _side(ran, records={('s1', day): without_orders})[0].days[1]
        assert _reasons(checked) == {_R.COMPLETENESS_NOT_CHECKED}

    def test_a_session_killed_after_booking_and_before_sealing_is_not_checked(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2),
                         ended_at=closing_of(FIRST_DAY + timedelta(days=1), CRYPTO_ANCHOR)
                         - timedelta(seconds=1))
        capture = deployment(killed)
        segments, named = derive_segments(capture)
        records = records_of(killed, skip=[FIRST_DAY + timedelta(days=1)])
        last = live_side_of(capture, segments, named, records).segments[0].days[1]
        assert _R.COMPLETENESS_NOT_CHECKED in _reasons(last)
        assert _R.LOOP_DID_NOT_RUN not in _reasons(last)


class TestGuardsAgainstADaySlippingIn:

    def test_a_day_sealed_twice(self):
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2))
        twin = session('s2', FIRST_TICK + timedelta(minutes=1), days_from(FIRST_DAY, 1))
        days = _side(first, twin)[0].days
        assert {_R.DUPLICATE_ROWS, _R.SESSION_OVERLAP} <= _reasons(days[0])

    def test_a_record_holding_a_fill_nothing_was_booked_for(self):
        closing = closing_of(FIRST_DAY + timedelta(days=1), CRYPTO_ANCHOR)
        noon = closing - timedelta(hours=12)
        unbooked = day_record('s1', FIRST_DAY + timedelta(days=1), events=[
            event(1, OrderEventType.SUBMITTED, noon), event(2, OrderEventType.FILLED, noon),
            event(3, OrderEventType.FILLED, noon)])
        day = _side(session('s1', FIRST_TICK, days_from(FIRST_DAY, 3)),
                    records={('s1', FIRST_DAY + timedelta(days=1)): unbooked})[0].days[1]
        assert _reasons(day) == {_R.INCOMPLETE_RECORD}
        assert any('orders_executed 2 recorded, 1 counted' in detail for detail in day.details)

    def test_pruned_records(self):
        segment, = _side(session('s1', FIRST_TICK, days_from(FIRST_DAY, 2),
                                 pruned_at='2027-01-10T00:00:00+00:00'))
        assert all(_reasons(day) == {_R.RECORDS_PRUNED} for day in segment.days)

    def test_days_after_a_declared_stop_lie_after_the_window(self):
        stopped_at = _at(2, 10)
        stopped = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=stopped_at,
                          closed=True)
        restarted = session('s2', _at(2, 12), days_from(FIRST_DAY + timedelta(days=2), 3),
                            first_period_no=4)
        stop = DeclaredStop(instant=stopped_at, changed_account=True, note='sold by hand')
        segment, = _side(stopped, restarted, stops=[stop])
        assert [day.included for day in segment.days[:2]] == [True, True]
        assert all(_R.AFTER_WINDOW_END in _reasons(day) for day in segment.days[2:])

    def test_without_a_first_tick_no_day_is_placed(self):
        segment, = _side(session('s1', None, days_from(FIRST_DAY, 2), start_time=FIRST_TICK))
        assert segment.days
        assert all(_reasons(day) == {_R.WINDOW_START_UNKNOWN} for day in segment.days)


class TestPropertiesOfEveryClassification:

    def test_every_grid_day_appears_and_every_excluded_one_says_why(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 4), ended_at=_at(4, 10))
        restarted = session('s2', _at(6, 9), days_from(FIRST_DAY + timedelta(days=6), 4),
                            first_period_no=5)
        days = _side(killed, restarted)[0].days
        assert [day.trading_day for day in days] == days_from(FIRST_DAY, 10)
        for day in days:
            assert day.included is not bool(day.exclusions)
            assert len(day.details) >= len(day.exclusions)
            assert all(reason.rule_section() for reason in day.exclusions)
            assert not (day.included and _R.RESUMPTION_PENDING in day.exclusions)

    def test_every_cause_names_its_rule_section_and_says_whether_it_breaks_the_record(self):
        breaking = {reason for reason in DayExclusionReason if reason.breaks_the_record()}
        assert breaking == {_R.SESSION_KILLED_IN_DAY, _R.SESSION_STOPPED_IN_DAY,
                            _R.NO_SESSION_AT_OPENING, _R.LOOP_DID_NOT_RUN}
        assert all(reason.rule_section() in {'0', '4', '5'} for reason in DayExclusionReason)


class TestTheResumption:
    """The days after a break wait for the two sides to hold the same position at an opening."""

    @staticmethod
    def _pending_days():
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_at(2, 10))
        restarted = session('s2', _at(2, 11), days_from(FIRST_DAY + timedelta(days=2), 5),
                            first_period_no=3)
        return _side(killed, restarted)[0].days

    @staticmethod
    def _position(day: TradingDayClassification, lots: float) -> BoundaryPosition:
        return BoundaryPosition(boundary=day.opening, frame_time=day.opening - timedelta(seconds=1),
                                net_lots={SYMBOL: lots} if lots else {}, holdings=None)

    def test_apart_apart_then_the_same(self):
        days = self._pending_days()
        pending = [day for day in days if _R.RESUMPTION_PENDING in day.exclusions]
        assert len(pending) == 4
        live = {day.opening: self._position(day, 0.5) for day in pending}
        backtest = {day.opening: self._position(day, lots)
                    for day, lots in zip(pending, (0.0, 0.0, 0.5, 0.0))}
        resolved = resolve_resumption(days, live, backtest, LOT_STEP, None)
        later = [day for day in resolved if day.trading_day >= pending[0].trading_day]
        assert [_reasons(day) for day in later] == [
            {_R.POSITIONS_APART}, {_R.POSITIONS_APART}, set(), set()]
        assert [day.included for day in later] == [False, False, True, True]

    def test_exactly_one_lot_step_counts_as_the_same(self):
        days = self._pending_days()
        first = next(day for day in days if _R.RESUMPTION_PENDING in day.exclusions)
        resolved = resolve_resumption(days, {first.opening: self._position(first, 0.5)},
                                      {first.opening: self._position(first, 0.5 + LOT_STEP)},
                                      LOT_STEP, None)
        assert all(_R.RESUMPTION_PENDING not in day.exclusions for day in resolved)

    def test_a_side_without_a_position_is_apart(self):
        days = self._pending_days()
        resolved = resolve_resumption(days, {}, {}, LOT_STEP, None)
        assert all(_R.POSITIONS_APART in day.exclusions for day in resolved
                   if day.trading_day >= FIRST_DAY + timedelta(days=3))

    def test_the_live_positions_come_from_each_days_last_frame(self):
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3))
        capture = deployment(killed)
        segments, named = derive_segments(capture)
        records = records_of(killed, lots={FIRST_DAY + timedelta(days=1): 0.3})
        days = live_side_of(capture, segments, named, records).segments[0].days
        positions = closing_positions_of(days, records)
        assert positions[days[1].closing].net_lots == {SYMBOL: 0.3}
        assert set(positions) == {day.closing for day in days}
