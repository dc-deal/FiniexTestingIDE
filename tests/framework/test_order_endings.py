"""
FiniexTestingIDE - Order Endings Tests (#362)

One status per way an order ends, in both pipelines. A refusal made here is `denied` and one the
venue made is `rejected`; a cancel says who asked and why; an order still out when the data or
the session ends gets a row of its own; an order nobody could account for is `unaccounted` and
reaches the strategy through a hook of its own. They used to share `rejected` — a typo in a lot
size, a refused amend, a give-up at the fill timeout — so a count, a cooldown and a report could
not tell them apart.

The executor counts are taken where each row is booked, and the declaration that maps a status
to its count is held complete in both directions.
"""

from dataclasses import fields
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.reporting.aggregation.figure_catalog import COLUMN_REDUCTION
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.aggregation_types import Reduction
from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    ExecutionStatsRow,
    ExecutionStatsTotals,
    RunResultRow,
    RunSummary,
)
from python.framework.types.decision_event_types import (
    DecisionEvent,
    DecisionEventType,
    OrderCancelledEvent,
    SessionEndSeverity,
)
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryResponse
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectionReason,
)
from python.framework.types.trading_env_types.stress_test_types import (
    StressTestConfig,
    StressTestRejectOrderConfig,
)
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_COUNT_FIELDS,
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])

_BID = 49999.0
_ASK = 50001.0
_RESTING_BUY = 40000.0


def _simulator(spot_mode: bool, latency_ms: int = 0,
               stress: StressTestConfig = None) -> TradeSimulator:
    """
    The simulation executor with one BTCUSD tick fed.

    Args:
        spot_mode: The account model
        latency_ms: The inbound latency, fixed — 0 lets an order arrive on the next tick
        stress: A stress-test configuration, or None

    Returns:
        The simulator, ready to take orders
    """
    sim = TradeSimulator(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT,
                                   MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('OrderEndingsSim'),
        seeds={'inbound_latency_seed': 42},
        stress_test_config=stress,
        inbound_latency_min_ms=latency_ms,
        inbound_latency_max_ms=latency_ms,
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
    )
    _tick(sim, msc=1000)
    return sim


def _tick(sim: TradeSimulator, msc: int, bid: float = _BID, ask: float = _ASK) -> None:
    """
    One BTCUSD tick, at the harness prices unless told otherwise.

    Args:
        sim: The simulator
        msc: The tick's millisecond stamp
        bid: The bid
        ask: The ask
    """
    sim.on_tick(TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=bid, ask=ask, collected_msc=msc, time_msc=msc,
    ))


def _limit(price: float = _RESTING_BUY) -> OpenOrderRequest:
    """A resting BUY limit far below the market."""
    return OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.LIMIT,
                            direction=OrderDirection.LONG, lots=0.01, price=price)


def _market(lots: float = 0.01, stop_loss: Optional[float] = None) -> OpenOrderRequest:
    """A market BUY, with a stop-loss where one is given."""
    return OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                            direction=OrderDirection.LONG, lots=lots, stop_loss=stop_loss)


def _ended(history: List[OrderResult]) -> List[OrderResult]:
    """Every row that ends an order — everything but the submission rows."""
    return [r for r in history if r.status is not OrderStatus.PENDING]


class TestTheStatusMapIsComplete:
    """The declaration a count is read through, held to its subject in both directions."""

    def test_every_status_is_counted_or_declared_uncounted(self):
        assert set(EXECUTION_STATS_FIELD_BY_STATUS) == set(OrderStatus)

    def test_every_status_count_names_its_status(self):
        for status, field_name in EXECUTION_STATS_FIELD_BY_STATUS.items():
            if field_name is not None:
                assert field_name == f'orders_{status.value}'

    def test_every_order_count_is_a_status_a_submission_or_an_adoption(self):
        counted = {name for name in EXECUTION_STATS_FIELD_BY_STATUS.values() if name}
        order_counts = {f.name for f in fields(ExecutionStats) if f.name.startswith('orders_')}
        assert order_counts == counted | {'orders_submitted', 'orders_adopted'}

    @pytest.mark.parametrize('model', [
        ExecutionStatsRow, ExecutionStatsTotals, RunSummary, RunResultRow,
        AggregatedPortfolioRow])
    def test_every_model_passing_the_counts_on_carries_every_count(self, model):
        assert set(EXECUTION_COUNT_FIELDS) <= set(model.model_fields)

    def test_the_ledger_has_a_summed_column_for_every_count(self):
        for name in EXECUTION_COUNT_FIELDS:
            assert name in LEDGER_COLUMNS
            assert COLUMN_REDUCTION[name] is Reduction.SUM


class TestARefusalSaysWhoRefused:
    """Refused here is denied, refused by the venue is rejected — in both pipelines."""

    @ACCOUNT_MODELS
    def test_a_lot_size_refusal_is_denied_and_never_submitted(self, spot_mode):
        sim = _simulator(spot_mode)
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL, spot_mode=spot_mode)
        live = mock.create_executor()
        mock.feed_tick(live, bid=_BID, ask=_ASK)

        for executor in (sim, live):
            result = executor.open_order(_market(lots=1e-9))
            assert result.status is OrderStatus.DENIED
            assert result.rejection_reason is RejectionReason.INVALID_LOT_SIZE
            stats = executor.get_execution_stats()
            assert (stats.orders_denied, stats.orders_submitted) == (1, 0)

    def test_a_close_of_a_missing_position_is_denied_with_its_own_reason(self):
        sim = _simulator(spot_mode=False)
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        live = mock.create_executor()
        mock.feed_tick(live, bid=_BID, ask=_ASK)

        for executor in (sim, live):
            result = executor.close_position('pos_btcusd_404')
            assert result.status is OrderStatus.DENIED
            assert result.rejection_reason is RejectionReason.POSITION_NOT_FOUND
            assert executor.get_order_history()[-1] is result, 'the denial is booked'

    def test_the_stress_test_is_a_venue_refusal_and_is_heard(self):
        stress = StressTestConfig(reject_open_order=StressTestRejectOrderConfig(
            enabled=True, seed=1, probability=1.0))
        sim = _simulator(spot_mode=False, stress=stress)
        heard: List[OrderResult] = []
        sim.add_order_outcome_listener(lambda direction, result, pending: heard.append(result))

        sim.open_order(_market())
        _tick(sim, msc=1001)

        assert [r.status for r in heard] == [OrderStatus.REJECTED], (
            'the strategy and the cooldown used to hear nothing of a stress-test refusal')
        assert sim.get_execution_stats().orders_rejected == 1


class TestEveryEndingHasARowInTheSimulation:
    """A cancel, and an order still out when the data ends, each end with a row of their own."""

    def test_a_strategy_cancel_ends_cancelled_by_the_strategy(self):
        sim = _simulator(spot_mode=False)
        events: List[DecisionEvent] = []
        sim.set_decision_event_sink(events.append)
        order_id = sim.open_order(_limit()).order_id
        _tick(sim, msc=1001)                                  # arrives, and rests

        assert sim.cancel_limit_order(order_id)
        _tick(sim, msc=1003)                                  # the cancel resolves

        ended = _ended(sim.get_order_history())
        assert [(r.status, r.initiator, r.end_reason) for r in ended] == [
            (OrderStatus.CANCELLED, OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED)]
        cancels = [e for e in events if isinstance(e, OrderCancelledEvent)]
        assert len(cancels) == len(ended) and all(
            event.result.is_mask_of(row) for event, row in zip(cancels, ended)), \
            'the event shows the booked row, through its read-only mask (#654)'

    def test_the_data_end_expires_resting_and_travelling_orders_alike(self):
        sim = _simulator(spot_mode=False, latency_ms=500)
        sim.open_order(_limit())
        _tick(sim, msc=1600)                                  # the limit arrives and rests
        sim.open_order(_market())                             # this one is still travelling

        sim.finish_remaining_orders()

        ended = _ended(sim.get_order_history())
        assert [(r.order_type, r.status, r.end_reason) for r in ended] == [
            (OrderType.LIMIT, OrderStatus.EXPIRED, OrderEndReason.SCENARIO_END),
            (OrderType.MARKET, OrderStatus.EXPIRED, OrderEndReason.SCENARIO_END),
        ], 'an order still on its way used to end without any row'
        assert sim.get_execution_stats().orders_expired == 2


class _ActsOnTheWayOut:
    """A logic that sends orders from its session-end event and records what it hears after."""

    def __init__(self, act, then=None) -> None:
        self.heard: List[str] = []
        self._act = act
        self._then = then

    @classmethod
    def get_subscribed_events(cls):
        return set(DecisionEventType)

    def on_order_filled(self, event) -> None:
        self.heard.append(f'filled:{event.order_id}')

    def on_order_rejected(self, event) -> None:
        self.heard.append(f'rejected:{event.order_id}')

    def on_order_unaccounted(self, event) -> None:
        self.heard.append(f'unaccounted:{event.order_id}')

    def on_order_cancelled(self, event) -> None:
        self.heard.append(f'cancelled:{event.order_id}')

    def on_partial_close(self, event) -> None:
        self.heard.append('partial_close')

    def on_position_closed(self, event) -> None:
        self.heard.append(f'closed:{event.position_id}')

    def on_session_end(self, event) -> None:
        self.heard.append('session_end')
        self._act()

    def on_session_end_order_outcome(self, event) -> None:
        self.heard.append(f'outcome:{event.order_id}:{event.status.value}')
        if self._then is not None:
            then, self._then = self._then, None
            then()


def _end_the_session(sim: TradeSimulator, logic: _ActsOnTheWayOut) -> None:
    """
    The simulation's end, in the order its tick loop runs it once the data is over.

    Args:
        sim: The simulator
        logic: What hears the decision events
    """
    dispatcher = DecisionEventDispatcher(logic, sim, GlobalLogger('SessionEndOrders'))
    dispatcher.emit_session_end()
    sim.settle_session_end_orders()
    dispatcher.emit_session_end_orders()
    sim.withdraw_unsent_orders()
    sim.finish_remaining_orders()


class TestAnOrderSentAtTheSessionEndInTheSimulation:
    """
    An order the strategy sends from on_session_end ends as it ends live (#642).

    Live, the stop waits once more and books its fill; in a backtest no tick followed, so it
    expired and a strategy closing on its way out ended flat live and open in its backtest. The
    simulation now settles it at the last quote and tells the strategy how it ended.
    """

    @ACCOUNT_MODELS
    def test_a_close_sent_from_the_event_ends_flat_and_is_heard(self, spot_mode):
        sim = _simulator(spot_mode)
        sim.open_order(_market())
        _tick(sim, msc=1001)
        position_id, = [p.position_id for p in sim.get_open_positions()]
        logic = _ActsOnTheWayOut(lambda: sim.close_position(position_id))

        _end_the_session(sim, logic)

        assert sim.get_open_positions() == []
        trade, = sim.get_trade_history()
        assert trade.exit_price == pytest.approx(_BID)
        assert logic.heard[-2:] == ['session_end', f'outcome:{position_id}:executed']

    @ACCOUNT_MODELS
    def test_a_market_order_sent_from_the_event_fills_at_the_last_quote(self, spot_mode):
        sim = _simulator(spot_mode)
        logic = _ActsOnTheWayOut(lambda: sim.open_order(_market()))

        _end_the_session(sim, logic)

        position, = sim.get_open_positions()
        assert position.entry_price == pytest.approx(_ASK)
        assert logic.heard[-1] == f'outcome:{position.position_id}:executed'

    def test_a_resting_order_sent_from_the_event_expires_and_is_heard(self):
        sim = _simulator(spot_mode=False)
        sent: List[str] = []
        logic = _ActsOnTheWayOut(lambda: sent.append(sim.open_order(_limit()).order_id))

        _end_the_session(sim, logic)

        ended = _ended(sim.get_order_history())
        assert [(r.order_id, r.status) for r in ended] == [(sent[0], OrderStatus.EXPIRED)]
        assert logic.heard[-1] == f'outcome:{sent[0]}:expired'

    def test_a_cancel_sent_from_the_event_is_applied(self):
        """A cancel waited for a tick that never came, and its order expired instead."""
        sim = _simulator(spot_mode=False)
        resting = sim.open_order(_limit()).order_id
        _tick(sim, msc=1001)                                  # the limit arrives and rests
        logic = _ActsOnTheWayOut(lambda: sim.cancel_limit_order(resting))

        _end_the_session(sim, logic)

        ended, = _ended(sim.get_order_history())
        assert (ended.order_id, ended.status, ended.initiator, ended.end_reason) == (
            resting, OrderStatus.CANCELLED, OrderInitiator.STRATEGY,
            OrderEndReason.CANCEL_REQUESTED)

    def test_an_order_sent_from_the_outcome_hook_is_withdrawn(self):
        sim = _simulator(spot_mode=False)
        sent: List[str] = []
        logic = _ActsOnTheWayOut(
            lambda: sim.open_order(_market()),
            then=lambda: sent.append(sim.open_order(_market()).order_id))

        _end_the_session(sim, logic)

        withdrawn, = [r for r in sim.get_order_history() if r.order_id == sent[0]
                      and r.status is not OrderStatus.PENDING]
        assert (withdrawn.status, withdrawn.initiator, withdrawn.end_reason) == (
            OrderStatus.CANCELLED, OrderInitiator.FRAMEWORK, OrderEndReason.SESSION_END)
        assert len(sim.get_open_positions()) == 1
        assert not any(sent[0] in heard for heard in logic.heard), 'the session was over'

    def test_an_order_already_on_its_way_still_expires_with_the_data(self):
        """Only what the strategy sends from the event is settled; the data ends the rest."""
        sim = _simulator(spot_mode=False, latency_ms=500)
        travelling = sim.open_order(_market()).order_id
        logic = _ActsOnTheWayOut(lambda: None)

        _end_the_session(sim, logic)

        ended, = _ended(sim.get_order_history())
        assert (ended.order_id, ended.status, ended.end_reason) == (
            travelling, OrderStatus.EXPIRED, OrderEndReason.SCENARIO_END)
        assert logic.heard == ['session_end']

    def test_after_an_emergency_end_nothing_is_settled(self):
        """A live stop does not wait through an EMERGENCY end, so neither does the simulation."""
        sim = _simulator(spot_mode=False)
        sim.open_order(_market())
        _tick(sim, msc=1001)
        position_id, = [p.position_id for p in sim.get_open_positions()]
        sim.request_session_end('operator pressed Ctrl+C twice', SessionEndSeverity.EMERGENCY)
        logic = _ActsOnTheWayOut(lambda: sim.close_position(position_id))

        _end_the_session(sim, logic)

        assert [p.position_id for p in sim.get_open_positions()] == [position_id]
        withdrawn = [r for r in sim.get_order_history()
                     if r.end_reason is OrderEndReason.SESSION_END]
        assert [r.status for r in withdrawn] == [OrderStatus.CANCELLED]


class TestAProtectiveExitInTheSimulation:
    """The simulation's stop-loss exit is an order as live's is, and it waits as live's does."""

    _STOP = 49000.0
    _BREACH = {'bid': 48900.0, 'ask': 48902.0}

    def test_it_counts_as_a_submitted_order(self):
        sim = _simulator(spot_mode=False)
        sim.open_order(_market(stop_loss=self._STOP))
        _tick(sim, msc=1001)                                  # the entry fills
        _tick(sim, msc=1002, **self._BREACH)                  # the stop is breached

        stats = sim.get_execution_stats()
        assert (stats.orders_submitted, stats.orders_executed, stats.sl_tp_triggered) == (
            2, 2, 1), 'the exit was counted executed and never submitted — 2/1 executed'

    def test_it_stands_aside_while_the_strategys_close_is_on_its_way(self):
        sim = _simulator(spot_mode=False, latency_ms=500)
        sim.open_order(_market(stop_loss=self._STOP))
        _tick(sim, msc=1600)                                  # the entry arrives and fills
        sim.close_position(sim.get_open_positions()[0].position_id)   # arrives at 2100
        _tick(sim, msc=1700, **self._BREACH)                  # the stop is breached meanwhile

        assert sim.get_open_positions(), 'the level was filled beneath the close on its way'

        _tick(sim, msc=2200, **self._BREACH)                  # the close arrives
        assert [(t.close_reason, t.exit_price) for t in sim.get_trade_history()] == [
            (CloseReason.MANUAL, self._BREACH['bid'])], 'live closes at the market here too'
        assert not [r for r in sim.get_order_history() if r.is_refused], (
            'the close arrived to find its position gone — a refusal live never produces')
        stats = sim.get_execution_stats()
        assert (stats.orders_submitted, stats.orders_executed, stats.sl_tp_triggered) == (
            2, 2, 0)


class TestEveryEndingHasARowLive:
    """Live, a cancel names its initiator, and what the session leaves unconfirmed is unaccounted."""

    def test_a_strategy_cancel_ends_cancelled_by_the_strategy(self):
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=_BID, ask=_ASK)
        order_id = executor.open_order(_limit()).order_id
        mock.await_submit_confirmation(executor)

        assert executor.cancel_limit_order(order_id)
        mock.await_submit_confirmation(executor)              # the cancel's answer drains

        ended = _ended(executor.get_order_history())
        assert [(r.status, r.initiator, r.end_reason) for r in ended] == [
            (OrderStatus.CANCELLED, OrderInitiator.STRATEGY, OrderEndReason.CANCEL_REQUESTED)]
        assert executor.get_execution_stats().orders_cancelled == 1

    @pytest.mark.parametrize('venue_status, expected', [
        (BrokerOrderStatus.CANCELLED, (OrderStatus.CANCELLED, OrderEndReason.VENUE_CANCELLED)),
        (BrokerOrderStatus.EXPIRED, (OrderStatus.EXPIRED, OrderEndReason.VENUE_EXPIRED)),
    ], ids=['cancelled', 'expired'])
    def test_an_order_the_venue_ended_unasked_is_the_venues(self, venue_status, expected):
        mock = MockOrderExecution(mode=MockExecutionMode.TIMEOUT)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=_BID, ask=_ASK)
        order_id = executor.open_order(_limit()).order_id
        mock.await_submit_confirmation(executor)
        resting = executor.get_active_orders()[0]

        executor._handle_query_response(QueryResponse(
            order_id=order_id,
            broker_response=BrokerResponse(
                broker_ref=resting.broker_ref, status=venue_status,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ))

        ended = _ended(executor.get_order_history())
        assert [(r.status, r.initiator, r.end_reason) for r in ended] == [
            (expected[0], OrderInitiator.VENUE, expected[1])]

    def test_an_order_ended_after_a_partial_fill_ends_as_that_fill(self):
        """
        What was executed is the order's fill; the rest never happened — one row (#362).

        A second, `cancelled` row used to follow, stating the executed size again, so one order
        counted as executed and as cancelled. Its pipeline twin booked the fill alone already.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.TIMEOUT)
        executor = mock.create_executor()
        events: List[DecisionEvent] = []
        executor.set_decision_event_sink(events.append)
        mock.feed_tick(executor, bid=_BID, ask=_ASK)
        order_id = executor.open_order(_limit()).order_id
        mock.await_submit_confirmation(executor)
        resting = executor.get_active_orders()[0]

        executor._handle_query_response(QueryResponse(
            order_id=order_id,
            broker_response=BrokerResponse(
                broker_ref=resting.broker_ref, status=BrokerOrderStatus.CANCELLED,
                filled_lots=0.004, fill_price=_RESTING_BUY,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ))

        ended = _ended(executor.get_order_history())
        assert [(r.status, r.executed_lots) for r in ended] == [(OrderStatus.EXECUTED, 0.004)]
        assert not [e for e in events if isinstance(e, OrderCancelledEvent)]
        assert executor.get_active_orders() == []
        stats = executor.get_execution_stats()
        assert (stats.orders_executed, stats.orders_cancelled) == (1, 0)

    def test_an_order_still_travelling_at_the_session_end_is_unaccounted(self):
        mock = MockOrderExecution(mode=MockExecutionMode.TIMEOUT)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=_BID, ask=_ASK)
        executor.open_order(_market())
        mock.await_submit_confirmation(executor)

        executor.finish_remaining_orders()

        ended = _ended(executor.get_order_history())
        assert [(r.status, r.initiator, r.end_reason) for r in ended] == [
            (OrderStatus.UNACCOUNTED, OrderInitiator.FRAMEWORK, OrderEndReason.SESSION_END)
        ], 'it used to end without any row, while the venue may hold it'
