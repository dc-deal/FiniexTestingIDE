"""
How much capital may a bot commit to a new entry — asked once, answered per account model (#502).

Every CORE decision logic gates its entries on `AccountInfo.free_margin`. On a SPOT account that
figure is, measured against the Kraken config on 2026-09-23:

    free_margin = free quote balance + unrealized P&L on the holdings - lots

The dominant term is the middle one, and it is the defect: an unrealized gain on a coin is not
cash, and it cannot be spent on the next entry. The error is not a bias in one direction, which
would at least be correctable — it points BOTH ways. On a 1000 USD account holding 0.1 ETH bought
at 3000:

    ETH 3000   free quote 700.00   free_margin  699.90    the gate is roughly right
    ETH 6000   free quote 700.00   free_margin  999.90    it offers 43 % more than exists
    ETH 2000   free quote 700.00   free_margin  599.90    it withholds capital the account has

The trailing `- lots` is a second, small defect with its own cause: Kraken declares its margin in
the QUOTE currency at leverage 1, so the margin formula takes the branch that multiplies no price
and charges 0.1 ETH as 0.1 USD.

Neither is what the issue expected to find — it predicted that `balance` would not follow a spot
purchase at all. It does: `balance` IS `_balances[account_currency]`, in both account models.

The executor has answered the same question correctly per model since #489 — free margin on
margin, quote balance minus what this bot's own unfilled orders already claim on spot. What was
missing was a way for a DECISION to ask it. This suite pins both halves: what the old quantity
really does at spot, and that the new one follows the money instead.

Driven directly against the portfolio and the simulator — one mark, no latency, no scenario.
The quantity is the subject, the same shape as `test_account_value.py`.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'

_SYMBOL = 'ETHUSD'
_QUOTE = 'USD'
_BASE = 'ETH'
_PRICE = 3_000.0
_LOTS = 0.1
_SPENT = _LOTS * _PRICE          # 300 USD of a 1000 USD account
_START_QUOTE = 1_000.0
_CLOCK = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

_MT5_SYMBOL = 'EURUSD'
_MT5_PRICE = 1.10
_BALANCE = 10_000.0


class _NullLogger:
    """Minimal duck-typed logger — the arithmetic under test does not log."""

    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _spot_portfolio(balances: Optional[Dict[str, float]] = None) -> PortfolioManager:
    """
    A Kraken spot portfolio — leverage 1, contract size 1, margin declared in the quote.

    Args:
        balances: The opening inventory; the default is quote only, as a fresh bot starts

    Returns:
        A PortfolioManager in spot mode with a fixed clock
    """
    return PortfolioManager(
        logger=_NullLogger(), initial_balance=_START_QUOTE, account_currency=_QUOTE,
        broker_config=BrokerConfigFactory.build_broker_config(_KRAKEN_CONFIG),
        leverage=1, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=True,
        initial_balances=balances if balances is not None else {_QUOTE: _START_QUOTE},
        clock_fn=lambda: _CLOCK)


def _mark(portfolio: PortfolioManager, symbol: str, price: float) -> None:
    """Move the market to `price` and settle the open positions against it."""
    portfolio.mark_dirty(TickData(
        timestamp=_CLOCK, symbol=symbol, bid=price, ask=price))
    portfolio._ensure_positions_updated()


def _buy(portfolio: PortfolioManager, lots: float = _LOTS) -> None:
    """Spend quote on base through the real path, so the balances actually move."""
    portfolio.open_position(
        order_id='p1', symbol=_SYMBOL, direction=OrderDirection.LONG, lots=lots,
        entry_price=_PRICE, entry_tick_value=1.0, entry_bid=_PRICE, entry_ask=_PRICE,
        digits=2, contract_size=1, entry_tick_index=0)
    _mark(portfolio, _SYMBOL, _PRICE)


class TestTheQuantityTheBotsGateOnIsNotSpendableCash:
    """
    The defect, stated as the difference between two numbers rather than as a judgement.

    These cases stay true after the fix — the `free_margin` formula is deliberately out of
    scope (#497 owns whether it should be None at spot). They are the evidence that switching
    the gate was not a no-op.
    """

    def test_an_unrealized_gain_is_offered_as_capital_that_cannot_be_spent(self):
        """The dominant error: a coin that doubled did not put any cash in the account."""
        portfolio = _spot_portfolio()
        _buy(portfolio)
        _mark(portfolio, _SYMBOL, _PRICE * 2)

        free_quote = portfolio.get_asset_balance(_QUOTE)
        reported = portfolio.get_free_margin(OrderDirection.LONG)

        assert free_quote == pytest.approx(_START_QUOTE - _SPENT)
        assert reported > free_quote * 1.4, (
            f'free_margin offers {reported:.2f} USD while {free_quote:.2f} USD is actually '
            f'spendable — the difference is the holding\'s unrealized gain, which is not cash')

    def test_and_a_decline_withholds_capital_the_account_really_has(self):
        """
        The same defect pointing the other way, which is what makes it uncorrectable.

        A constant bias could be absorbed by choosing the floor differently. This one cannot:
        the gate is loosest exactly when the account is most exposed, and tightest when it is
        least — and both happen without any money moving.
        """
        portfolio = _spot_portfolio()
        _buy(portfolio)
        _mark(portfolio, _SYMBOL, _PRICE / 1.5)

        free_quote = portfolio.get_asset_balance(_QUOTE)
        reported = portfolio.get_free_margin(OrderDirection.LONG)

        assert reported < free_quote, (
            f'free_margin reports {reported:.2f} USD where {free_quote:.2f} USD is free — a '
            f'gate on this refuses entries the account can afford, in a drawdown')

    def test_the_margin_used_it_subtracts_is_the_lot_size_not_a_value(self):
        """
        Why the number is detached, pinned at the source.

        `margin_currency == quote_currency` at Kraken spot, so the margin formula takes the
        branch that multiplies no price: 0.1 ETH worth 300 USD is charged as 0.1.
        """
        portfolio = _spot_portfolio()
        _buy(portfolio)

        account = portfolio.get_account_info(OrderDirection.LONG)

        assert account.margin_used == pytest.approx(_LOTS), (
            f'margin_used is {account.margin_used}, and the position is worth {_SPENT} USD')


class TestFreeEntryCapitalAnswersPerWorld:
    """
    One method, two account models — which is what lets one gate serve both (#502).

    The margin answer must be `free_margin` EXACTLY: 44 checked-in mt5 scenarios gate on it,
    and a fix that moved them would be a second defect wearing the first one's clothes.
    """

    @staticmethod
    def _spot_simulator(balances: Optional[Dict[str, float]] = None) -> TradeSimulator:
        """A Kraken spot simulator, marked at the working price. Returns: the simulator."""
        simulator = TradeSimulator(
            broker_config=BrokerConfigFactory.build_broker_config(_KRAKEN_CONFIG),
            initial_balance=_START_QUOTE, account_currency=_QUOTE, logger=_NullLogger(),
            spot_mode=True,
            initial_balances=balances if balances is not None else {_QUOTE: _START_QUOTE})
        simulator.on_tick(TickData(
            timestamp=_CLOCK, symbol=_SYMBOL, bid=_PRICE, ask=_PRICE))
        return simulator

    @staticmethod
    def _margin_simulator() -> TradeSimulator:
        """An MT5 margin simulator, marked at the working price. Returns: the simulator."""
        simulator = TradeSimulator(
            broker_config=BrokerConfigFactory.build_broker_config(_MT5_CONFIG),
            initial_balance=_BALANCE, account_currency=_QUOTE, logger=_NullLogger())
        simulator.on_tick(TickData(
            timestamp=_CLOCK, symbol=_MT5_SYMBOL, bid=_MT5_PRICE, ask=_MT5_PRICE))
        return simulator

    def test_spot_answers_the_free_quote_balance(self):
        simulator = self._spot_simulator()

        assert simulator.get_free_entry_capital(
            _SYMBOL, OrderDirection.LONG) == pytest.approx(_START_QUOTE)

    def test_and_it_falls_by_exactly_what_was_spent(self):
        """The property the old quantity does not have, which is the whole point."""
        simulator = self._spot_simulator()
        _buy(simulator.portfolio)

        assert simulator.get_free_entry_capital(
            _SYMBOL, OrderDirection.LONG) == pytest.approx(_START_QUOTE - _SPENT)

    def test_a_spot_sell_reads_the_holding_valued_at_the_mark(self):
        """
        Selling spends the coin, not the cash, so the capital for a SELL is the holding.

        Answering the free QUOTE here would make a gate refuse a sell for lack of money it
        does not need — the mirror of the defect above. Valued rather than counted, because a
        single configured floor has to mean one thing in both directions.
        """
        simulator = self._spot_simulator(balances={_QUOTE: 0.0, _BASE: 2.0})

        assert simulator.get_free_entry_capital(
            _SYMBOL, OrderDirection.SHORT) == pytest.approx(2.0 * _PRICE)

    def test_margin_answers_exactly_free_margin(self):
        """Bit-identical, or 44 checked-in mt5 scenarios move for no reason."""
        simulator = self._margin_simulator()

        assert simulator.get_free_entry_capital(_MT5_SYMBOL, OrderDirection.LONG) == \
            simulator.portfolio.get_free_margin(OrderDirection.LONG)


class TestTheBotsOwnRestingOrdersAreSubtracted:
    """
    A venue reserves on PLACEMENT; our balances move on FILL (#489).

    Without this a gate reads a balance that two unfilled orders can each spend in full, and
    the second is refused by the venue while our own books still showed the money. The
    executor's funds check has counted this since #489 — a gate that did not would send the
    order the check then refuses, which is a rejection the bot could have avoided asking for.
    """

    @staticmethod
    def _resting_buy(simulator: TradeSimulator, price: float):
        """Place a LIMIT BUY far below the market so it rests. Returns: the OrderResult."""
        return simulator.open_order(OpenOrderRequest(
            symbol=_SYMBOL, order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
            lots=_LOTS, price=price))

    def test_an_unfilled_buy_reduces_the_capital_a_second_one_may_use(self):
        simulator = TestFreeEntryCapitalAnswersPerWorld._spot_simulator()
        before = simulator.get_free_entry_capital(_SYMBOL, OrderDirection.LONG)

        self._resting_buy(simulator, _PRICE * 0.5)
        after = simulator.get_free_entry_capital(_SYMBOL, OrderDirection.LONG)

        assert after < before, (
            f'a resting BUY claims quote the moment it is placed; the capital went '
            f'{before:.2f} -> {after:.2f}')

    def test_the_order_being_filled_can_be_excluded(self):
        """Counting the order under execution would reserve it twice — the fill's own case."""
        simulator = TestFreeEntryCapitalAnswersPerWorld._spot_simulator()
        result = self._resting_buy(simulator, _PRICE * 0.5)

        assert simulator.get_free_entry_capital(
            _SYMBOL, OrderDirection.LONG,
            exclude_order_id=result.order_id) == pytest.approx(_START_QUOTE)
