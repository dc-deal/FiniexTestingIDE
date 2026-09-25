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


class TestASpotAccountIsOfferedNoMarginFiguresAtAll:
    """
    What replaced the defect, and the history is kept because the numbers are the argument.

    Until 2026-09-24 a spot account received `margin_used`, `free_margin` and `margin_level`
    on its `AccountInfo`, and all three were invented. `margin_currency == quote_currency` at
    Kraken spot and leverage is 1, so the margin formula took the branch that multiplies no
    price: a 0.1 ETH position worth 300 USD was charged as **0.1**. `free_margin` inherited
    that and moved with the holdings' unrealized P&L, so it pointed BOTH ways — measured on a
    1000 USD account holding 0.1 ETH bought at 3000, against 700.00 USD actually spendable:

        ETH 6000  →  free_margin 999.90   offers 43 % more than exists
        ETH 2000  →  free_margin 599.90   withholds capital the account has

    No choice of floor absorbs a bias that changes sign. #502 moved the CORE logics onto
    `get_free_entry_capital`; this suite now pins the second half — the misleading figures are
    not merely unused, they are no longer produced. A false map is worse than a blank one.
    """

    def test_the_three_margin_figures_are_absent(self):
        portfolio = _spot_portfolio()
        _buy(portfolio)

        account = portfolio.get_account_info(OrderDirection.LONG)

        assert account.margin_used is None
        assert account.free_margin is None
        assert account.margin_level is None

    def test_the_figures_a_spot_account_really_has_are_untouched(self):
        """
        The guard against overcorrecting: only the margin trio went.

        `balances` is the spot account's own answer and `equity` is its mark-to-market — both
        must still be there, or the blanking took something a bot legitimately reads.
        """
        portfolio = _spot_portfolio()
        _buy(portfolio)

        account = portfolio.get_account_info(OrderDirection.LONG)

        assert account.balances is not None
        assert account.balances[_QUOTE] == pytest.approx(_START_QUOTE - _SPENT)
        assert account.equity > 0
        assert account.open_positions == 1

    def test_asking_for_free_margin_at_spot_is_refused_by_name(self):
        """
        Loudly rather than as None: every caller treats the answer as a number to compare
        against, and both of them already sit behind `not spot_mode`. Reaching it here is a
        programming error, and the message names the method that does answer the question.
        """
        portfolio = _spot_portfolio()
        _buy(portfolio)

        with pytest.raises(ValueError) as raised:
            portfolio.get_free_margin(OrderDirection.LONG)

        assert 'get_free_entry_capital' in str(raised.value)

    def test_no_margin_is_computed_per_position_at_spot(self):
        """
        The second reason, and it is why the figures are not merely blanked at the end.

        The loop asked the broker adapter once per OPEN POSITION, every time the account was
        asked for — on a method the decision path reaches through `get_free_entry_capital`.
        """
        portfolio = _spot_portfolio()
        _buy(portfolio)
        calls = []
        original = portfolio.broker_config.calculate_margin
        portfolio.broker_config.calculate_margin = (
            lambda *a, **k: calls.append(a) or original(*a, **k))

        portfolio.get_account_info(OrderDirection.LONG)

        assert calls == [], f'the broker was asked for margin {len(calls)} time(s) at spot'


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
