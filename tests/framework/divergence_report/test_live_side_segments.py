"""
A live deployment cut into segments, each with where its sessions end and its compared window
(#512).

A segment begins where a detector finds a boundary between two booked sessions — today a change of
the code — and a later kind of boundary is one more detector, which a test-only detector proves
without a line of the segmentation changing. A window starts at the first tick and ends at the
boundary asked for, or at the last boundary before a stop that changed the account: declared by
the operator, or — where nothing is declared — a position that moved across a restart.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.reporting.builders.divergence import (
    live_segment_builder,
    live_trading_day_builder,
)
from python.framework.reporting.builders.divergence.live_segment_builder import (
    SEGMENT_BOUNDARY_DETECTORS,
    cut_segments,
    derive_segments,
    recorded_window_of,
    session_end_of,
    sessions_that_ran,
)
from python.framework.reporting.builders.divergence.live_trading_day_builder import closing_of
from python.framework.types.api.divergence_report_types import (
    DeclaredStop,
    SegmentBoundary,
    SegmentBoundaryKind,
    SessionEndKind,
    WindowEndCause,
)
from python.framework.types.api.report_types import SessionBookingState, WarningsErrorsOutcome
from python.framework.types.run_origin_types import (
    FRAMEWORK_REPOSITORY,
    CodeStand,
    RepositoryStand,
)
from python.framework.validators import divergence_day_validator
from python.framework.validators.divergence_day_validator import live_side_of
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    FIRST_DAY,
    FIRST_TICK,
    LOT_STEP,
    code_stand,
    days_from,
    deployment,
    records_of,
    session,
)

_UTC = timezone.utc


def _end_of_day(day_offset: int, hours: int = 10) -> datetime:
    """An instant inside a day of the deployment, counted from its first."""
    return FIRST_TICK.replace(minute=0) + timedelta(days=day_offset, hours=hours)


class TestTheBoundaryDetectors:

    def test_every_kind_but_the_start_has_exactly_one_detector(self):
        kinds = [kind for kind, _ in SEGMENT_BOUNDARY_DETECTORS]
        assert sorted(kinds) == sorted(
            set(SegmentBoundaryKind) - {SegmentBoundaryKind.DEPLOYMENT_START})

    def test_the_same_code_is_one_segment(self):
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_end_of_day(2))
        second = session('s2', _end_of_day(2, 12), days_from(FIRST_DAY + timedelta(days=2), 2),
                         first_period_no=3)
        segments = cut_segments([first, second])
        assert len(segments) == 1
        assert [boundary.kind for boundary in segments[0][0]] == [
            SegmentBoundaryKind.DEPLOYMENT_START]

    def test_a_code_change_begins_a_segment_and_names_what_moved(self):
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 2), ended_at=_end_of_day(2))
        second = session('s2', _end_of_day(2, 12), days_from(FIRST_DAY + timedelta(days=2), 2),
                         stand=code_stand('e5f6a7b8'))
        segments = cut_segments([first, second])
        assert [len(sessions) for _, sessions in segments] == [1, 1]
        boundary = segments[1][0][0]
        assert (boundary.kind, boundary.previous_run_id, boundary.run_id) == (
            SegmentBoundaryKind.CODE_STAND, 's1', 's2')
        assert boundary.code_changes and not boundary.stand_unread

    def test_a_stand_nobody_could_read_counts_as_moved(self):
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1), ended_at=_end_of_day(1))
        second = session('s2', _end_of_day(1, 12), days_from(FIRST_DAY + timedelta(days=1), 1),
                         stand=CodeStand(repositories=[RepositoryStand(
                             repository=FRAMEWORK_REPOSITORY, commit=None, dirty=False)]))
        boundary = cut_segments([first, second])[1][0][0]
        assert boundary.stand_unread

    def test_a_session_without_booked_figures_never_cuts(self):
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1), ended_at=_end_of_day(1))
        running = session('s2', _end_of_day(1, 12), [],
                          booking_state=SessionBookingState.NOT_BOOKED_YET)
        segments = cut_segments([first, running])
        assert len(segments) == 1 and [s.run_id for s in segments[0][1]] == ['s1', 's2']

    def test_a_further_kind_is_one_more_detector(self):
        """The seam a later boundary kind plugs into: nothing of the segmentation changes."""
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1), ended_at=_end_of_day(1))
        second = session('s2', _end_of_day(1, 12), days_from(FIRST_DAY + timedelta(days=1), 1),
                         stand=code_stand('e5f6a7b8'))

        def every_restart(previous, current):
            return SegmentBoundary(kind=SegmentBoundaryKind.CODE_STAND, run_id=current.run_id,
                                   previous_run_id=previous.run_id, stand_unread=True)

        detectors = SEGMENT_BOUNDARY_DETECTORS + ((SegmentBoundaryKind.CODE_STAND, every_restart),)
        segments = cut_segments([first, second], detectors)
        assert len(segments) == 2 and len(segments[1][0]) == 2, 'one segment names both'


class TestWhenASessionBegan:
    """Windows and days are event time, so a session is placed by its first tick — a replay of
    recorded data starts on today's clock with ticks of another month."""

    def test_its_first_tick_places_it(self):
        replay = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1),
                         start_time=FIRST_TICK + timedelta(days=300))
        assert replay.get_began() == FIRST_TICK

    def test_its_start_only_where_no_tick_came(self):
        silent = session('s2', None, [], start_time=FIRST_TICK)
        assert silent.get_began() == FIRST_TICK
        assert session('s3', None, []).get_began() is None


class TestSessionsThatCoverNoDay:

    def test_a_failed_a_refused_and_a_silent_session_are_named(self):
        ran = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1))
        failed = session('s2', None, [], booking_state=SessionBookingState.FAILED,
                         start_time=_end_of_day(1))
        refused = session('s3', _end_of_day(1, 12), [],
                          outcome=WarningsErrorsOutcome(refused_at_first_tick=True))
        silent = session('s4', None, [], booking_state=SessionBookingState.NOT_BOOKED_YET)
        kept, named = sessions_that_ran([ran, failed, refused, silent])
        assert [s.run_id for s in kept] == ['s1']
        assert [n.run_id for n in named] == ['s2', 's3', 's4'] and all(n.reason for n in named)


class TestWhereASessionEnds:

    @pytest.mark.parametrize('outcome, expected', [
        (WarningsErrorsOutcome(shutdown_mode='normal'), SessionEndKind.CLOSED),
        (WarningsErrorsOutcome(shutdown_mode='emergency', operator_interrupted=True),
         SessionEndKind.OPERATOR_INTERRUPTED),
        (WarningsErrorsOutcome(shutdown_mode='emergency', emergency_reason='feed lost'),
         SessionEndKind.CRASHED),
    ], ids=['closed', 'ctrl_c', 'crashed'])
    def test_a_close_row_says_how(self, outcome, expected):
        ended_at = _end_of_day(1)
        end = session_end_of(session('s1', FIRST_TICK, days_from(FIRST_DAY, 1), ended_at=ended_at,
                                     closed=True, outcome=outcome), has_successor=True)
        assert (end.kind, end.at, end.at_is_last_sign_of_life) == (expected, ended_at, False)
        assert (end.emergency_reason == 'feed lost') is (expected is SessionEndKind.CRASHED)

    def test_without_its_close_it_was_killed_or_it_runs(self):
        killed_at = _end_of_day(1)
        killed = session('s1', FIRST_TICK, days_from(FIRST_DAY, 1), ended_at=killed_at)
        assert session_end_of(killed, has_successor=True).kind is SessionEndKind.NO_CLOSE
        end = session_end_of(killed, has_successor=False)
        assert (end.kind, end.at, end.at_is_last_sign_of_life) == (
            SessionEndKind.OPEN, killed_at, True)


class TestTheWindow:

    def test_it_starts_at_the_first_tick_never_at_the_first_periods_opening(self):
        idle_first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3),
                             start_time=FIRST_TICK - timedelta(hours=1))
        segments, _ = derive_segments(deployment(idle_first))
        assert segments[0].window.start == FIRST_TICK

    def test_the_boundary_asked_for_ends_it_once_sealed(self):
        until = closing_of(FIRST_DAY + timedelta(days=2), CRYPTO_ANCHOR)
        capture = deployment(session('s1', FIRST_TICK, days_from(FIRST_DAY, 4)))
        segments, _ = derive_segments(capture, until=until)
        window = segments[0].window
        assert (window.end, window.end_cause) == (until, WindowEndCause.UNTIL)

    def test_a_boundary_not_sealed_yet_is_not_reached(self):
        until = closing_of(FIRST_DAY + timedelta(days=29), CRYPTO_ANCHOR)
        capture = deployment(session('s1', FIRST_TICK, days_from(FIRST_DAY, 4)))
        segments, _ = derive_segments(capture, until=until)
        window = segments[0].window
        assert window.end_cause is WindowEndCause.NOT_REACHED
        assert window.end == window.last_sealed == closing_of(
            FIRST_DAY + timedelta(days=3), CRYPTO_ANCHOR)

    def test_without_a_boundary_asked_for_it_ends_at_the_last_sealed(self):
        capture = deployment(session('s1', FIRST_TICK, days_from(FIRST_DAY, 2)))
        segments, _ = derive_segments(capture)
        assert segments[0].window.end_cause is WindowEndCause.LAST_SEALED

    def test_a_boundary_asked_for_must_be_a_boundary(self):
        with pytest.raises(ValueError, match='trading-day boundary'):
            derive_segments(deployment(session('s1', FIRST_TICK, days_from(FIRST_DAY, 2))),
                            until=datetime(2026, 12, 24, 0, 1, tzinfo=_UTC))

    def test_no_recorded_first_tick_leaves_it_without_a_start(self):
        segments, _ = derive_segments(deployment(session('s1', None, days_from(FIRST_DAY, 2),
                                                         start_time=FIRST_TICK)))
        assert (segments[0].window.start, segments[0].window.end_cause) == (
            None, WindowEndCause.START_UNKNOWN)


class TestAStopThatChangedTheAccount:
    """B1 section 4: such a stop ends the window at the last boundary before it."""

    @staticmethod
    def _restart(first_lots: float, last_lots: float):
        stopped_at = _end_of_day(3)
        first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3), ended_at=stopped_at,
                        closed=True, last_lots=last_lots)
        second = session('s2', stopped_at + timedelta(hours=1),
                         days_from(FIRST_DAY + timedelta(days=3), 4), first_lots=first_lots,
                         first_period_no=5)
        return deployment(first, second), stopped_at

    def test_a_declared_stop_ends_it_at_the_last_boundary_before(self):
        capture, stopped_at = self._restart(0.0, 0.5)
        stop = DeclaredStop(instant=stopped_at, changed_account=True, note='sold by hand')
        window = derive_segments(capture, declared_stops=[stop])[0][0].window
        assert window.end == closing_of(FIRST_DAY + timedelta(days=2), CRYPTO_ANCHOR)
        assert window.end_cause is WindowEndCause.DECLARED_STOP
        assert 'sold by hand' in window.end_detail

    def test_an_undeclared_position_change_across_the_restart_ends_it(self):
        capture, _ = self._restart(0.0, 0.5)
        window = derive_segments(capture)[0][0].window
        assert window.end_cause is WindowEndCause.UNDECLARED_ACCOUNT_CHANGE
        assert window.end == closing_of(FIRST_DAY + timedelta(days=2), CRYPTO_ANCHOR)
        assert 's1 → s2' in window.end_detail

    def test_a_stop_declared_as_changing_nothing_lets_the_window_run_on(self):
        capture, stopped_at = self._restart(0.0, 0.5)
        stop = DeclaredStop(instant=stopped_at, changed_account=False, note='restart only')
        window = derive_segments(capture, declared_stops=[stop])[0][0].window
        assert window.end_cause is WindowEndCause.LAST_SEALED

    def test_the_same_position_within_one_lot_step_is_no_change(self):
        capture, _ = self._restart(0.5 + LOT_STEP, 0.5)
        assert derive_segments(capture)[0][0].window.end_cause is WindowEndCause.LAST_SEALED


class TestTheReplaysInputs:

    def test_the_window_and_the_first_sessions_records(self):
        capture = deployment(session('s1', FIRST_TICK, days_from(FIRST_DAY, 3)))
        segment = derive_segments(capture)[0][0]
        window = recorded_window_of(segment)
        assert (window.warmup_from_run, window.start, window.end) == (
            's1', FIRST_TICK, closing_of(FIRST_DAY + timedelta(days=2), CRYPTO_ANCHOR))
        assert (window.rendered_config_id, window.broker_config_id) == ('rendered_1', 'broker_1')
        assert window.balances == {'USD': 10000.0}

    def test_a_window_without_a_start_cannot_be_replayed(self):
        segment = derive_segments(deployment(session('s1', None, days_from(FIRST_DAY, 2),
                                                     start_time=FIRST_TICK)))[0][0]
        with pytest.raises(ValueError, match='window start'):
            recorded_window_of(segment)


def test_nothing_reads_a_clock(monkeypatch):
    """The segments and days of the same records are the same whenever they are derived."""
    class NoClock(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError('a clock was read')

        @classmethod
        def utcnow(cls):
            raise AssertionError('a clock was read')

    for module in (live_segment_builder, live_trading_day_builder, divergence_day_validator):
        monkeypatch.setattr(module, 'datetime', NoClock)
    first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3), ended_at=_end_of_day(3))
    second = session('s2', _end_of_day(3, 12), days_from(FIRST_DAY + timedelta(days=3), 2),
                     first_period_no=4)
    capture = deployment(first, second)
    records = {**records_of(first), **records_of(second)}
    segments, named = derive_segments(capture)
    assert (segments, named) == derive_segments(capture)
    assert live_side_of(capture, segments, named, records) == live_side_of(
        capture, segments, named, records)
