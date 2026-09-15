"""
One account value per model, and the circuit breaker reads it (#356 / #492).

The live breaker used to read a different quantity per account model: spot got its
spot-aware equity, margin got `get_balance()`. A margin balance moves only on REALISED P&L
by construction, so an open drawdown is invisible to it — which made the account model that
can lose MORE than it holds the one whose breaker could not see the loss coming. The
drawdown series three methods away had the right number the whole time; it simply was not
the number the breaker asked for.

`get_account_value()` is that one number, public since #356. It also answers None rather
than guessing: spot holdings cannot be valued before a price exists, and an unvalued holding
is not a drawdown.

Driven directly against the PortfolioManager — no ticks, no latency, no scenario. The
quantity is the subject.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection

_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'
_SYMBOL = 'EURUSD'
_ENTRY = 1.10
_LOTS = 1.0
_BALANCE = 10_000.0
_CLOCK = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class _NullLogger:
    """Minimal duck-typed logger — the arithmetic under test does not log."""

    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _portfolio(
    spot_mode: bool = False,
    initial_balances: Optional[Dict[str, float]] = None,
) -> PortfolioManager:
    """
    Build a portfolio over the real MT5 broker config.

    Args:
        spot_mode: Build the asset-inventory portfolio instead of the margin one
        initial_balances: Asset inventory for spot mode — a holding is what makes a spot
            account value move at all, since it lives in the balances and not in a position

    Returns:
        A PortfolioManager with a fixed clock
    """
    return PortfolioManager(
        logger=_NullLogger(), initial_balance=_BALANCE, account_currency='USD',
        broker_config=BrokerConfigFactory.build_broker_config(_MT5_CONFIG),
        leverage=500, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode,
        initial_balances=initial_balances,
        clock_fn=lambda: _CLOCK)


def _mark(portfolio: PortfolioManager, price: float) -> None:
    """Move the market to `price` and settle the open positions against it."""
    portfolio.mark_dirty(TickData(
        timestamp=_CLOCK, symbol=_SYMBOL, bid=price, ask=price))
    portfolio._ensure_positions_updated()


def _open_long(portfolio: PortfolioManager) -> Position:
    """A plain LONG at the entry price, marked there."""
    position = Position(
        position_id='p1', symbol=_SYMBOL, direction=OrderDirection.LONG, lots=_LOTS,
        original_lots=_LOTS, entry_price=_ENTRY, entry_time=_CLOCK,
        entry_tick_value=1.0, digits=5, contract_size=100000)
    portfolio.open_positions['p1'] = position
    _mark(portfolio, _ENTRY)
    return position


class TestMarginSeesAnOpenLoss:
    """
    The defect this fixes, stated as the difference between two numbers.

    A margin position moving against us costs real money the moment it moves. Settled cash
    does not know that yet.
    """

    def test_the_account_value_falls_while_the_balance_does_not(self):
        portfolio = _portfolio()
        _open_long(portfolio)

        _mark(portfolio, _ENTRY - 0.0100)

        assert portfolio.balance == pytest.approx(_BALANCE), (
            'settled cash is unchanged, which is correct — nothing was realised')
        value = portfolio.get_account_value()
        assert value is not None
        assert value < _BALANCE, (
            f'the account value is {value}, so an open loss of about 1000 USD is invisible '
            f'to whatever reads it — that is the breaker measuring the riskier model on the '
            f'weaker scale')

    def test_and_it_rises_on_an_open_gain(self):
        portfolio = _portfolio()
        _open_long(portfolio)

        _mark(portfolio, _ENTRY + 0.0100)

        assert portfolio.get_account_value() > _BALANCE

    def test_a_flat_margin_account_reads_its_balance(self):
        """Without a position the two answers must agree, or every baseline shifts."""
        portfolio = _portfolio()
        _mark(portfolio, _ENTRY)

        assert portfolio.get_account_value() == pytest.approx(portfolio.balance)


class TestSpotAnswersNoneRatherThanGuessing:
    """
    An unvalued holding is not a drawdown.

    The breaker now skips the check on None instead of measuring against a substitute — the
    same discipline as a missing clock raising rather than falling back to wall time (§9).
    """

    def test_no_price_means_no_number(self):
        portfolio = _portfolio(spot_mode=True)

        assert portfolio.get_account_value() is None, (
            'a substituted value here would be a drawdown reading invented out of nothing')

    def test_with_a_price_it_is_the_spot_equity(self):
        portfolio = _portfolio(spot_mode=True)
        _mark(portfolio, _ENTRY)

        assert portfolio.get_account_value() == pytest.approx(
            portfolio.get_spot_equity(_ENTRY))


class TestTheDrawdownSeriesAndTheBreakerShareIt:
    """
    One definition, two consumers — which is the whole reason the method is public.

    While they were separate, the series and the breaker could disagree about whether the
    account had lost anything, and the report would show one of the two.
    """

    def test_the_running_drawdown_is_fed_from_the_same_call(self):
        portfolio = _portfolio()
        _open_long(portfolio)
        _mark(portfolio, _ENTRY - 0.0050)

        portfolio.sample_equity()

        value = portfolio.get_account_value()
        assert portfolio.get_portfolio_statistics().account_max_drawdown == pytest.approx(_BALANCE - value), (
            'the series and the breaker would then disagree about whether the account lost '
            'anything, and the report shows one of the two')


class TestTheMinFloorMeansTheSameThingInBothModels:
    """
    The consequence of the change, made explicit so it cannot drift back.

    `min_balance` and `min_equity` now denominate the SAME quantity — the account value —
    and the account model only decides which of the two config keys a profile writes. Before
    #356 the margin floor was settled cash, which by construction moves only on realised
    P&L: an open loss could not reach it at all, however deep it got.

    The field names were kept because six live profiles set them. What changed is what they
    are compared against, and that is stated at every place a reader meets them.
    """

    def test_an_open_loss_can_now_reach_the_margin_floor(self):
        portfolio = _portfolio()
        _open_long(portfolio)
        floor = _BALANCE - 500.0

        _mark(portfolio, _ENTRY - 0.0100)

        assert portfolio.balance > floor, (
            'settled cash never reaches the floor — that was the defect')
        assert portfolio.get_account_value() < floor, (
            'the floor is now reachable by an open loss, which is the whole point')


class TestTheDrawdownPercentageIsMeasuredAgainstThePeakOfTheMoment:
    """
    The percentage and the two floats beside it belong to DIFFERENT instants.

    `account_max_drawdown` is the deepest decline; `max_equity` is the highest point ever reached.
    But the deepest decline fell from whatever peak stood AT THE TIME, and a later, higher
    peak does not make it shallower. So dividing the two finished figures by each other is
    not the maximum drawdown percentage — it is a smaller number, and it is smaller on
    exactly the runs that recovered, which is every profitable one.

    This is why the percentage is carried per sample rather than derived at the end, and it
    is what puts the two pipelines on one construction: the live safety reading has always
    divided by its baseline of the moment (#497).
    """

    def test_a_later_higher_peak_does_not_flatter_the_earlier_decline(self):
        portfolio = _portfolio()
        _open_long(portfolio)

        _mark(portfolio, _ENTRY + 0.0100)
        portfolio.sample_equity()
        peak = portfolio.get_account_value()

        _mark(portfolio, _ENTRY - 0.0050)
        portfolio.sample_equity()
        trough = portfolio.get_account_value()

        _mark(portfolio, _ENTRY + 0.0200)
        portfolio.sample_equity()

        stats = portfolio.get_portfolio_statistics()
        assert stats.account_max_drawdown_pct == pytest.approx((peak - trough) / peak * 100)

        naive = stats.account_max_drawdown / stats.max_equity * 100
        assert stats.account_max_drawdown_pct > naive, (
            f'the carried percentage is {stats.account_max_drawdown_pct:.2f} % and the quotient of '
            f'the two finished figures is {naive:.2f} % — the quotient is what a reader gets '
            f'when the report divides at the end, and it understates the risk that was taken')

    def test_without_a_later_peak_the_two_constructions_agree(self):
        """The guard against overcorrecting — they may differ only where they should."""
        portfolio = _portfolio()
        _open_long(portfolio)

        _mark(portfolio, _ENTRY + 0.0100)
        portfolio.sample_equity()
        _mark(portfolio, _ENTRY - 0.0050)
        portfolio.sample_equity()

        stats = portfolio.get_portfolio_statistics()
        assert stats.account_max_drawdown_pct == pytest.approx(
            stats.account_max_drawdown / stats.max_equity * 100)

    def test_an_account_that_only_rose_reports_no_drawdown(self):
        portfolio = _portfolio()
        _open_long(portfolio)

        for step in (0.0050, 0.0100, 0.0200):
            _mark(portfolio, _ENTRY + step)
            portfolio.sample_equity()

        assert portfolio.get_portfolio_statistics().account_max_drawdown_pct == 0.0

    def test_spot_measures_it_the_same_way(self):
        """
        §31b: a money path is checked in BOTH account models, never reasoned through on one.

        A spot holding lives in the BALANCES, so the account value moves with every price
        without any position being open — which is precisely the model the thirty-day run
        uses.
        """
        portfolio = _portfolio(
            spot_mode=True, initial_balances={'USD': 5_000.0, 'EUR': 5_000.0})

        _mark(portfolio, _ENTRY + 0.0100)
        portfolio.sample_equity()
        peak = portfolio.get_account_value()

        _mark(portfolio, _ENTRY - 0.0050)
        portfolio.sample_equity()
        trough = portfolio.get_account_value()

        _mark(portfolio, _ENTRY + 0.0200)
        portfolio.sample_equity()

        stats = portfolio.get_portfolio_statistics()
        assert stats.account_max_drawdown_pct == pytest.approx((peak - trough) / peak * 100)
        assert stats.account_max_drawdown_pct > stats.account_max_drawdown / stats.max_equity * 100
