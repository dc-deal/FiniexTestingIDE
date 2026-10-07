"""
FiniexTestingIDE - Order Events in the Simulation (#362)

What a backtest writes into the order-event stream, one order life at a time: the submission,
the simulated venue taking the order, a stop triggering, every cancel and amend and how it was
answered, the fill or the ending. Read the way a consumer reads it — grouped by the submission
each step belongs to, in `seq` order.

A backtest must write the same stream every time it runs, so nothing here may read the wall
clock: `ts_init` stays empty, and two identical runs are compared field by field.
"""

from dataclasses import asdict
from typing import List

import pytest

from python.framework.types.trading_env_types.order_event_types import (
    OrderEvent,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderCapabilities,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderType,
)
from python.framework.types.trading_env_types.stress_test_types import (
    StressTestConfig,
    StressTestRejectOrderConfig,
)
from tests.framework.order_events.conftest import (
    limit_order,
    make_simulator,
    market_order,
    order_steps,
    record_events,
    sim_tick,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])

_RESTING_BUY = 40000.0
_BUY_STOP = 50100.0


def _stop(order_type: OrderType = OrderType.STOP, limit: float = None) -> OpenOrderRequest:
    """A BUY stop above the market, or a stop-limit with its limit beside it."""
    return OpenOrderRequest(symbol='BTCUSD', order_type=order_type,
                            direction=OrderDirection.LONG, lots=0.01,
                            stop_price=_BUY_STOP, price=limit)


class TestAMarketOrder:
    """Taken and filled in one instant, after the delay the backtest models."""

    @ACCOUNT_MODELS
    def test_it_is_submitted_accepted_and_filled(self, spot_mode):
        sim = make_simulator(spot_mode, latency_ms=40)
        events = record_events(sim)

        sim.open_order(market_order())
        sim_tick(sim, msc=1050)

        assert order_steps(events) == [['submitted', 'accepted', 'filled']]

    def test_the_acceptance_carries_the_modelled_delay(self):
        sim = make_simulator(spot_mode=False, latency_ms=40)
        events = record_events(sim)

        sim.open_order(market_order())
        sim_tick(sim, msc=1050)

        accepted = [e for e in events if e.event_type is OrderEventType.ACCEPTED]
        assert [e.in_flight_ms for e in accepted] == [40.0]

    def test_the_submission_carries_the_market_it_was_sent_into(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)

        sim.open_order(market_order())

        submitted = events[0]
        assert submitted.event_type is OrderEventType.SUBMITTED
        assert submitted.submission_mid == pytest.approx((49999.0 + 50001.0) / 2)
        assert submitted.submission_time_msc == 1000

    def test_the_fill_says_what_executed_and_what_it_cost(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)

        sim.open_order(market_order())
        sim_tick(sim, msc=1001)

        filled = events[-1]
        assert filled.event_type is OrderEventType.FILLED
        assert (filled.action, filled.order_type, filled.direction, filled.lots) == (
            OrderAction.OPEN, OrderType.MARKET, OrderDirection.LONG, 0.01)
        assert filled.fill_price is not None and filled.fee is not None
        assert filled.fee_currency == 'USD'


class TestARestingOrder:
    """Taken on arrival, then it waits — and every way it can end is a step of its own."""

    @ACCOUNT_MODELS
    def test_a_limit_is_accepted_on_arrival_and_filled_when_the_market_comes(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)

        sim.open_order(limit_order(price=49900.0))
        sim_tick(sim, msc=1001)                                   # arrives and rests
        assert order_steps(events) == [['submitted', 'accepted']]

        sim_tick(sim, msc=1002, bid=49800.0, ask=49850.0)        # the market reaches it
        assert order_steps(events) == [['submitted', 'accepted', 'filled']]
        assert events[1].limit_price == 49900.0

    def test_a_strategy_cancel_is_asked_for_then_carried_out(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)
        order_id = sim.open_order(limit_order()).order_id
        sim_tick(sim, msc=1001)

        assert sim.cancel_limit_order(order_id)
        sim_tick(sim, msc=1003)

        assert order_steps(events) == [['submitted', 'accepted', 'cancel_requested', 'cancelled']]
        requested, cancelled = events[-2:]
        assert (requested.initiator, requested.end_reason) == (
            OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED), 'as the live request says'
        assert (cancelled.initiator, cancelled.end_reason) == (
            OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED)

    def test_a_fill_that_overtakes_the_cancel_refuses_it_first(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)
        order_id = sim.open_order(limit_order(price=49900.0)).order_id
        sim_tick(sim, msc=1001)                                   # arrives and rests

        assert sim.cancel_limit_order(order_id)                   # resolves a millisecond on
        sim_tick(sim, msc=1001, bid=49800.0, ask=49850.0)        # the fill comes first

        assert order_steps(events) == [[
            'submitted', 'accepted', 'cancel_requested', 'cancel_rejected', 'filled']]

    def test_an_amend_is_asked_for_then_carried_out_at_its_new_price(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)
        order_id = sim.open_order(limit_order()).order_id
        sim_tick(sim, msc=1001)

        assert sim.modify_limit_order(order_id, new_price=41000.0).success
        sim_tick(sim, msc=1003)

        assert order_steps(events) == [['submitted', 'accepted', 'modify_requested', 'modified']]
        assert [e.limit_price for e in events[-2:]] == [41000.0, 41000.0]

    def test_an_amend_overtaken_by_a_fill_is_refused_first(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)
        order_id = sim.open_order(limit_order(price=49900.0)).order_id
        sim_tick(sim, msc=1001)

        assert sim.modify_limit_order(order_id, new_price=41000.0).success
        sim_tick(sim, msc=1001, bid=49800.0, ask=49850.0)

        assert order_steps(events) == [[
            'submitted', 'accepted', 'modify_requested', 'modify_rejected', 'filled']]

    def test_a_local_refusal_of_a_cancel_writes_nothing(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)

        assert not sim.cancel_limit_order('pos_btcusd_404')

        assert events == [], 'the answer to a refused request is its return value'

    def test_the_data_end_expires_resting_and_travelling_orders_alike(self):
        sim = make_simulator(spot_mode=False, latency_ms=500)
        events = record_events(sim)
        sim.open_order(limit_order())
        sim_tick(sim, msc=1600)                                   # the limit rests
        sim.open_order(market_order())                                 # this one travels

        sim.finish_remaining_orders()

        assert order_steps(events) == [
            ['submitted', 'accepted', 'expired'],
            ['submitted', 'expired'],
        ]
        assert {e.end_reason for e in events if e.event_type is OrderEventType.EXPIRED} == {
            OrderEndReason.SCENARIO_END}


class TestAStopOrder:
    """A stop rests, triggers, and only then fills — the trigger is a step of its own."""

    @ACCOUNT_MODELS
    def test_a_stop_triggers_and_fills_at_the_market(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)

        sim.open_order(_stop())
        sim_tick(sim, msc=1001)                                   # rests above the market
        sim_tick(sim, msc=1002, bid=50140.0, ask=50150.0)        # the market reaches it

        assert order_steps(events) == [['submitted', 'accepted', 'triggered', 'filled']]
        triggered = events[2]
        assert triggered.trigger_price == _BUY_STOP

    def test_a_stop_limit_triggers_and_then_fills_as_a_limit(self):
        sim = make_simulator(spot_mode=False)
        events = record_events(sim)

        sim.open_order(_stop(OrderType.STOP_LIMIT, limit=50200.0))
        sim_tick(sim, msc=1001)
        sim_tick(sim, msc=1002, bid=50140.0, ask=50150.0)

        steps = order_steps(events)
        assert steps[0][:3] == ['submitted', 'accepted', 'triggered']
        assert steps[0][-1] == 'filled'
        assert all(e.order_type is OrderType.STOP_LIMIT for e in events), (
            'the stream names the order that was submitted, as the row does')


class TestAStopLimitWithARequestOnItsWay:
    """
    A stop-limit triggers while a cancel or a modification of it is on its way. Only a fill in
    the same pass overtakes the request; a limit that rests keeps it — and a modification that
    moves the stop no longer applies to the limit the order has become, while one that leaves
    the stop alone does.
    """

    @pytest.fixture
    def sim(self, monkeypatch):
        """A simulator whose venue declares stop orders, as the Kraken and MT5 adapters do."""
        sim = make_simulator(spot_mode=False)
        monkeypatch.setattr(sim.broker.adapter, 'get_order_capabilities', lambda: OrderCapabilities(
            stop_orders=True, stop_limit_orders=True))
        return sim

    def _resting_stop_limit(self, sim, limit: float = 50050.0) -> str:
        """A BUY stop-limit (stop 50100) resting above the market."""
        order_id = sim.open_order(_stop(OrderType.STOP_LIMIT, limit=limit)).order_id
        sim_tick(sim, msc=1001)
        return order_id

    def test_a_cancel_survives_a_trigger_that_does_not_fill(self, sim):
        events = record_events(sim)
        order_id = self._resting_stop_limit(sim)
        assert sim.cancel_stop_order(order_id)
        sim_tick(sim, msc=1001, bid=50140.0, ask=50150.0)    # triggers; the limit is not reached
        sim_tick(sim, msc=1005, bid=50140.0, ask=50150.0)    # the cancel is carried out

        assert order_steps(events) == [[
            'submitted', 'accepted', 'cancel_requested', 'triggered', 'cancelled']]

    def test_a_fill_in_the_same_pass_overtakes_the_cancel(self, sim):
        events = record_events(sim)
        order_id = self._resting_stop_limit(sim, limit=50200.0)
        assert sim.cancel_stop_order(order_id)
        sim_tick(sim, msc=1001, bid=50140.0, ask=50150.0)    # triggers, and the limit is reached

        assert order_steps(events) == [[
            'submitted', 'accepted', 'cancel_requested', 'cancel_rejected', 'triggered', 'filled']]

    def test_an_amend_of_the_stop_is_refused_and_the_limit_keeps_its_price(self, sim):
        events = record_events(sim)
        order_id = self._resting_stop_limit(sim)
        assert sim.modify_stop_order(
            order_id, new_stop_price=50120.0, new_limit_price=50060.0).success
        sim_tick(sim, msc=1001, bid=50140.0, ask=50150.0)    # triggers; the limit rests
        sim_tick(sim, msc=1005, bid=50100.0, ask=50110.0)    # above the limit: no fill

        assert order_steps(events) == [[
            'submitted', 'accepted', 'modify_requested', 'modify_rejected', 'triggered']]
        resting = sim.get_active_orders()[0]
        assert resting.entry_price == 50050.0, (
            'the amend wrote the new trigger into the limit, and a BUY filled above its limit')

    def test_an_amend_that_leaves_the_stop_alone_applies_to_the_limit(self, sim):
        events = record_events(sim)
        order_id = self._resting_stop_limit(sim)
        assert sim.modify_stop_order(order_id, new_limit_price=50060.0).success
        sim_tick(sim, msc=1001, bid=50140.0, ask=50150.0)    # triggers; the limit rests
        sim_tick(sim, msc=1005, bid=50100.0, ask=50110.0)    # the amend is carried out

        assert order_steps(events) == [[
            'submitted', 'accepted', 'modify_requested', 'triggered', 'modified']]
        resting = sim.get_active_orders()[0]
        assert resting.entry_price == 50060.0
        assert resting.order_kwargs['limit_price'] == 50060.0


class TestAPositionsCloses:
    """A close is an order of its own — the strategy's, or the protective level's."""

    @ACCOUNT_MODELS
    def test_a_close_is_submitted_accepted_and_filled_for_its_position(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)
        sim.open_order(market_order())
        sim_tick(sim, msc=1001)
        position = sim.get_open_positions()[0]

        sim.close_position(position.position_id)
        sim_tick(sim, msc=1002)

        entry, close = order_steps(events)
        assert close == ['submitted', 'accepted', 'filled']
        closing = [e for e in events if e.action is OrderAction.CLOSE]
        assert {(e.position_id, e.direction, e.symbol) for e in closing} == {
            (position.position_id, OrderDirection.LONG, 'BTCUSD')}, (
            'a close states its POSITION, and the position\'s direction')

    @ACCOUNT_MODELS
    def test_a_breached_stop_loss_exits_through_an_order_of_its_own(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)
        sim.open_order(market_order(stop_loss=49000.0))
        sim_tick(sim, msc=1001)                                   # the entry fills
        sim_tick(sim, msc=1002, bid=48900.0, ask=48902.0)        # the level is breached

        entry, exit_ = order_steps(events)
        assert entry == ['submitted', 'accepted', 'filled']
        assert exit_ == ['submitted', 'accepted', 'filled']
        exit_events = [e for e in events if e.action is OrderAction.CLOSE]
        assert {(e.order_type, e.symbol, e.direction, e.lots) for e in exit_events} == {
            (OrderType.MARKET, 'BTCUSD', OrderDirection.LONG, 0.01)}


class TestARefusal:
    """Refused here is denied and never submitted; refused by the simulated venue is rejected."""

    @ACCOUNT_MODELS
    def test_a_denial_was_never_submitted(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)

        sim.open_order(market_order(lots=1e-9))

        assert [(e.event_type, e.submitted_seq) for e in events] == [
            (OrderEventType.DENIED, None)]

    @ACCOUNT_MODELS
    def test_a_refused_close_names_its_position(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)
        sim.open_order(market_order())
        sim_tick(sim, msc=1001)
        position = sim.get_open_positions()[0]

        refused = sim.close_position(position.position_id, lots=1e-9)

        assert refused.is_refused and refused.position_id == position.position_id
        denied = events[-1]
        assert (denied.event_type, denied.position_id, denied.action, denied.submitted_seq) == (
            OrderEventType.DENIED, position.position_id, OrderAction.CLOSE, None)

    def test_a_stress_test_refusal_answers_the_submission(self):
        stress = StressTestConfig(reject_open_order=StressTestRejectOrderConfig(
            enabled=True, seed=1, probability=1.0))
        sim = make_simulator(spot_mode=False, latency_ms=40, stress=stress)
        events = record_events(sim)

        sim.open_order(market_order())
        sim_tick(sim, msc=1050)

        assert order_steps(events) == [['submitted', 'rejected']]
        assert events[-1].in_flight_ms == 40.0, 'a refusal answers its submission'


class TestTheStreamItself:
    """Ordered by `seq`, joined by `submitted_seq`, and the same every time it is run."""

    def _session(self) -> List[OrderEvent]:
        """A short backtest with every kind of order life in it."""
        sim = make_simulator(spot_mode=False, latency_ms=20)
        events = record_events(sim)
        sim.open_order(market_order(stop_loss=49000.0))
        limit_id = sim.open_order(limit_order()).order_id
        sim.open_order(_stop())
        sim_tick(sim, msc=1030)
        sim.cancel_limit_order(limit_id)
        sim_tick(sim, msc=1060, bid=50140.0, ask=50150.0)
        sim_tick(sim, msc=1090, bid=48900.0, ask=48902.0)
        sim_tick(sim, msc=1120)
        sim.finish_remaining_orders()
        return events

    def test_seq_is_strictly_increasing(self):
        events = self._session()
        assert [e.seq for e in events] == list(range(1, len(events) + 1))

    def test_every_step_names_a_submission_that_was_recorded(self):
        events = self._session()
        submissions = {e.seq for e in events if e.event_type is OrderEventType.SUBMITTED}
        assert submissions
        assert {e.submitted_seq for e in events} <= submissions

    def test_nothing_reads_the_wall_clock(self):
        events = self._session()
        assert all(e.ts_init is None for e in events)
        assert all(e.event_time is not None for e in events)

    def test_two_identical_backtests_write_identical_streams(self):
        first, second = self._session(), self._session()
        assert [asdict(e) for e in first] == [asdict(e) for e in second]
