"""
Fill Slippage (#631) — every fill measured against the price its order names, in both pipelines.

`derive_fill_slippage` reads a unit's order-event stream and the level each protective close fired
at, which the executor keeps per fill. It is tested over hand-made event sequences shaped the way
each pipeline writes them: the simulation turns a triggered stop-limit into a limit and its fill
carries no trigger, a session's fill does; the simulation fills a protective close AT its level, a
session wherever the market let it. One derivation has to give both the same reference, or a
backtest and a session measure one order two ways.
"""

import pytest

from python.framework.reporting.builders.fill_slippage import derive_fill_slippage
from python.framework.types.fill_slippage_types import SlippageReference
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderType,
)

def _event(seq, event_type, submitted_seq, order_type=OrderType.MARKET,
           direction=OrderDirection.LONG, action=OrderAction.OPEN, position_id='pos_1', **fields):
    """
    One order event of the order `pos_1` opens — or closes, with `action=CLOSE`.

    Args:
        seq: Its place on the stream
        event_type: What happened
        submitted_seq: Its order's submission
        order_type: The type the order was submitted as
        direction: The position's direction
        action: OPEN or CLOSE
        position_id: The position it opens or closes
        **fields: Prices and the rest

    Returns:
        The event
    """
    return OrderEvent(
        seq=seq, event_type=event_type, order_id=f'order_{submitted_seq}',
        submitted_seq=submitted_seq, order_type=order_type, direction=direction, action=action,
        position_id=position_id, symbol='BTCUSD', **fields)


def _one(events, levels=None):
    """
    The slippage of the stream's only fill.

    Args:
        events: The stream
        levels: The level each protective close fired at, by its fill's seq

    Returns:
        The fill's slippage
    """
    derived = derive_fill_slippage(list(events), levels or {})
    assert len(derived) == 1
    return next(iter(derived.values()))


class TestAMarketOrderIsMeasuredAgainstTheMidWhenItWasSent:

    def test_a_buy_above_the_mid_is_a_cost(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, submission_mid=100.0),
            _event(2, OrderEventType.FILLED, 1, fill_price=100.05),
        ])

        assert slip.measured_against is SlippageReference.SUBMISSION_MID
        assert slip.reference_price == 100.0
        assert slip.slippage == pytest.approx(0.05)
        assert slip.slippage_pct == pytest.approx(0.05)

    def test_closing_a_long_sells_so_below_the_mid_is_the_cost(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, action=OrderAction.CLOSE, submission_mid=100.0),
            _event(2, OrderEventType.FILLED, 1, action=OrderAction.CLOSE, fill_price=99.9),
        ])

        assert slip.slippage == pytest.approx(0.1)

    def test_every_part_of_a_split_fill_against_the_same_mid(self):
        derived = derive_fill_slippage([
            _event(1, OrderEventType.SUBMITTED, 1, submission_mid=100.0),
            _event(2, OrderEventType.PARTIALLY_FILLED, 1, fill_price=100.02),
            _event(3, OrderEventType.FILLED, 1, fill_price=100.08),
        ], {})

        assert set(derived) == {2, 3}, 'keyed by the fill event'
        assert {s.reference_price for s in derived.values()} == {100.0}
        assert derived[2].slippage == pytest.approx(0.02)
        assert derived[3].slippage == pytest.approx(0.08)

    def test_without_a_mid_the_fill_is_not_measured(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1),
            _event(2, OrderEventType.FILLED, 1, fill_price=100.05),
        ])

        assert slip.measured_against is SlippageReference.SUBMISSION_MID
        assert (slip.reference_price, slip.slippage, slip.slippage_pct) == (None, None, None)


class TestALimitIsMeasuredAgainstTheLimitInForce:

    def test_after_a_modify_the_new_limit(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.LIMIT, limit_price=99.0),
            _event(2, OrderEventType.MODIFIED, 1, OrderType.LIMIT, limit_price=99.5),
            _event(3, OrderEventType.FILLED, 1, OrderType.LIMIT, limit_price=99.5, fill_price=99.4),
        ])

        assert slip.measured_against is SlippageReference.LIMIT_PRICE
        assert slip.reference_price == 99.5
        assert slip.slippage == pytest.approx(-0.1), 'a buy below its limit is an improvement'

    def test_a_fill_that_carries_no_limit_takes_the_latest_one_before_it(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.LIMIT, limit_price=99.0),
            _event(2, OrderEventType.MODIFIED, 1, OrderType.LIMIT, limit_price=99.5),
            _event(3, OrderEventType.FILLED, 1, OrderType.LIMIT, fill_price=99.5),
        ])

        assert slip.reference_price == 99.5

    def test_an_adopted_resting_order_is_measured_like_one_it_sent(self):
        """A cold start adopts a limit a previous session left at the venue (#355)."""
        slip = _one([
            _event(1, OrderEventType.ADOPTED, 1, OrderType.LIMIT, limit_price=99.0),
            _event(2, OrderEventType.FILLED, 1, OrderType.LIMIT, fill_price=99.0),
        ])

        assert (slip.measured_against, slip.slippage) == (SlippageReference.LIMIT_PRICE, 0.0)


class TestAStopIsMeasuredAgainstItsTrigger:

    def test_a_triggered_stop_limit_in_the_simulation(self):
        """The simulation rewrites a triggered stop-limit to a limit, so its fill names the type it
        was submitted as and carries no trigger: the trigger comes from the event that did."""
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.STOP_LIMIT,
                   trigger_price=101.0, limit_price=101.5),
            _event(2, OrderEventType.TRIGGERED, 1, OrderType.STOP_LIMIT,
                   trigger_price=101.0, limit_price=101.5),
            _event(3, OrderEventType.FILLED, 1, OrderType.STOP_LIMIT,
                   limit_price=101.5, fill_price=101.3),
        ])

        assert slip.measured_against is SlippageReference.TRIGGER_PRICE
        assert slip.reference_price == 101.0
        assert slip.slippage == pytest.approx(0.3)

    def test_the_same_order_in_a_session_gives_the_same_answer(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.STOP_LIMIT,
                   trigger_price=101.0, limit_price=101.5),
            _event(2, OrderEventType.FILLED, 1, OrderType.STOP_LIMIT,
                   trigger_price=101.0, limit_price=101.5, fill_price=101.3),
        ])

        assert (slip.measured_against, slip.reference_price) == (
            SlippageReference.TRIGGER_PRICE, 101.0)
        assert slip.slippage == pytest.approx(0.3)

    def test_a_moved_trigger_is_the_one_in_force(self):
        slip = _one([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.STOP, trigger_price=101.0),
            _event(2, OrderEventType.MODIFIED, 1, OrderType.STOP, trigger_price=100.5),
            _event(3, OrderEventType.FILLED, 1, OrderType.STOP, fill_price=100.6),
        ])

        assert slip.reference_price == 100.5
        assert slip.slippage == pytest.approx(0.1)


class TestAProtectiveCloseIsMeasuredAgainstTheLevelThatFired:
    """The simulation fills it AT the level, so it reads 0; a session reads the real distance —
    the difference a parity check measures. Against the mid it would read the overshoot of the
    tick that crossed the level. The executor keeps the level per fill as it books it."""

    def _stream(self, exit_fill):
        return [
            _event(1, OrderEventType.SUBMITTED, 1, submission_mid=100.0),
            _event(2, OrderEventType.FILLED, 1, fill_price=100.0),
            _event(3, OrderEventType.SUBMITTED, 3, action=OrderAction.CLOSE, submission_mid=94.8),
            _event(4, OrderEventType.FILLED, 3, action=OrderAction.CLOSE, fill_price=exit_fill),
        ]

    def test_the_simulation_fills_at_the_level_and_reads_zero(self):
        derived = derive_fill_slippage(self._stream(95.0), {4: 95.0})

        assert derived[4].measured_against is SlippageReference.PROTECTIVE_LEVEL
        assert (derived[4].reference_price, derived[4].slippage) == (95.0, 0.0)
        assert derived[2].measured_against is SlippageReference.SUBMISSION_MID, 'the entry is not'

    def test_a_session_reads_how_far_below_the_stop_it_sold(self):
        derived = derive_fill_slippage(self._stream(94.62), {4: 95.0})

        assert derived[4].slippage == pytest.approx(0.38)

    def test_a_take_profit_is_measured_against_its_target(self):
        derived = derive_fill_slippage(self._stream(105.1), {4: 105.0})

        assert derived[4].reference_price == 105.0
        assert derived[4].slippage == pytest.approx(-0.1), 'sold above the target'

    def test_every_part_of_a_close_sent_twice_finds_the_level(self):
        """The venue ended the first close after a part of it; the stop's second close took the
        rest. Both parts are protective fills, as both trade records say."""
        derived = derive_fill_slippage([
            _event(1, OrderEventType.SUBMITTED, 1, submission_mid=100.0),
            _event(2, OrderEventType.FILLED, 1, fill_price=100.0),
            _event(3, OrderEventType.SUBMITTED, 3, action=OrderAction.CLOSE, submission_mid=94.8),
            _event(4, OrderEventType.PARTIALLY_FILLED, 3, action=OrderAction.CLOSE,
                   fill_price=94.9),
            _event(5, OrderEventType.CANCELLED, 3, action=OrderAction.CLOSE),
            _event(6, OrderEventType.SUBMITTED, 6, action=OrderAction.CLOSE, submission_mid=94.6),
            _event(7, OrderEventType.FILLED, 6, action=OrderAction.CLOSE, fill_price=94.7),
        ], {4: 95.0, 7: 95.0})

        assert [derived[seq].measured_against for seq in (4, 7)] == [
            SlippageReference.PROTECTIVE_LEVEL] * 2
        assert derived[4].slippage == pytest.approx(0.1)
        assert derived[7].slippage == pytest.approx(0.3)

    def test_a_close_the_logic_chose_is_measured_against_its_mid(self):
        derived = derive_fill_slippage(self._stream(94.62), {})

        assert derived[4].measured_against is SlippageReference.SUBMISSION_MID
        assert derived[4].reference_price == 94.8

    def test_the_level_belongs_to_the_reason_that_closed(self):
        """Every close reason answers — the level that fired, or none."""
        expected = {CloseReason.SL_TRIGGERED: 95.0, CloseReason.TP_TRIGGERED: 105.0,
                    CloseReason.MANUAL: None, CloseReason.SCENARIO_END: None}

        assert set(expected) == set(CloseReason), 'a new close reason needs its answer here'
        for reason, level in expected.items():
            assert reason.fired_level(95.0, 105.0) == level


class TestWhatIsNotAFill:

    def test_events_without_a_fill_and_untied_events_are_left_out(self):
        derived = derive_fill_slippage([
            _event(1, OrderEventType.SUBMITTED, 1, OrderType.LIMIT, limit_price=99.0),
            _event(2, OrderEventType.ACCEPTED, 1, OrderType.LIMIT, limit_price=99.0),
            _event(3, OrderEventType.CANCELLED, 1, OrderType.LIMIT, limit_price=99.0),
            OrderEvent(seq=4, event_type=OrderEventType.FILLED, order_id='late_answer',
                       fill_price=100.0),
        ], {})

        assert derived == {}
