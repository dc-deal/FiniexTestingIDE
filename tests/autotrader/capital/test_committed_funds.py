"""
FiniexTestingIDE - Committed Funds Tests (#489)

A venue reserves the asset when an order is PLACED; our balances move only on FILL. So
without a committed figure the funds check reads a balance that two unfilled orders can each
spend in full — and the second one comes back as a broker rejection the bot cannot explain to
itself, while our own books still showed the money.

These tests exercise the SHARED executor path through the live mock (no network): the
committed query, the exclusion that keeps a filling order from reserving itself twice, and
the submission-time refusal, which is RETURNED rather than announced — `_notify_outcome` is
called only from the asynchronous resolution paths, so no outcome event fires for it.
"""

from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectionReason,
)

# The tick the harness feeds: a BUY pays 50001.0, a SELL receives 49999.0.
_BID = 49999.0
_ASK = 50001.0

# Deliberately below market so the LIMIT rests instead of filling on submission.
_RESTING_BUY_PRICE = 49000.0
# Deliberately above market, same reason on the sell side.
_RESTING_SELL_PRICE = 51000.0


def _spot_executor(usd: float = 1000.0, btc: float = 0.0):
    """
    A spot executor with a fed tick, ready to take orders.

    Args:
        usd: Starting quote balance
        btc: Starting base balance

    Returns:
        (mock, executor) — the harness and the executor built from it
    """
    mock = MockOrderExecution(
        mode=MockExecutionMode.DELAYED_FILL,
        spot_mode=True,
        initial_balances={'USD': usd, 'BTC': btc},
        account_currency='USD',
    )
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol='BTCUSD', bid=_BID, ask=_ASK)
    return mock, executor


def _limit(direction: OrderDirection, lots: float, price: float) -> OpenOrderRequest:
    """
    A resting LIMIT order request.

    Args:
        direction: LONG or SHORT
        lots: Size in base units
        price: The limit price

    Returns:
        The request
    """
    return OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.LIMIT,
        direction=direction, lots=lots, price=price,
    )


def _market(direction: OrderDirection, lots: float) -> OpenOrderRequest:
    """
    A MARKET order request.

    Args:
        direction: LONG or SHORT
        lots: Size in base units

    Returns:
        The request
    """
    return OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET, direction=direction, lots=lots,
    )


class TestTheCommittedQuery:
    """What an unfilled order claims, per currency."""

    def test_an_empty_book_claims_nothing(self):
        _, executor = _spot_executor()

        assert executor.get_committed_funds('USD') == 0.0
        assert executor.get_committed_funds('BTC') == 0.0

    def test_a_resting_buy_claims_its_quote_including_the_fee(self):
        _, executor = _spot_executor()
        executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))

        committed = executor.get_committed_funds('USD')
        notional = 0.012 * _RESTING_BUY_PRICE

        # The fee is part of what the venue holds, so it is part of the reserve. Asserted as
        # a property rather than a recomputed number — a second fee formula in a test is a
        # second place for it to be wrong.
        assert committed > notional
        assert committed < notional * 1.02
        # And it claims the quote alone: the base arrives on fill, it is not spent.
        assert executor.get_committed_funds('BTC') == 0.0

    def test_a_resting_sell_claims_its_base(self):
        _, executor = _spot_executor(btc=0.05)
        executor.open_order(_limit(OrderDirection.SHORT, 0.02, _RESTING_SELL_PRICE))

        # A sell spends base, and the amount is the lots themselves — no price, no fee:
        # the fee comes out of the quote proceeds.
        assert executor.get_committed_funds('BTC') == pytest.approx(0.02)
        assert executor.get_committed_funds('USD') == 0.0

    def test_the_excluded_order_does_not_reserve_itself(self):
        """
        The order being filled is still in its own collection while the fill runs.

        Counting it there would reserve it twice and refuse a fill the account can afford,
        which is why the query takes an explicit exclusion rather than trusting the moment
        an order is removed from its list.
        """
        _, executor = _spot_executor()
        result = executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))

        assert executor.get_committed_funds('USD') > 0.0
        assert executor.get_committed_funds(
            'USD', exclude_order_id=result.order_id) == 0.0

    def test_margin_mode_reserves_nothing(self):
        """Its quantity is free margin, and an unfilled order's claim on it is #209's."""
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL, spot_mode=False)
        executor = mock.create_executor()
        mock.feed_tick(executor, symbol='BTCUSD', bid=_BID, ask=_ASK)
        executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))

        assert executor.get_committed_funds('USD') == 0.0


class TestTheSubmissionRefusal:
    """The case the feature exists for, and the shape of the answer."""

    def test_the_second_order_is_refused_while_the_first_still_rests(self):
        """
        1000 USD · a resting BUY claiming ~589 · a market BUY needing ~501.

        Before #489 both passed: neither knew about the other's reserve, and the venue
        refused the second one for insufficient funds.
        """
        _, executor = _spot_executor(usd=1000.0)
        executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))

        second = executor.open_order(_market(OrderDirection.LONG, 0.01))

        assert second.status == OrderStatus.REJECTED
        assert second.rejection_reason == RejectionReason.INSUFFICIENT_FUNDS
        # The message separates the two numbers, so an empty account and a fully-claimed
        # one cannot read the same.
        assert 'committed' in second.rejection_message
        assert 'balance' in second.rejection_message

    def test_the_same_order_passes_when_nothing_is_committed(self):
        """The regression guard: without an unfilled order, the balance is the balance."""
        _, executor = _spot_executor(usd=1000.0)

        result = executor.open_order(_market(OrderDirection.LONG, 0.01))

        assert result.status != OrderStatus.REJECTED

    def test_a_sell_is_refused_against_committed_base(self):
        _, executor = _spot_executor(btc=0.03)
        executor.open_order(_limit(OrderDirection.SHORT, 0.02, _RESTING_SELL_PRICE))

        second = executor.open_order(_market(OrderDirection.SHORT, 0.02))

        assert second.status == OrderStatus.REJECTED
        assert second.rejection_reason == RejectionReason.INSUFFICIENT_FUNDS

    def test_the_refusal_is_returned_and_announces_nothing(self):
        """
        A submission rejection is the return value, not an event.

        Every existing submission rejection follows that convention — `_notify_outcome` is
        called only from the asynchronous resolution paths — so an algo learns from the
        result it already holds, in the same call.
        """
        _, executor = _spot_executor(usd=1000.0)
        seen: List[Tuple[OrderDirection, OrderResult]] = []
        executor.add_order_outcome_listener(
            lambda direction, result, pending: seen.append((direction, result)))
        executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))

        refused = executor.open_order(_market(OrderDirection.LONG, 0.01))

        assert refused.status == OrderStatus.REJECTED
        assert seen == []

    def test_the_refusal_is_recorded_in_the_order_history(self):
        """It is a real rejection, so the run's own record carries it."""
        _, executor = _spot_executor(usd=1000.0)
        executor.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))
        executor.open_order(_market(OrderDirection.LONG, 0.01))

        rejections = [
            r for r in executor.get_order_history()
            if r.rejection_reason == RejectionReason.INSUFFICIENT_FUNDS
        ]

        assert len(rejections) == 1


def _spot_simulator(usd: float = 1000.0, btc: float = 0.0) -> TradeSimulator:
    """
    The SIMULATION executor in spot mode, zero latency, with one tick fed.

    The committed-funds check lives on the shared base class, so the simulation must refuse
    exactly what the live executor refuses — that is the framework's central claim, and this
    harness is what measures it instead of trusting the shared code path.

    Args:
        usd: Starting quote balance
        btc: Starting base balance

    Returns:
        The simulator, ready to take orders
    """
    sim = TradeSimulator(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT,
                                   MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=usd,
        account_currency='USD',
        logger=GlobalLogger('CommittedFundsSim'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
        spot_mode=True,
        initial_balances={'USD': usd, 'BTC': btc},
    )
    _feed_sim_tick(sim, msc=1000)
    return sim


def _feed_sim_tick(sim: TradeSimulator, msc: int) -> None:
    """
    One BTCUSD tick at the harness prices, stamped explicitly.

    Args:
        sim: The simulator
        msc: The tick's millisecond stamp
    """
    sim.on_tick(TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=_BID, ask=_ASK, collected_msc=msc, time_msc=msc,
    ))


class TestBothPipelinesAgree:
    """
    "Identical in both pipelines" is measured here, not assumed from the shared code path.

    The refusal is on `AbstractTradeExecutor`, so the simulation inherits it — but a claim
    about parity that rests on where the code sits is a claim, and this class turns it into
    a reading. The fill-time site is exercised in its POSITIVE direction: it must count the
    other orders' claims and exclude its own, so a fill the account can afford goes through.
    A fill-time REFUSAL cannot be produced here — submission and fill compute the same figure,
    so only a balance change from outside (another actor, a fee on a third fill) can open a
    gap between them, and the harness has no such actor.
    """

    def test_the_simulation_refuses_the_second_order_too(self):
        """Same account, same two orders, same refusal — on the other pipeline."""
        sim = _spot_simulator(usd=1000.0)
        sim.open_order(_limit(OrderDirection.LONG, 0.012, _RESTING_BUY_PRICE))
        _feed_sim_tick(sim, msc=1001)          # latency drains; the LIMIT rests

        second = sim.open_order(_market(OrderDirection.LONG, 0.01))

        assert second.status == OrderStatus.REJECTED
        assert second.rejection_reason == RejectionReason.INSUFFICIENT_FUNDS
        assert 'committed' in second.rejection_message

    def test_the_simulation_passes_it_without_a_resting_order(self):
        """The regression guard on the sim side."""
        sim = _spot_simulator(usd=1000.0)

        result = sim.open_order(_market(OrderDirection.LONG, 0.01))

        assert result.status != OrderStatus.REJECTED

    def test_the_fill_site_reads_others_claims_and_excludes_the_filling_order(self):
        """
        The figure the fill-time check reads: everyone else's claim, never its own.

        A resting LIMIT claims ~294 and a market order in transit claims ~501. When the
        market order fills, the check must see 1000 − 294 (available for it), not
        1000 − 294 − 501 — the latter would refuse an order the account can plainly afford.
        Asserted on the query the fill site calls, with both orders in flight: this is
        deterministic, whereas driving the mock to a fill is not (the mock fills a LIMIT
        regardless of price, so "resting" cannot be held while the market order fills).
        """
        _, executor = _spot_executor(usd=1000.0)
        resting = executor.open_order(_limit(OrderDirection.LONG, 0.006, _RESTING_BUY_PRICE))
        market = executor.open_order(_market(OrderDirection.LONG, 0.01))

        assert market.status != OrderStatus.REJECTED       # 1000 − 294 covers ~501

        # What the fill site reads for each order: everyone else's claim, not its own.
        seen_by_market = executor.get_committed_funds('USD', exclude_order_id=market.order_id)
        seen_by_resting = executor.get_committed_funds('USD', exclude_order_id=resting.order_id)
        both = executor.get_committed_funds('USD')

        # Excluding the market order leaves the resting LIMIT's claim (notional + maker fee) …
        assert 0.0 < seen_by_market < 0.006 * _RESTING_BUY_PRICE * 1.02
        # … excluding the LIMIT leaves the market order's claim (notional at ask + taker fee) …
        assert 0.0 < seen_by_resting < 0.01 * _ASK * 1.02
        # … and together they are exactly the sum: nothing counted twice, nothing dropped.
        assert both == pytest.approx(seen_by_market + seen_by_resting)
