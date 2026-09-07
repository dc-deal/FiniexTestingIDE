"""
FiniexTestingIDE - Protective Level Enforcement Tests (#500)

A stop_loss or take_profit declared on a live order used to be recorded on the position,
printed on the console, carried into the run report — and enforced by nobody. The submit
payload had no field for it, and the engine's check returned immediately unless the executor
was a simulation, on the stated assumption that the broker had taken it. The venue's own
answer, measured against the live API, confirmed it never received one.

These tests pin the property that replaces the assumption: a declared level is enforced, and
the report says by whom. They also pin the difference between the two pipelines, because it
is real and must not be smoothed over — the simulation closes AT the level, in-tick, while
live closes at whatever the venue gives it a round trip later.
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    ProtectiveLevelEnforcement,
)

_SYMBOL = 'BTCUSD'


def _live_with_protected_long(stop_loss=None, take_profit=None):
    """
    A live executor holding one filled LONG with the given levels.

    Args:
        stop_loss: Level to declare, or None
        take_profit: Level to declare, or None

    Returns:
        (mock, executor) — the harness and the executor holding the position
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=0.01, stop_loss=stop_loss, take_profit=take_profit))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    return mock, executor


def _simulator():
    """A simulation executor over the mock adapter. Returns: the simulator."""
    return TradeSimulator(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger('ProtectiveLevels'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )


class TestSomebodyEnforcesIt:
    """The crossing property: a declared level is never left with nobody behind it."""

    def test_both_pipelines_name_an_enforcer(self):
        """
        Neither executor may answer "nobody".

        This is the whole defect expressed as one assertion. Before #500 the live executor
        had no answer at all, and the silence read as the venue's yes.
        """
        _, live = _live_with_protected_long()
        for executor in (live, _simulator()):
            answer = executor.get_protective_level_enforcement()
            assert isinstance(answer, ProtectiveLevelEnforcement)

    def test_a_live_level_is_watched_by_this_process(self):
        """Today's live answer, and the report has to be able to say it out loud."""
        _, live = _live_with_protected_long()

        assert live.get_protective_level_enforcement() == ProtectiveLevelEnforcement.LOCAL


class TestTheAnswerIsPinnedForTheREALAdapters:
    """
    Both LOCAL assertions above run over MockBrokerAdapter — the one adapter that can
    never trip. That matters: the resolver is a CONSTANT with no override anywhere, so the
    only thing that can make it wrong is somebody wiring it to a capability, and the
    obvious candidate is the wrong field.

    `native_position_sl_tp` answers "who performs a MODIFY on an open position". It is not
    "who HOLDS this level", and on the two venues that exist the two answers point in
    opposite directions: #209 declares it True for MT5 while MT5's submit still attaches no
    level (VENUE would be claimed with nobody holding it — this issue's original defect one
    level up), and Kraken keeps it False by decision while a standalone stop order at the
    venue genuinely does hold one. What the resolver needs is a submit-side declaration
    (#500 calls it `native_order_attached_sl_tp`); nothing can declare it truthfully yet,
    so the constant stays and this test is what fails when it changes.

    Built from the REAL broker JSON, so it exercises the adapters a backtest actually runs
    on — 110 of 120 checked-in scenario declarations are `mt5`, and the sim reads a real
    Mt5Adapter object whose declarations it does not control.
    """

    _CONFIGS = (
        ('kraken_spot', BrokerType.KRAKEN_SPOT,
         'configs/brokers/kraken/kraken_spot_broker_config.json'),
        ('mt5', BrokerType.MT5_FOREX, 'configs/brokers/mt5/mt5_broker_config.json'),
    )

    @pytest.mark.parametrize('name,broker_type,config_path', _CONFIGS)
    def test_a_real_adapter_still_answers_local(self, name, broker_type, config_path):
        """
        Every adapter in the tree must answer LOCAL, and for a stated reason.

        No submit path attaches a protective level to an order, on any adapter, so LOCAL is
        not a default here — it is the only truthful answer available.
        """
        with open(config_path, encoding='utf-8') as handle:
            config = json.load(handle)
        adapter = (KrakenAdapter(config) if broker_type == BrokerType.KRAKEN_SPOT
                   else Mt5Adapter(config))

        simulator = TradeSimulator(
            broker_config=BrokerConfig(broker_type, adapter),
            initial_balance=10000.0,
            account_currency='USD',
            logger=GlobalLogger('ProtectiveLevelsRealAdapter'),
            seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=0,
            inbound_latency_max_ms=0,
        )

        assert simulator.get_protective_level_enforcement() == (
            ProtectiveLevelEnforcement.LOCAL), (
            f'{name} makes the simulation answer something other than LOCAL. If a '
            f'capability was just wired into the resolver, check it is a SUBMIT-side one — '
            f'native_position_sl_tp is about who performs a modify, and reading it here '
            f'switches off simulated SL/TP enforcement for every mt5 backtest.')

    def test_no_adapter_declares_a_venue_held_level_yet(self):
        """
        The precondition under the constant, asserted directly rather than assumed.

        The day an adapter can carry a level to the venue on a submit, this goes red and
        the resolver is the thing to fix — not this test.
        """
        for _, _, config_path in self._CONFIGS:
            with open(config_path, encoding='utf-8') as handle:
                config = json.load(handle)
            adapter = (KrakenAdapter(config) if 'kraken' in config_path
                       else Mt5Adapter(config))
            caps = adapter.get_order_capabilities()

            assert not caps.native_position_sl_tp, (
                f'{config_path} declares native_position_sl_tp. That flag routes '
                f'modify_position server-side; it does NOT mean the venue holds a level '
                f'declared at submit. Read #500 item 3 before touching the resolver.')


class TestTheLiveStopActs:
    """The live path closes on a breach — the behaviour that did not exist."""

    def test_a_breach_closes_the_position(self):
        mock, executor = _live_with_protected_long(stop_loss=49000.0)
        assert len(executor.get_open_positions()) == 1

        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        assert not executor.get_open_positions(), 'The stop was breached and did not act'

    def test_the_exit_carries_the_reason_across_the_round_trip(self):
        """
        The reason is known at the TRIGGER and the fill lands a round trip later.

        It travels on the PendingOrder, because a live close is asynchronous. Without that
        the trade would be recorded as a plain manual close and the run report could not
        tell a stop-out from a strategy exit.
        """
        mock, executor = _live_with_protected_long(stop_loss=49000.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        trades = executor.portfolio.get_trade_history()
        assert len(trades) == 1
        assert trades[0].close_reason == CloseReason.SL_TRIGGERED

    def test_a_target_breach_closes_it_too(self):
        mock, executor = _live_with_protected_long(take_profit=51000.0)

        mock.feed_tick(executor, symbol=_SYMBOL, bid=51100.0, ask=51101.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=51100.0, ask=51101.0)

        trades = executor.portfolio.get_trade_history()
        assert len(trades) == 1
        assert trades[0].close_reason == CloseReason.TP_TRIGGERED

    def test_a_position_without_levels_is_left_alone(self):
        """The regression guard: nothing closes what declared nothing."""
        mock, executor = _live_with_protected_long()

        mock.feed_tick(executor, symbol=_SYMBOL, bid=10.0, ask=11.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=10.0, ask=11.0)

        assert len(executor.get_open_positions()) == 1


class TestItDoesNotCloseTwice:
    """
    A live close takes a round trip, and the next tick is usually worse.

    Without the in-flight guard the second tick would submit a second close for the same
    position — the double close the old early return was (wrongly) protecting against, and
    the reason that return has to be replaced by a guard rather than simply deleted.
    """

    def test_a_second_breach_while_the_close_is_in_flight_triggers_nothing(self):
        """
        TIMEOUT mode: the close is submitted and never confirmed, so it stays in flight
        for the rest of the test — which is exactly the window the guard has to cover.
        `sl_tp_triggered` counts accepted triggers, so it is the honest observable here.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=49000.0))
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        position_id = executor.get_open_positions()[0].position_id

        # Trigger, then hold the close in flight by never draining a confirmation.
        executor.close_position(position_id, close_reason=CloseReason.SL_TRIGGERED)
        assert executor.is_pending_close(position_id), 'Setup: the close must be in flight'
        before = executor.get_execution_stats().sl_tp_triggered

        executor._check_sl_tp_triggers(_tick(48900.0, 48901.0))
        executor._check_sl_tp_triggers(_tick(48800.0, 48801.0))

        assert executor.get_execution_stats().sl_tp_triggered == before, (
            'A breach while the close is already in flight must not trigger a second one')


class TestTheSimulationIsUnchanged:
    """
    The simulation kept its own exit, and that is deliberate.

    It fills a synthetic close AT the level, in-tick and deterministic, which is what every
    backtest assertion rests on. Routing it through the live path would alter results across
    the whole suite for a parity that a backtest cannot honestly have anyway.
    """

    def test_the_simulation_still_answers_local(self):
        assert _simulator().get_protective_level_enforcement() == (
            ProtectiveLevelEnforcement.LOCAL)

    def test_a_simulated_stop_fills_at_the_level_itself(self):
        sim = _simulator()
        sim.on_tick(_tick(50000.0, 50001.0))
        result = sim.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=49000.0))
        assert result.status == OrderStatus.PENDING
        sim.on_tick(_tick(50000.0, 50001.0))
        assert len(sim.get_open_positions()) == 1

        sim.on_tick(_tick(48900.0, 48901.0))

        trades = sim.portfolio.get_trade_history()
        assert len(trades) == 1
        assert trades[0].close_reason == CloseReason.SL_TRIGGERED
        # AT the level — not at the breaching price. This is the difference from live.
        assert trades[0].exit_price == pytest.approx(49000.0)


def _tick(bid: float, ask: float) -> TickData:
    """
    A tick at the given prices.

    Args:
        bid: Bid price
        ask: Ask price

    Returns:
        The TickData
    """
    return TickData(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        symbol=_SYMBOL, bid=bid, ask=ask, volume=1.0)


class TestWhereTheVenueHoldsItWeStepAside:
    """
    The contract an adapter that can carry a level to the wire will rely on.

    No executor answers VENUE today — the conditional-close work is its own issue. But the
    branch has to exist and has to be proven BEFORE that work lands, because getting it
    wrong is the expensive direction: the venue's own stop order can fill while our close is
    still in flight, and the position would be sold twice. So the enforcement answer is
    overridden here to state the contract rather than to describe today's behaviour.
    """

    def test_a_venue_held_level_is_not_acted_on_locally(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=49000.0))
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        assert len(executor.get_open_positions()) == 1

        executor.get_protective_level_enforcement = (
            lambda: ProtectiveLevelEnforcement.VENUE)
        executor._check_sl_tp_triggers(_tick(48900.0, 48901.0))

        assert len(executor.get_open_positions()) == 1, (
            'The venue holds this level; closing it here as well would sell the position '
            'twice')
        assert executor.get_execution_stats().sl_tp_triggered == 0

    def test_the_local_answer_still_acts(self):
        """The other direction, so the branch cannot be read as "never act"."""
        mock, executor = _live_with_protected_long(stop_loss=49000.0)
        assert executor.get_protective_level_enforcement() == (
            ProtectiveLevelEnforcement.LOCAL)

        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        assert not executor.get_open_positions()


class TestAPartialCloseSuppressesTheStop:
    """
    Any close in flight suppresses the level, not only a protective one — deliberately.

    `is_pending_close` matches ANY close registered for the position, so a strategy's
    partial close briefly holds the stop off. That is the conservative reading and it is
    the right one: a partial close takes some lots and a stop takes all of them, so letting
    both fly would ask the venue to sell more than the position holds. The cost is a narrow
    window, one round trip long, in which the level is not acted on. Pinned here because it
    is a real gap and nobody should discover it from a live account.
    """

    def test_a_strategy_partial_close_holds_the_stop_off_while_it_flies(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.02, stop_loss=49000.0))
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        position_id = executor.get_open_positions()[0].position_id

        executor.close_position(position_id, lots=0.01)
        before = executor.get_execution_stats().sl_tp_triggered
        executor._check_sl_tp_triggers(_tick(48900.0, 48901.0))

        assert executor.get_execution_stats().sl_tp_triggered == before, (
            'A partial close in flight must hold the stop off — two closes against one '
            'position would over-sell it')
