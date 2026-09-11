"""
FiniexTestingIDE - Venue-Held Protection: Declaration and Refusal (#503, stage A)

A protective level declared on an order is enforced by THIS process (#500). It therefore
dies with the process, and a month-long unattended run restarts — that is a certainty, not
a risk. #503 turns a declared level into a real order at the venue, which survives us.

This file covers the first stage only: the intent can be EXPRESSED, RESOLVED and REFUSED,
and the position can say who holds its level. Nothing is placed yet.

Three parties decide whether an order may carry a venue-held protection — the profile
default, the per-order override, and the adapter's capability — and the refusal has to name
the short side, so an operator reading it knows which one to change.

The simulation is deliberately absent from the refusal: it accepts the flag and changes
nothing. A strategy that opts in must still be backtestable, and parity between the two
pipelines is the point of the project.
"""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderCapabilities,
    OrderDirection,
    OrderStatus,
    OrderType,
    ProtectiveLevelEnforcement,
    RejectionReason,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'


class _VenueHoldsProtectionMock(MockBrokerAdapter):
    """
    A mock whose venue accepts a standalone protective order.

    The plain MockBrokerAdapter declares False, which is truthful — it has no venue. This
    subclass exists so the ACCEPTING side of the resolution has something to accept, without
    a live adapter and without credentials.
    """

    def get_order_capabilities(self) -> OrderCapabilities:
        return replace(super().get_order_capabilities(),
                       venue_held_protective_orders=True)


def _live_executor(
    profile_default: bool = False,
    venue_can_hold: bool = False,
) -> LiveTradeExecutor:
    """
    A live executor with a declared profile default and a chosen venue capability.

    Args:
        profile_default: What the profile's execution.venue_held_protection says
        venue_can_hold: Whether the adapter declares venue_held_protective_orders

    Returns:
        The executor, ready for open_order
    """
    adapter = (_VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)
               if venue_can_hold
               else MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))
    return LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, adapter),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('VenueHeldProtection'),
        venue_held_protection=profile_default,
    )


def _market_request(venue_held_protection=None) -> OpenOrderRequest:
    """
    A plain protected MARKET buy, with the override under test.

    Args:
        venue_held_protection: The per-order override — None follows the profile

    Returns:
        The request
    """
    return OpenOrderRequest(
        symbol=_SYMBOL,
        order_type=OrderType.MARKET,
        direction=OrderDirection.LONG,
        lots=0.01,
        stop_loss=49000.0,
        venue_held_protection=venue_held_protection,
    )


def _tick(bid: float, ask: float) -> TickData:
    """
    One tick for the fixed symbol.

    Args:
        bid: Bid price
        ask: Ask price

    Returns:
        The TickData
    """
    return TickData(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        symbol=_SYMBOL, bid=bid, ask=ask, volume=1.0)


class TestTheResolutionMatrix:
    """
    Profile ⊕ per-order override, observed through the only thing that can be observed at
    this stage: whether an adapter that CANNOT hold it refuses the order.

    Asserting the resolution through the refusal rather than through a private getter is
    deliberate. The resolution exists to gate the placement; a test that reads it directly
    would still pass if the gate were wired to something else.
    """

    @pytest.mark.parametrize('profile_default,override,expect_refused', [
        (False, None, False),    # nobody asked for it
        (False, True, True),     # the order asked, over a profile that did not
        (True, None, True),      # the profile asked, the order stayed silent
        (True, False, False),    # the order opted OUT of a profile that asked
    ])
    def test_the_two_declarations_combine(self, profile_default, override, expect_refused):
        executor = _live_executor(profile_default=profile_default, venue_can_hold=False)
        result = executor.open_order(_market_request(venue_held_protection=override))

        refused = result.status == OrderStatus.REJECTED
        assert refused == expect_refused, (
            f'profile={profile_default} override={override} resolved to '
            f'{"refused" if refused else "accepted"}')


class TestARefusalNamesTheShortSide:
    """
    A refusal an operator cannot act on is only half a refusal. The pre-flight intersection
    already names whichever side is short; this says the same thing at submit time.
    """

    def test_the_venue_is_named_when_it_cannot_hold_it(self):
        executor = _live_executor(profile_default=True, venue_can_hold=False)
        result = executor.open_order(_market_request())

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.ORDER_TYPE_NOT_SUPPORTED
        assert 'venue_held_protection' in result.rejection_message
        assert executor.get_broker_name() in result.rejection_message

    def test_a_venue_that_can_hold_it_lets_the_order_through(self):
        executor = _live_executor(profile_default=True, venue_can_hold=True)
        result = executor.open_order(_market_request())

        assert result.status != OrderStatus.REJECTED, (
            f'The venue declares it can hold a protective order: {result.rejection_message}')

    def test_an_order_without_a_level_is_never_refused_for_this(self):
        """
        Nothing to protect, nothing to refuse.

        A profile-wide opt-in must not turn every unprotected order into a rejection —
        that would make the switch unusable for any strategy that also enters without a
        stop.
        """
        executor = _live_executor(profile_default=True, venue_can_hold=False)
        request = _market_request()
        request.stop_loss = None
        result = executor.open_order(request)

        assert result.status != OrderStatus.REJECTED, result.rejection_message


class TestTheStampSaysWhoHoldsIt:
    """
    `protective_enforcement` is DERIVED from the broker reference, never stored beside it.
    A fourth carrier of the same truth is how the console came to print a stop nobody held.
    """

    def test_without_a_reference_the_position_follows_the_run(self):
        executor = _live_executor()
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        mock.feed_tick(executor, symbol=_SYMBOL)
        executor.open_order(_market_request())
        mock.feed_tick(executor, symbol=_SYMBOL)

        position = executor.get_open_positions()[0]
        assert position.protective_broker_ref is None
        assert position.protective_enforcement(ProtectiveLevelEnforcement.LOCAL) == (
            ProtectiveLevelEnforcement.LOCAL)

    def test_a_reference_makes_it_venue_held_whatever_the_run_says(self):
        executor = _live_executor()
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        mock.feed_tick(executor, symbol=_SYMBOL)
        executor.open_order(_market_request())
        mock.feed_tick(executor, symbol=_SYMBOL)

        position = executor.get_open_positions()[0]
        position.protective_broker_ref = 'OABCDE-1234-XYZ'
        assert position.protective_enforcement(ProtectiveLevelEnforcement.LOCAL) == (
            ProtectiveLevelEnforcement.VENUE)


class TestAMixedRunStepsAsidePerPosition:
    """
    The reason the check became per-position: once one position's level rests at the venue
    and another's does not, a single answer for the whole run is wrong for one of them.

    Getting this wrong is asymmetric. Acting locally on a venue-held level sells the
    position twice; NOT acting on a local one leaves it unprotected. Both are covered here.
    """

    def test_the_stamped_position_is_left_to_the_venue_and_the_other_is_not(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _live_executor()
        mock.feed_tick(executor, symbol=_SYMBOL)
        executor.open_order(_market_request())
        mock.feed_tick(executor, symbol=_SYMBOL)
        executor.open_order(_market_request())
        mock.feed_tick(executor, symbol=_SYMBOL)
        assert len(executor.get_open_positions()) == 2

        venue_held = executor.get_open_positions()[0]
        venue_held.protective_broker_ref = 'OABCDE-1234-XYZ'
        locally_held_id = executor.get_open_positions()[1].position_id

        # Two breaching ticks: the first triggers, the second lets the asynchronous close
        # resolve. A live close is a round trip — the position does not vanish in-tick.
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        still_open = {p.position_id for p in executor.get_open_positions()}
        assert venue_held.position_id in still_open, (
            'The venue holds this one; closing it here too sells the position twice')
        assert locally_held_id not in still_open, (
            'Nobody but this process holds that level — it had to act')
        assert executor.get_execution_stats().sl_tp_triggered == 1, (
            'Exactly one of the two levels was ours to enforce')


class TestTheSimulationAcceptsAndIgnores:
    """
    A backtest has no venue, so it can neither place nor refuse. It accepts the flag and
    enforces the level itself — the same result a run without the flag produces.

    Refusing instead would make a strategy that opts in un-backtestable, and the sim/live
    parity that costs is the whole claim of the framework.
    """

    def test_the_flag_does_not_reject_a_backtest_order(self):
        simulator = TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT,
                MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('VenueHeldProtectionSim'),
            seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=0,
            inbound_latency_max_ms=0,
        )
        simulator.on_tick(_tick(50000.0, 50001.0))
        result = simulator.open_order(_market_request(venue_held_protection=True))

        assert result.status != OrderStatus.REJECTED, result.rejection_message

    def test_the_backtest_still_answers_local(self):
        simulator = TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT,
                MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('VenueHeldProtectionSim'),
            seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=0,
            inbound_latency_max_ms=0,
        )

        assert simulator.get_protective_level_enforcement() == (
            ProtectiveLevelEnforcement.LOCAL)


class TestTheAdaptersDeclareItTruthfully:
    """
    Built from the REAL broker JSON, because a capability is a statement about a venue and
    the venue is what the adapter in the tree talks to.

    Kraken's True is a MEASURED fact (#500, minimum size, cleaned up): a standalone
    stop-loss order rests at the venue over a spot holding and fires without us. MT5's
    False is not a gap — MT5 holds the level ON the position, which is a different
    mechanism and belongs to #209.
    """

    _CONFIGS = (
        ('kraken_spot', 'configs/brokers/kraken/kraken_spot_broker_config.json', True),
        ('mt5', 'configs/brokers/mt5/mt5_broker_config.json', False),
    )

    @pytest.mark.parametrize('name,config_path,expected', _CONFIGS)
    def test_each_adapter_states_whether_its_venue_can_hold_one(
            self, name, config_path, expected):
        with open(config_path, encoding='utf-8') as handle:
            config = json.load(handle)
        adapter = (KrakenAdapter(config) if 'kraken' in config_path
                   else Mt5Adapter(config))

        assert adapter.get_order_capabilities().venue_held_protective_orders == expected

    def test_the_mock_declares_no_venue(self):
        """The mock has no venue at all, and says so rather than defaulting quietly."""
        caps = MockBrokerAdapter(
            mode=MockExecutionMode.INSTANT_FILL).get_order_capabilities()

        assert not caps.venue_held_protective_orders

    def test_it_is_not_the_same_question_as_a_position_level_modify(self):
        """
        `native_position_sl_tp` answers who performs a MODIFY on an open position. On the
        two venues that exist the two answers point in OPPOSITE directions, so reading one
        for the other reinstates #500's original defect one level up.
        """
        with open('configs/brokers/kraken/kraken_spot_broker_config.json',
                  encoding='utf-8') as handle:
            caps = KrakenAdapter(json.load(handle)).get_order_capabilities()

        assert caps.venue_held_protective_orders
        assert not caps.native_position_sl_tp


class TestTheStandDownIsPerLevelNotPerPosition:
    """
    Kraken has no OCO and no bracket, so exactly ONE of a declared pair can rest at the
    venue — the STOP. The take profit of the same position is still ours to enforce.

    Standing down for the whole position would leave the target recorded, printed, carried
    into the run report and enforced by nobody. That is #500's original defect verbatim,
    reinstated one level up, and it would be invisible: the operator sees a target on the
    console and no reason to doubt it.
    """

    def _protected_long_with_both_levels(self):
        """
        A live LONG carrying a stop the venue holds and a target it does not.

        Returns:
            (mock, executor, position)
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        # A venue that lets a stop REST — the stock mock fills everything on arrival and
        # rejects any reference it did not mint, which tears the order down one poll
        # after it was confirmed. See this suite's conftest.
        adapter = VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)
        executor = LiveTradeExecutor(
            broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, adapter),
            initial_balance=100000.0,
            account_currency='USD',
            logger=GlobalLogger('VenueHeldProtection'),
        )
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=49000.0, take_profit=51000.0))
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        position = executor.get_open_positions()[0]
        # The venue holds this position's STOP. Both halves of that state are set,
        # because the code sets them together: the id is what we cancel by, the
        # reference is what makes the enforcement answer VENUE.
        protective = PendingOrder(
            pending_order_id='ord_protect_1',
            order_action=PendingOrderAction.CLOSE,
            order_type=OrderType.STOP,
            broker_ref='OABCDE-1234-XYZ',
            symbol=_SYMBOL,
            direction=OrderDirection.SHORT,
            close_lots=position.lots,
            closes_position_id=position.position_id,
        )
        executor._active_stop_orders.append(protective)
        adapter.hold_resting_stop(protective.broker_ref)
        position.protective_order_id = protective.pending_order_id
        position.protective_broker_ref = protective.broker_ref
        return mock, executor, position

    def test_the_target_is_still_enforced_here(self, capsys):
        """
        The target fires locally — and its close goes BEHIND the cancel of the stop.

        Both halves matter. If the local check stood down for the whole position, the
        target would be enforced by nobody. If the close went out BESIDE the resting
        stop, both could fill and the second would sell a holding that is gone — there is
        no reduce_only at spot, and a market close is measured to go through while a stop
        rests over the same holding (#503 D3).

        The ordering is what is asserted, because this mock resolves a cancel inside the
        same tick and a real venue does not: the intermediate state is not observable,
        the sequence is.
        """
        mock, executor, position = self._protected_long_with_both_levels()
        capsys.readouterr()

        mock.feed_tick(executor, symbol=_SYMBOL, bid=51100.0, ask=51101.0)
        printed = capsys.readouterr().out

        assert executor.get_execution_stats().sl_tp_triggered == 1, (
            'The venue holds the STOP, not the target — nobody else would act on it')
        assert 'waiting for the venue to confirm the protective order is cancelled' in printed
        assert printed.index('cancel resolved') < printed.index(
            'Live close order tracked'), (
            'The close must leave only AFTER the venue confirmed the stop is gone')

        # And the close itself is a round trip like any other.
        mock.feed_tick(executor, symbol=_SYMBOL, bid=51100.0, ask=51101.0)
        assert not executor.get_open_positions(), 'and then it closes'

    def test_the_stop_is_still_left_to_the_venue(self):
        """The other direction, so the change cannot be read as "always act"."""
        mock, executor, position = self._protected_long_with_both_levels()

        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        assert len(executor.get_open_positions()) == 1, (
            'Acting on the stop here as well would sell the position twice')
        assert executor.get_execution_stats().sl_tp_triggered == 0
