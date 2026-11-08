"""
The trading days of a live deployment as the divergence report places them (#512).

The grid of days is held to the booking period recorder itself — the crypto anchor at midnight
UTC, the forex anchor at 17:00 New York across a weekend and a daylight-saving switch — so the
report places a day exactly where a session sealed it. The count declaration is held complete in
both directions; a day's events are counted the way the executor counted them; a boundary's
position is read from the last frame before it; days and orders are paired with a backtest.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.reporting.builders.booking_period_builder import PeriodSnapshot
from python.framework.reporting.builders.divergence.live_trading_day_builder import (
    boundary_position_of,
    closing_of,
    count_day_order_events,
    is_step_after_partial_fill,
    pair_orders_of_day,
    pair_trading_days,
    positions_agree,
    trading_day_grid,
)
from python.framework.types.api.divergence_report_types import (
    LedgerRowKey,
    TradingDayClassification,
)
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.divergence_capture_types import (
    ORDER_EVENT_COUNT_COLUMN,
    UNCOMPARED_COUNT_COLUMNS,
    BoundaryPosition,
)
from python.framework.types.run_results_types import PeriodCloseReason
from python.framework.types.trading_env_types.order_event_types import (
    ORDER_EVENT_BY_STATUS,
    OrderEventType,
)
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_COUNT_FIELDS,
    EXECUTION_STATS_FIELD_BY_STATUS,
)
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    LOT_STEP,
    SYMBOL,
    event,
    frame,
    ledger_row,
)

_FOREX = DayAnchorConfig(local_time='17:00', timezone='America/New_York')
_UTC = timezone.utc


def _sealed_by_the_recorder(anchor: DayAnchorConfig, weekend_closure: bool, start: datetime,
                            hours: int):
    """Drive the recorder hour by hour from a start, and hand back what it sealed."""
    recorder = BookingPeriodRecorder('unit', anchor, weekend_closure=weekend_closure)
    for hour in range(hours + 1):
        recorder.check_boundary(start + timedelta(hours=hour),
                                lambda: ([], PeriodSnapshot(currency='USD')))
    return [period for period in recorder.get_sealed_periods()
            if period.reason is PeriodCloseReason.ANCHOR]


class TestTheGridIsTheRecorders:
    """Every day the recorder seals, with its label and closing, and no other."""

    @pytest.mark.parametrize('anchor, weekend_closure, start', [
        (CRYPTO_ANCHOR, False, datetime(2026, 11, 24, 0, 5, tzinfo=_UTC)),
        # A forex fortnight across a weekend and the New York switch back to winter time
        (_FOREX, True, datetime(2026, 10, 26, 23, 30, tzinfo=_UTC)),
    ], ids=['crypto', 'forex_weekend_dst'])
    def test_labels_and_closings_match(self, anchor, weekend_closure, start):
        sealed = _sealed_by_the_recorder(anchor, weekend_closure, start, hours=14 * 24)
        last_closing = sealed[-1].closed_at
        grid = trading_day_grid(start, last_closing, anchor, weekend_closure)
        assert [(period.trading_day, period.closed_at) for period in sealed] == [
            (day, closing_of(day, anchor)) for day in grid]
        assert len(grid) > 5

    def test_the_forex_grid_skips_the_closed_days(self):
        start = datetime(2026, 10, 26, 23, 30, tzinfo=_UTC)
        grid = trading_day_grid(start, start + timedelta(days=14), _FOREX, True)
        assert date(2026, 10, 30) not in grid and date(2026, 10, 31) not in grid
        assert date(2026, 11, 1) in grid, 'Sunday evening trades for Monday'

    def test_a_grid_that_closes_no_day_is_empty(self):
        start = datetime(2026, 11, 24, 0, 5, tzinfo=_UTC)
        assert trading_day_grid(start, start + timedelta(hours=3), CRYPTO_ANCHOR, False) == []


class TestTheCountDeclaration:
    """Every event is counted in one column or declared uncounted; every count names its events."""

    def test_it_declares_every_event(self):
        assert set(ORDER_EVENT_COUNT_COLUMN) == set(OrderEventType)

    def test_every_count_is_counted_or_declared_uncompared(self):
        columns = {column for column in ORDER_EVENT_COUNT_COLUMN.values() if column is not None}
        assert columns | set(UNCOMPARED_COUNT_COLUMNS) == set(EXECUTION_COUNT_FIELDS)
        assert not columns & set(UNCOMPARED_COUNT_COLUMNS)

    def test_its_endings_are_the_executors_own_maps(self):
        for status, ending in ORDER_EVENT_BY_STATUS.items():
            if ending is not None:
                assert ORDER_EVENT_COUNT_COLUMN[ending] == EXECUTION_STATS_FIELD_BY_STATUS[status]


class TestADaysEventsAreCountedAsTheExecutorCounts:

    def test_an_order_submitted_and_filled(self):
        at = datetime(2026, 11, 24, 12, tzinfo=_UTC)
        counts = count_day_order_events([
            event(1, OrderEventType.SUBMITTED, at), event(2, OrderEventType.ACCEPTED, at),
            event(3, OrderEventType.FILLED, at)])
        assert (counts['orders_submitted'], counts['orders_executed']) == (1, 1)
        assert sum(counts.values()) == 2

    def test_the_step_after_a_partial_fill_counts_nowhere(self):
        at = datetime(2026, 11, 24, 12, tzinfo=_UTC)
        step = event(3, OrderEventType.CANCELLED, at, cum_lots=0.04)
        counts = count_day_order_events([event(1, OrderEventType.SUBMITTED, at),
                                         event(2, OrderEventType.PARTIALLY_FILLED, at), step])
        assert is_step_after_partial_fill(step)
        assert (counts['orders_executed'], counts['orders_cancelled']) == (1, 0)

    def test_a_cancel_with_nothing_executed_counts(self):
        at = datetime(2026, 11, 24, 12, tzinfo=_UTC)
        cancel = event(2, OrderEventType.CANCELLED, at)
        assert not is_step_after_partial_fill(cancel)
        assert count_day_order_events([cancel])['orders_cancelled'] == 1

    def test_an_adoption_counts_as_adopted(self):
        at = datetime(2026, 11, 24, 12, tzinfo=_UTC)
        assert count_day_order_events([event(1, OrderEventType.ADOPTED, at, cum_lots=0.02)])[
            'orders_adopted'] == 1


class TestThePositionAtABoundary:

    def test_it_is_the_last_frame_strictly_before_it(self):
        boundary = datetime(2026, 11, 25, tzinfo=_UTC)
        frames = [frame(boundary - timedelta(minutes=2), lots=0.1),
                  frame(boundary - timedelta(minutes=1), lots=0.2),
                  frame(boundary, lots=0.3)]
        position = boundary_position_of(frames, boundary)
        assert position.frame_time == boundary - timedelta(minutes=1)
        assert position.net_lots == {SYMBOL: pytest.approx(0.2)}

    def test_a_short_is_negative_and_flat_is_empty(self):
        boundary = datetime(2026, 11, 25, tzinfo=_UTC)
        short = boundary_position_of([frame(boundary - timedelta(seconds=1), 0.1, 'short')],
                                     boundary)
        flat = boundary_position_of([frame(boundary - timedelta(seconds=1))], boundary)
        assert short.net_lots == {SYMBOL: pytest.approx(-0.1)} and flat.net_lots == {}

    def test_no_frame_before_it_reads_nothing(self):
        boundary = datetime(2026, 11, 25, tzinfo=_UTC)
        position = boundary_position_of([frame(boundary)], boundary)
        assert position.frame_time is None and position.holdings is None


class TestTwoPositionsAgree:

    @staticmethod
    def _at(lots, holdings=None):
        boundary = datetime(2026, 11, 25, tzinfo=_UTC)
        return BoundaryPosition(boundary=boundary, frame_time=boundary - timedelta(seconds=1),
                                net_lots={SYMBOL: lots} if lots else {}, holdings=holdings)

    def test_within_exactly_one_lot_step(self):
        assert positions_agree(self._at(0.10), self._at(0.10 + LOT_STEP), LOT_STEP, None)
        assert not positions_agree(self._at(0.10), self._at(0.10 + 2 * LOT_STEP), LOT_STEP, None)

    def test_flat_against_flat(self):
        assert positions_agree(self._at(0.0), self._at(0.0), LOT_STEP, None)

    def test_a_side_without_a_frame_is_apart(self):
        boundary = datetime(2026, 11, 25, tzinfo=_UTC)
        unread = BoundaryPosition(boundary=boundary, frame_time=None, net_lots={}, holdings=None)
        assert not positions_agree(unread, self._at(0.0), LOT_STEP, None)

    def test_at_spot_the_base_holding_decides_too(self):
        held = self._at(0.1, {'ETH': 0.1, 'USD': 1000.0})
        sold_by_hand = self._at(0.1, {'ETH': 0.0, 'USD': 1200.0})
        assert positions_agree(held, self._at(0.1, {'ETH': 0.1, 'USD': 999.0}), LOT_STEP, 'ETH')
        assert not positions_agree(held, sold_by_hand, LOT_STEP, 'ETH')


class TestPairing:

    @staticmethod
    def _day(day: date, included: bool = True) -> TradingDayClassification:
        closing = closing_of(day, CRYPTO_ANCHOR)
        return TradingDayClassification(
            trading_day=day, opening=closing - timedelta(days=1), closing=closing,
            run_id='live_1', included=included,
            ledger_row=LedgerRowKey(run_id='live_1', unit_name='my_session', period_no=day.day))

    def test_days_pair_by_their_closing(self):
        days = [self._day(date(2026, 11, 24)), self._day(date(2026, 11, 25), included=False),
                self._day(date(2026, 11, 26))]
        backtest = [ledger_row('backtest_1', 1, datetime(2026, 11, 24, 0, 5, tzinfo=_UTC),
                               closing_of(date(2026, 11, 24), CRYPTO_ANCHOR))]
        paired = pair_trading_days(days, backtest)
        assert [pair.closing.date() for pair in paired] == [date(2026, 11, 25), date(2026, 11, 27)]
        assert paired[0].backtest_row.run_id == 'backtest_1'
        assert paired[1].backtest_row is None, 'a day the backtest closed no row at says so'

    def test_orders_pair_by_rank_and_what_is_left_is_listed(self):
        at = datetime(2026, 11, 24, 12, tzinfo=_UTC)
        live = [('live_2', event(1, OrderEventType.SUBMITTED, at)),
                ('live_1', event(9, OrderEventType.SUBMITTED, at)),
                ('live_1', event(4, OrderEventType.SUBMITTED, at)),
                ('live_1', event(5, OrderEventType.FILLED, at))]
        backtest = [event(7, OrderEventType.SUBMITTED, at), event(3, OrderEventType.SUBMITTED, at)]
        paired = pair_orders_of_day(live, backtest)
        assert [(pair.live_run_id, pair.live_seq, pair.backtest_seq) for pair in paired.pairs] == [
            ('live_2', 1, 3), ('live_1', 4, 7)]
        assert [(left.run_id, left.seq) for left in paired.live_unpaired] == [('live_1', 9)]
        assert paired.backtest_unpaired == []
