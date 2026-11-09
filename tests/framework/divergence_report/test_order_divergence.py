"""
Measure 2 of the divergence report (#512) — each order submitted on both sides, measured.

Each test builds one day's orders on both sides from the record's own types and changes one thing:
a fill price, a partial fill, a refusal, a fee the venue did not report, an order on one side only.
The measures are numbers only; no test here asks whether one lies within its bound.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytest

from python.framework.reporting.builders.divergence.order_divergence_builder import (
    decision_frame_of,
    measure_day_orders,
    order_side_of,
    taker_rate_pct_of,
)
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderSide,
    RejectionReason,
)

_DAY = date(2026, 11, 25)
_OPENING = datetime(2026, 11, 25, tzinfo=timezone.utc)
_CLOSING = _OPENING + timedelta(days=1)
_DECIDED = _OPENING + timedelta(hours=9, seconds=2)


def _submitted(seq: int, at: datetime = _DECIDED, mid: Optional[float] = 3000.0,
               action: OrderAction = OrderAction.OPEN,
               direction: OrderDirection = OrderDirection.LONG,
               lots: float = 0.05) -> OrderEventRow:
    return OrderEventRow(
        scenario_name='my_unit', seq=seq, event_type=OrderEventType.SUBMITTED,
        order_id=f'order_{seq}', submitted_seq=seq, action=action, direction=direction,
        symbol='ETHUSD', lots=lots, submission_mid=mid, event_time=at.isoformat())


def _filled(seq: int, submitted_seq: int, price: float, lots: float = 0.05,
            fee: Optional[float] = 1.2, fee_source: FeeSource = FeeSource.VENUE,
            event_type: OrderEventType = OrderEventType.FILLED,
            action: OrderAction = OrderAction.OPEN,
            direction: OrderDirection = OrderDirection.LONG,
            at: datetime = _DECIDED + timedelta(seconds=2)) -> OrderEventRow:
    return OrderEventRow(
        scenario_name='my_unit', seq=seq, event_type=event_type,
        order_id=f'order_{submitted_seq}', submitted_seq=submitted_seq, action=action,
        direction=direction, symbol='ETHUSD', lots=lots, fill_price=price, fee=fee,
        fee_source=fee_source, event_time=at.isoformat())


def _order_frame(order_id: Optional[str], mid: float, equity: float,
                 at: datetime = _DECIDED) -> StateFrame:
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.ORDER, ticks_processed=1,
        symbol='ETHUSD', account_currency='USD', last_price=mid, balance=equity, equity=equity,
        open_positions=[], order_id=order_id)


def _day(live: List[OrderEventRow], backtest: List[OrderEventRow],
         live_frames: Optional[List[StateFrame]] = None,
         backtest_frames: Optional[List[StateFrame]] = None,
         taker_rate_pct: Optional[float] = 0.8):
    return measure_day_orders(
        _DAY, _CLOSING, [('s1', event) for event in live],
        {'s1': live_frames if live_frames is not None
         else [_order_frame('order_1', 3000.0, 1000.0)]},
        'b1', backtest,
        backtest_frames if backtest_frames is not None else [
            _order_frame('order_1', 3000.0, 1000.0)],
        taker_rate_pct, unbooked_executions=0)


class TestAnOrderOnBothSides:

    def test_the_same_order_shows_no_divergence(self):
        events = [_submitted(1), _filled(2, 1, 3001.5)]
        order, = _day(events, events).orders
        assert order.lots_difference == 0
        assert order.execution_difference == pytest.approx(0.0)
        assert order.lots_ratio == 1 and order.equity_ratio == 1

    def test_a_worse_live_fill_is_a_positive_execution_difference(self):
        live = [_submitted(1), _filled(2, 1, 3002.4)]
        backtest = [_submitted(1), _filled(2, 1, 3000.6)]
        order, = _day(live, backtest).orders
        assert order.execution_difference == pytest.approx(1.8)
        assert order.execution_difference_pct == pytest.approx(1.8 / 3000.0 * 100)

    def test_a_sell_is_worse_when_it_gets_less(self):
        close = dict(action=OrderAction.CLOSE, direction=OrderDirection.LONG)
        live = [_submitted(1, **close), _filled(2, 1, 2998.0, **close)]
        backtest = [_submitted(1, **close), _filled(2, 1, 2999.5, **close)]
        order, = _day(live, backtest).orders
        assert order.live.order_side is OrderSide.SELL
        assert order.execution_difference == pytest.approx(1.5)

    def test_the_live_fee_rate_against_the_taker_rate(self):
        live = [_submitted(1), _filled(2, 1, 3000.0, fee=1.2)]
        backtest = [_submitted(1), _filled(2, 1, 3000.0, fee=1.2,
                                           fee_source=FeeSource.ESTIMATE)]
        order, = _day(live, backtest).orders
        assert order.live_fee_rate_pct == pytest.approx(1.2 / (0.05 * 3000.0) * 100)
        assert order.taker_rate_pct == 0.8 and order.fee_not_checked_because == ''

    def test_a_fee_the_venue_did_not_report_is_not_checked(self):
        live = [_submitted(1), _filled(2, 1, 3000.0, fee_source=FeeSource.ESTIMATE)]
        order, = _day(live, live).orders
        assert order.live_fee_rate_pct is None
        assert "not the venue's own figure" in order.fee_not_checked_because

    def test_a_partial_fill_counts_the_executed_lots_only(self):
        live = [_submitted(1), _filled(2, 1, 3000.0, lots=0.03,
                                       event_type=OrderEventType.PARTIALLY_FILLED)]
        backtest = [_submitted(1), _filled(2, 1, 3000.0)]
        order, = _day(live, backtest).orders
        assert order.lots_difference == pytest.approx(-0.02)
        assert order.lots_ratio == pytest.approx(0.6)

    def test_several_fills_are_priced_by_their_lot_weighted_mean(self):
        live = [_submitted(1), _filled(2, 1, 3000.0, lots=0.02,
                                       event_type=OrderEventType.PARTIALLY_FILLED),
                _filled(3, 1, 3003.0, lots=0.03)]
        order, = _day(live, live).orders
        assert order.live.fill_price == pytest.approx((0.02 * 3000.0 + 0.03 * 3003.0) / 0.05)
        assert order.live.fee == pytest.approx(2.4)

    def test_a_refusal_is_named_with_its_cause(self):
        refused = OrderEventRow(
            scenario_name='my_unit', seq=2, event_type=OrderEventType.REJECTED,
            order_id='order_1', submitted_seq=1, rejection_reason=RejectionReason.BROKER_ERROR,
            venue_reason='EOrder:Insufficient funds', event_time=_DECIDED.isoformat())
        order, = _day([_submitted(1), refused], [_submitted(1), _filled(2, 1, 3000.0)]).orders
        assert order.live.filled_lots == 0
        assert 'broker_error' in order.live.refusal and 'Insufficient funds' in order.live.refusal

    def test_the_size_against_the_two_account_values(self):
        live = [_submitted(1, lots=0.051), _filled(2, 1, 3000.0, lots=0.051)]
        backtest = [_submitted(1), _filled(2, 1, 3000.0)]
        order, = _day(live, backtest,
                      live_frames=[_order_frame('order_1', 3000.0, 1020.0)]).orders
        assert order.lots_ratio == pytest.approx(1.02)
        assert order.equity_ratio == pytest.approx(1.02)

    def test_the_delay_from_the_decision_mid_is_reported_apart(self):
        live = [_submitted(1, mid=3001.0), _filled(2, 1, 3001.0)]
        order, = _day(live, live, live_frames=[_order_frame('order_1', 3000.0, 1000.0)]).orders
        assert order.live_delay == pytest.approx(1.0)
        assert order.backtest_delay == pytest.approx(1.0)


class TestWhatIsLeftOver:

    def test_an_order_on_one_side_only_is_listed(self):
        day = _day([_submitted(1), _filled(2, 1, 3000.0), _submitted(3), _filled(4, 3, 3001.0)],
                   [_submitted(1), _filled(2, 1, 3000.0)])
        assert len(day.orders) == 1
        assert [outcome.seq for outcome in day.live_one_sided] == [3]
        assert day.backtest_one_sided == []
        assert (day.live_fill_count, day.backtest_fill_count) == (2, 1)

    def test_an_order_whose_record_names_no_side_measures_no_price(self):
        bare = OrderEventRow(scenario_name='my_unit', seq=1, event_type=OrderEventType.SUBMITTED,
                             order_id='order_1', submitted_seq=1, event_time=_DECIDED.isoformat())
        order, = _day([bare, _filled(2, 1, 3000.0)], [_submitted(1), _filled(2, 1, 3000.0)]).orders
        assert order.live.order_side is None and order.execution_difference is None


class TestTheDecisionFrame:

    def test_found_by_the_order_it_names(self):
        frames = [_order_frame(None, 2990.0, 990.0, _DECIDED - timedelta(minutes=1)),
                  _order_frame('order_1', 3000.0, 1000.0)]
        assert decision_frame_of('order_1', _DECIDED, frames).last_price == 3000.0

    def test_else_the_order_frame_written_when_it_was_submitted(self):
        frames = [_order_frame('close_pos_1', 3000.0, 1000.0)]
        assert decision_frame_of('order_7', _DECIDED, frames).last_price == 3000.0

    def test_no_frame_of_the_pass_gives_none(self):
        assert decision_frame_of('order_7', _DECIDED,
                                 [_order_frame(None, 3000.0, 1000.0, _OPENING)]) is None


def test_a_close_sells_what_a_long_held_and_buys_back_what_a_short_held():
    assert order_side_of(_submitted(1, action=OrderAction.CLOSE)) is OrderSide.SELL
    assert order_side_of(_submitted(1, action=OrderAction.CLOSE,
                                    direction=OrderDirection.SHORT)) is OrderSide.BUY
    assert order_side_of(_submitted(1, direction=OrderDirection.SHORT)) is OrderSide.SELL


def test_the_taker_rate_comes_from_the_frozen_document_in_percent():
    assert taker_rate_pct_of({'fee_structure': {'model': 'maker_taker', 'maker_fee': 0.4,
                                                'taker_fee': 0.8}}) == 0.8
    assert taker_rate_pct_of({'fee_structure': {'model': 'spread'}}) is None
    assert taker_rate_pct_of({}) is None
