"""
FiniexTestingIDE - Instant Resolution Tests (#460)

What the simulator does inside one instant of the backtest's timeline, driven the way the
dispatcher drives it: the clock moves to the instant, a modify or cancel due by then applies, each
tick of the slice moves the market and checks resting orders, and what the latency queue has due
fills once, at the prices standing then. And the one edge of the dispatcher itself that no other
suite reaches: a session end asked for while the events are delivered keeps the algo from running.
"""

from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import MagicMock

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.loop_context import LoopContext
from python.framework.loop.loop_event_dispatcher import dispatch_backtest_instant
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.loop_types.backtest_pass_kind_types import BacktestPassKind
from python.framework.types.loop_types.loop_event_types import DataSliceEvent
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.booking_day_harness import SYMBOL

_START = 1_780_000_000_000
_LATENCY_MS = 40


def _at(msc: int) -> datetime:
    """
    A stamp as the clock reads it.

    Args:
        msc: The stamp in epoch milliseconds

    Returns:
        The UTC instant
    """
    return datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc)


def _tick(offset: int, bid: float) -> TickData:
    """
    A tick that arrived when it happened, one unit of spread wide.

    Args:
        offset: When, in ms after the start
        bid: Its bid; the ask is one above

    Returns:
        The tick
    """
    msc = _START + offset
    return TickData(timestamp=_at(msc), symbol=SYMBOL, bid=bid, ask=bid + 1.0,
                    time_msc=msc, collected_msc=msc)


def _simulator() -> TradeSimulator:
    """
    A simulator whose every order takes the same latency, so its due time is known.

    Returns:
        The simulator
    """
    logger = ScenarioLogger(scenario_set_name='instant_resolution', scenario_name='instant',
                            run_timestamp=_at(_START), run_id=mint_run_id(_at(_START)))
    return TradeSimulator(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=_LATENCY_MS, inbound_latency_max_ms=_LATENCY_MS,
        spot_mode=False)


def _instant(simulator: TradeSimulator, offset: int, *ticks: TickData) -> None:
    """
    Run one instant through the simulator in the dispatcher's order.

    Args:
        simulator: The simulator
        offset: The instant's stamp, in ms after the start
        ticks: The instant's slice, empty for an instant that only carries a due time
    """
    stamp = _START + offset
    simulator.begin_instant(stamp)
    simulator.resolve_scheduled(stamp)
    for tick in ticks:
        simulator.apply_tick(tick)
    simulator.resolve_due(stamp)


def _order(order_type: OrderType, price: Optional[float] = None,
           stop_price: Optional[float] = None) -> OpenOrderRequest:
    """
    A long order of the smallest size.

    Args:
        order_type: Its type
        price: Its limit, where it has one
        stop_price: Its stop, where it has one

    Returns:
        The request
    """
    return OpenOrderRequest(symbol=SYMBOL, order_type=order_type, direction=OrderDirection.LONG,
                            lots=0.01, price=price, stop_price=stop_price)


class TestAnOrderFillsWhenItComesDue:

    def test_at_its_due_time_at_the_price_standing_then(self):
        """
        Sent at .000 with 40 ms of latency, the order fills at .040 — an instant without a tick —
        at the ask of the last tick before it, and the position opens at .040.
        """
        simulator = _simulator()
        _instant(simulator, 0, _tick(0, bid=100.0))
        simulator.open_order(_order(OrderType.MARKET))

        assert simulator.peek_next_due_msc() == _START + _LATENCY_MS
        _instant(simulator, _LATENCY_MS - 1)
        assert simulator.get_open_positions() == [], 'filled before its due time'

        _instant(simulator, _LATENCY_MS)
        positions = simulator.get_open_positions()
        assert len(positions) == 1
        assert positions[0].entry_price == 101.0
        assert positions[0].entry_time == _at(_START + _LATENCY_MS)

    def test_its_latency_counts_from_the_instant_it_was_sent_in(self):
        """
        Sent in an instant without a tick (a timer, a due time), an order's latency counts from
        that instant, not from the last tick — the base of every due time is the instant.
        """
        simulator = _simulator()
        _instant(simulator, 0, _tick(0, bid=100.0))
        _instant(simulator, 700)
        simulator.open_order(_order(OrderType.MARKET))

        assert simulator.peek_next_due_msc() == _START + 700 + _LATENCY_MS


class TestADueCancelComesFirst:
    """A limit resting at 95 while the ask stands at 101, then a tick whose ask reaches it."""

    @staticmethod
    def _resting_limit() -> TradeSimulator:
        """
        A simulator with the limit accepted and resting.

        Returns:
            The simulator
        """
        simulator = _simulator()
        _instant(simulator, 0, _tick(0, bid=100.0))
        simulator.open_order(_order(OrderType.LIMIT, price=95.0))
        _instant(simulator, _LATENCY_MS)
        assert len(simulator.get_active_orders_snapshot().active_limit_orders) == 1
        return simulator

    def test_without_a_cancel_the_tick_fills_it(self):
        simulator = self._resting_limit()
        _instant(simulator, 51, _tick(51, bid=90.0))

        assert len(simulator.get_open_positions()) == 1

    def test_a_cancel_due_in_the_instant_keeps_it_from_filling_there(self):
        simulator = self._resting_limit()
        _instant(simulator, 50)
        order_id = simulator.get_active_orders_snapshot().active_limit_orders[0].order_id
        assert simulator.cancel_limit_order(order_id)
        assert simulator.peek_next_due_msc() == _START + 51

        _instant(simulator, 51, _tick(51, bid=90.0))

        assert simulator.get_open_positions() == []
        assert simulator.get_active_orders_snapshot().active_limit_orders == []

    def test_a_modify_due_in_the_instant_applies_before_the_tick_is_checked(self):
        """
        The limit is moved from 95 to 85 at +50, due at +51. The tick at +51 offers an ask of 91:
        it would fill the old limit and does not reach the new one, so the order rests on.
        """
        simulator = self._resting_limit()
        _instant(simulator, 50)
        order_id = simulator.get_active_orders_snapshot().active_limit_orders[0].order_id
        assert simulator.modify_limit_order(order_id, new_price=85.0).success
        assert simulator.peek_next_due_msc() == _START + 51

        _instant(simulator, 51, _tick(51, bid=90.0))

        assert simulator.get_open_positions() == []
        resting, = simulator.get_active_orders_snapshot().active_limit_orders
        assert resting.entry_price == 85.0


class TestAStopInsideASweep:

    def test_fires_on_the_tick_that_reached_it(self):
        """
        Three ticks arrive in one millisecond; only the second reaches the buy stop at 105. Each
        tick of a slice is checked, so the stop fires at that tick's ask — checked only at the
        slice's last tick, it would not have fired at all.
        """
        simulator = _simulator()
        _instant(simulator, 0, _tick(0, bid=100.0))
        simulator.open_order(_order(OrderType.STOP, stop_price=105.0))
        _instant(simulator, _LATENCY_MS)
        assert len(simulator.get_active_orders_snapshot().active_stop_orders) == 1

        _instant(simulator, 100, _tick(100, bid=101.0), _tick(100, bid=105.0),
                 _tick(100, bid=102.0))

        positions = simulator.get_open_positions()
        assert len(positions) == 1
        assert positions[0].entry_price == 106.0


class TestTheClockIsTheInstants:
    """
    A collector's clock can run a little behind the venue's, so a recorded arrival can lie before
    the venue stamp (measured 2026-11-13: 0.66 % of the trusted ticks, by up to 50 ms).
    """

    @staticmethod
    def _stamped_after_its_arrival() -> TickData:
        """
        A tick the venue stamped 40 ms after it was recorded as received.

        Returns:
            The tick
        """
        return TickData(timestamp=_at(_START + 40), symbol=SYMBOL, bid=100.0, ask=101.0,
                        time_msc=_START + 40, collected_msc=_START)

    def test_on_the_timeline_the_tick_leaves_the_clock_at_the_instant(self):
        simulator = _simulator()
        _instant(simulator, 0, self._stamped_after_its_arrival())

        assert simulator.get_current_time() == _at(_START)

    def test_a_tick_handed_over_on_its_own_still_moves_the_clock_to_its_time(self):
        simulator = _simulator()
        simulator.on_tick(self._stamped_after_its_arrival())

        assert simulator.get_current_time() == _at(_START + 40)


class TestASessionEndAskedForInTheDelivery:

    def test_keeps_the_algo_from_running(self):
        """
        A hook that asks for the session end while the instant's events are delivered ends the
        session there: the algo does not compute on the slice.
        """
        requested: List[bool] = []
        simulator = MagicMock()
        simulator.get_current_time.return_value = _at(_START)
        simulator.is_session_end_requested.side_effect = lambda: bool(requested)
        dispatcher = MagicMock()
        dispatcher.drain.side_effect = lambda: requested.append(True)
        orchestrator = MagicMock()
        ctx = LoopContext(
            executor=simulator, bar_controller=MagicMock(), orchestrator=orchestrator,
            decision_logic=MagicMock(), decision_event_dispatcher=dispatcher, symbol=SYMBOL,
            booking=MagicMock(), seal_source=lambda: ([], MagicMock()), reads_closed_bars=False,
            profiling_enabled=False, profile_times={}, profile_counts={})
        tick = _tick(0, bid=100.0)
        events = (DataSliceEvent(tick.time_msc, tick.collected_msc, 0, (tick,)),)

        kind = dispatch_backtest_instant(events, simulator, ctx, MagicMock(), LoopPassRecord())

        assert kind is BacktestPassKind.NO_PASS
        orchestrator.process_tick.assert_not_called()
        simulator.apply_tick.assert_called_once_with(tick)
