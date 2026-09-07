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

    def test_the_live_path_carries_market_and_limit_only(self):
        executor = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL).create_executor()

        assert executor.get_supported_order_types() == {OrderType.MARKET, OrderType.LIMIT}

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
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol='BTCUSD', bid=49999.0, ask=50001.0)

        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.STOP_LIMIT,
            direction=OrderDirection.LONG, lots=0.01, price=49000.0, stop_price=49500.0))

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.ORDER_TYPE_NOT_SUPPORTED

    def test_every_declared_type_is_one_the_gate_lets_through(self):
        """
        The consistency property: a declared type is never rejected as unsupported.

        Checked on the live path for its two types — a MARKET and a LIMIT must not come back
        ORDER_TYPE_NOT_SUPPORTED, whatever else happens to them.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol='BTCUSD', bid=49999.0, ask=50001.0)

        for order_type in executor.get_supported_order_types():
            result = executor.open_order(OpenOrderRequest(
                symbol='BTCUSD', order_type=order_type, direction=OrderDirection.LONG,
                lots=0.01, price=49000.0 if order_type == OrderType.LIMIT else None))
            assert result.rejection_reason != RejectionReason.ORDER_TYPE_NOT_SUPPORTED, (
                f'{order_type.value} is declared but the gate rejected it')


class TestTheWireRefusesWhatItCannotMap:
    """
    The second line behind the gate: the Kraken payload builder maps MARKET and LIMIT only.

    It used to fall through to 'limit' for anything else — so had the executor's gate ever
    let a STOP_LIMIT through, it would have gone to the venue as a plain LIMIT at its limit
    price. Now it raises, which is what makes widening the gate safe to attempt at all: the
    wire cannot receive a type the builder has not been taught.

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
                order_type=OrderType.STOP_LIMIT, adapter=self._kraken_offline(),
                price=49000.0, stop_price=49500.0)

        message = str(refused.value)
        assert 'stop_limit' in message
        # It names what IS mapped, so the reader knows the boundary rather than guessing it.
        assert 'MARKET' in message and 'LIMIT' in message
