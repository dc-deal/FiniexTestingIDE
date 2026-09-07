"""
FiniexTestingIDE - Order Type Gate Tests (#500 sibling)

An adapter declares what the VENUE accepts; an executor declares what the PIPELINE has built.
The two disagreed: Kraken declares STOP_LIMIT, the live path carries MARKET and LIMIT — and a
strategy declaring STOP_LIMIT passed pre-flight, then had every order rejected at submission.
A checked-in live profile sat in exactly that state.

Pre-flight now checks a strategy's needs against the INTERSECTION of both declarations, and the
executor's `open_order()` gate reads the same set, so the two cannot drift apart again.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderCapabilities,
    OrderDirection,
    OrderStatus,
    OrderType,
    RejectionReason,
)


class _VenueDeclaresMore(MockBrokerAdapter):
    """
    A mock whose VENUE declaration is wider than any pipeline has built — Kraken's shape.

    Kraken declares STOP_LIMIT and ICEBERG; the live path routes neither, the sim routes
    STOP_LIMIT only. This is the disagreement the gate has to catch.
    """

    def get_order_capabilities(self) -> OrderCapabilities:
        return replace(super().get_order_capabilities(),
                       stop_limit_orders=True, iceberg_orders=True)


def _sim_with(adapter: MockBrokerAdapter) -> TradeSimulator:
    """
    A simulation executor over the given adapter.

    Args:
        adapter: The venue declaration to test against

    Returns:
        The simulator
    """
    return TradeSimulator(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, adapter),
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger('OrderTypeGate'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )


class TestWhatEachPipelineDeclares:
    """The two sets are the contract; everything else reads them."""

    def test_the_live_path_carries_the_four_types_it_has_built(self):
        executor = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL).create_executor()

        assert executor.get_supported_order_types() == {
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}

    def test_neither_pipeline_declares_a_type_it_cannot_place(self):
        """
        TRAILING_STOP and ICEBERG stay out of BOTH sets.

        Kraken declares both as venue capabilities, and this is the assertion that keeps a
        venue declaration from leaking into a pipeline one — the confusion #500 found in
        the other direction.
        """
        executor = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL).create_executor()
        sim = _sim_with(MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))

        for declared in (executor.get_supported_order_types(), sim.get_supported_order_types()):
            assert OrderType.TRAILING_STOP not in declared
            assert OrderType.ICEBERG not in declared
            assert OrderType.UNKNOWN not in declared

    def test_the_simulation_carries_the_resting_types_too(self):
        sim = _sim_with(MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))

        assert sim.get_supported_order_types() == {
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}


class TestPreFlightReadsTheIntersection:
    """A strategy is refused at STARTUP for what the pipeline lacks, not at its first order."""

    def test_a_venue_declared_type_the_pipeline_lacks_is_refused_before_trading(self):
        """
        ICEBERG: the venue says yes, the simulation has no branch for it.

        Before this gate the strategy started and every ICEBERG order came back
        ORDER_TYPE_NOT_SUPPORTED — the same shape the checked-in STOP_LIMIT live profile was in.
        """
        sim = _sim_with(_VenueDeclaresMore(mode=MockExecutionMode.INSTANT_FILL))

        with pytest.raises(ValueError) as refused:
            DecisionTradingApi(sim, required_order_types=[OrderType.ICEBERG])

        message = str(refused.value)
        assert 'has not implemented' in message and 'iceberg' in message
        # And it says WHICH side is short: the venue offered it, the pipeline did not.
        assert 'does not offer: []' in message

    def test_a_type_both_sides_carry_passes(self):
        """STOP_LIMIT on the SIMULATION: venue declares it, the sim routes it — allowed."""
        sim = _sim_with(_VenueDeclaresMore(mode=MockExecutionMode.INSTANT_FILL))

        api = DecisionTradingApi(sim, required_order_types=[OrderType.STOP_LIMIT])

        assert api is not None

    def test_a_type_the_venue_lacks_is_named_as_the_venue_side(self):
        """The default mock declares neither STOP_LIMIT nor ICEBERG — the venue is short."""
        sim = _sim_with(MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))

        with pytest.raises(ValueError) as refused:
            DecisionTradingApi(sim, required_order_types=[OrderType.ICEBERG])

        assert 'does not offer' in str(refused.value) and 'iceberg' in str(refused.value)


class TestTheSubmissionGateReadsTheSameSet:
    """The gate in open_order() and the pre-flight cannot disagree — they read one set."""

    def test_the_live_path_rejects_what_it_does_not_declare(self):
        """
        ICEBERG, fully formed. It was STOP_LIMIT until the live path learned that type.

        The order carries a price, so a rejection cannot come from the price gate — the
        only thing that can refuse it is the declared set.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol='BTCUSD', bid=49999.0, ask=50001.0)

        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.ICEBERG,
            direction=OrderDirection.LONG, lots=0.01, price=49000.0))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.ORDER_TYPE_NOT_SUPPORTED

    def test_every_declared_type_is_one_the_gate_lets_through(self):
        """
        The consistency property: a declared type is never rejected as unsupported.

        Every declared type must not come back ORDER_TYPE_NOT_SUPPORTED, whatever else
        happens to it.

        Each type is supplied with the prices ITS shape needs. That is not convenience: with
        `price=None` for everything, a STOP is refused as INVALID_PRICE and the assertion
        below still holds — so the test would pass while proving nothing about the types it
        was widened to cover. The assertion is also strengthened to ACCEPTANCE, because
        "rejected for some other reason" was the loophole.
        """
        prices = {
            OrderType.MARKET: {},
            OrderType.LIMIT: {'price': 49000.0},
            OrderType.STOP: {'stop_price': 51000.0},
            OrderType.STOP_LIMIT: {'stop_price': 51000.0, 'price': 51100.0},
        }
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol='BTCUSD', bid=49999.0, ask=50001.0)

        for order_type in executor.get_supported_order_types():
            assert order_type in prices, (
                f'{order_type.value} was added to the declared set without telling this '
                f'test which prices it needs — see the docstring')
            result = executor.open_order(OpenOrderRequest(
                symbol='BTCUSD', order_type=order_type, direction=OrderDirection.LONG,
                lots=0.01, **prices[order_type]))
            assert result.status != OrderStatus.REJECTED, (
                f'{order_type.value} is declared but was rejected: '
                f'{result.rejection_reason}')


class TestTheWireRefusesWhatItCannotMap:
    """
    The second line behind the gate: the Kraken payload builder maps only what it knows.

    It used to fall through to 'limit' for anything else — so had the executor's gate ever
    let a STOP_LIMIT through, it would have gone to the venue as a plain LIMIT at its limit
    price. Now it raises, which is what makes widening the gate safe to attempt at all: the
    wire cannot receive a type the builder has not been taught.

    The unmapped type asserted here MOVES as the builder learns types. It was STOP_LIMIT
    until #500 taught the builder the two stop types; ICEBERG is the next one Kraken offers
    and this project has not built, so it is the honest stand-in. If ICEBERG is ever routed,
    point this at the type that is then still unmapped rather than deleting the test — the
    refusal is the contract, not the type.

    Exercised through the processor's public submit path, offline: the builder runs BEFORE any
    network call, so an adapter that was never enabled for live is enough.
    """

    _BROKER_CONFIG = Path('configs/brokers/kraken/kraken_spot_broker_config.json')

    def _kraken_offline(self) -> KrakenAdapter:
        """Kraken's real payload builder, no credentials, no network. Returns: the adapter."""
        with open(self._BROKER_CONFIG, 'r') as f:
            return KrakenAdapter(json.load(f))

    def test_a_type_the_builder_cannot_map_raises_instead_of_becoming_a_limit(self):
        processor = LiveRequestProcessor(
            logger=GlobalLogger(name='OrderTypeGateWire'),
            timeout_config=TimeoutConfig(order_timeout_seconds=30.0))

        with pytest.raises(ValueError) as refused:
            processor.submit_open_order(
                symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
                order_type=OrderType.ICEBERG, adapter=self._kraken_offline(),
                price=49000.0)

        message = str(refused.value)
        assert 'iceberg' in message
        # It names what IS mapped, so the reader knows the boundary rather than guessing it.
        assert 'MARKET' in message and 'LIMIT' in message
        assert 'STOP' in message and 'STOP_LIMIT' in message
