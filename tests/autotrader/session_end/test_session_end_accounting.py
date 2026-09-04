"""
FiniexTestingIDE - Session-End Accounting Tests (#492)

Realised and valued, kept apart. A run may end holding something, so the model has to answer
two different questions without mixing them:

    trade statistics   →  COMPLETED trades only
    net_profit         →  realised
    final_equity       →  realised + what is still held, valued

Two guards here are worth more than the rest.

**The phantom-drawdown guard.** The run-end equity sample has to use the SPOT portfolio value,
not the margin-style `balance + unrealized_pnl`: in spot mode `balance` is the QUOTE balance
alone, so a held coin contributes only its unrealised gain and never its value. Measured on a
1000 USD account buying 0.01 BTC: 1.57 USD the right way (fee plus spread, real) against
603 USD the wrong way — 384x, and the wrong one reads as a 60 % drawdown caused by a purchase.

**The console guard.** `total_trades == 0` used to print "No trades executed" and return,
skipping balances, costs and the position the unit was holding — so a buy-and-hold run
reported nothing at all.
"""

import pytest

from python.framework.reporting.builders.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_RUN_ID = '20260903_143000_abcd1234'


def _spot_session_holding_one_coin():
    """
    A spot executor that bought and still holds, with the equity sampled at the end.

    Returns:
        (mock, executor) — the portfolio carries one open position and a sampled curve
    """
    mock = MockOrderExecution(
        mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
        initial_balances={'USD': 1000.0, 'BTC': 0.0})
    executor = mock.create_executor()

    mock.feed_tick(executor, bid=59999.0, ask=60001.0)
    executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.01))
    mock.feed_tick(executor, bid=59999.0, ask=60001.0)
    return mock, executor


class TestTheEquitySampleIsSpotAware:
    """The guard against reporting a purchase as a drawdown."""

    def test_buying_does_not_produce_a_drawdown(self):
        mock, executor = _spot_session_holding_one_coin()
        portfolio = executor.portfolio

        portfolio.sample_equity()
        stats = portfolio.get_portfolio_statistics()

        # Fee plus the spread between ask (bought) and mid (valued) — a couple of dollars
        # on a 1000 USD account, not six hundred.
        assert stats.max_drawdown < 10.0, (
            f'a purchase read as a {stats.max_drawdown:.0f} USD drawdown — the sample used '
            f'the margin-style equity, which leaves the coin out')

    def test_the_margin_formula_would_have_reported_a_phantom(self):
        """
        Pins WHY the spot branch exists, so removing it fails loudly.

        Without this, someone simplifying `sample_equity` back to `_calculate_equity()`
        gets a green suite and a 60 % drawdown in every spot report.
        """
        mock, executor = _spot_session_holding_one_coin()
        portfolio = executor.portfolio

        portfolio.sample_equity()
        spot_drawdown = portfolio.get_portfolio_statistics().max_drawdown
        naive_drawdown = portfolio._max_equity - portfolio._calculate_equity()

        assert naive_drawdown > 50 * max(spot_drawdown, 0.01), (
            'the two formulas no longer disagree — either the spot branch is gone or the '
            'balance model changed, and both need looking at')

    def test_no_sample_without_a_price(self):
        """
        A SPOT position nobody could price contributes no drawdown.

        The earlier version of this test built neither the position nor the spot mode its
        own docstring named — an empty margin portfolio with no tick, which cannot produce
        a drawdown for reasons that have nothing to do with the guard. It passed before the
        guard existed and would pass after it was removed. This one holds a real position
        and a real spot portfolio, and only the missing price stops the sample.
        """
        mock = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
            initial_balances={'USD': 1000.0, 'BTC': 0.02})
        executor = mock.create_executor()
        portfolio = executor.portfolio
        # A holding without a single tick — the #355 abort shape: the book was restored,
        # the tick source then failed.
        assert portfolio.get_asset_balance('BTC') == pytest.approx(0.02)
        assert portfolio.get_portfolio_statistics().max_equity > 0

        portfolio.sample_equity()

        assert portfolio.get_portfolio_statistics().max_drawdown == 0.0

    def test_the_curve_keeps_one_scale_across_a_close(self):
        """
        The close path and the run-end sample must measure the SAME quantity.

        `_update_statistics` — which runs on every close — used to call the margin-style
        formula directly while the run-end sample used the spot-aware one, so a spot run
        wrote two scales into one running maximum.

        Finding the case that DISCRIMINATES took two attempts, and the failures are worth
        recording. A buy-then-close needs a base holding the account had BEFORE it traded,
        or the two formulas coincide once the position is sold. Even then they agree, because
        the run-end sample lands LAST and a point that only RAISES the maximum produces no
        drawdown at the instant it is written. The scales only diverge visibly when the
        portfolio LOSES value that the quote balance cannot see — a pre-existing holding
        falling in price while a trade closes at a loss. Here: peak 2200 and trough ~2050 on
        the portfolio (~150), against peak 1000 and trough ~950 on the quote balance (~50).
        """
        mock = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
            initial_balances={'USD': 1000.0, 'BTC': 0.02})
        executor = mock.create_executor()
        portfolio = executor.portfolio

        mock.feed_tick(executor, bid=59999.0, ask=60001.0)
        portfolio.sample_equity()                     # the peak, at portfolio scale
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.01))
        mock.feed_tick(executor, bid=59999.0, ask=60001.0)
        mock.feed_tick(executor, bid=54999.0, ask=55001.0)     # the holding loses value
        executor.close_position(executor.get_open_positions()[0].position_id)
        mock.feed_tick(executor, bid=54999.0, ask=55001.0)     # the CLOSE writes the trough

        stats = portfolio.get_portfolio_statistics()

        assert stats.total_trades == 1, 'fixture failed to complete a trade'
        assert stats.max_equity > 2000.0, (
            f'the peak is on the quote scale ({stats.max_equity:.2f}) — the sample is not '
            f'seeing the portfolio')
        # Measured, both directions: 151.58 with one scale, 1251.58 with two. The window
        # has to be closed on BOTH sides — a one-sided `> 100` passes against the mixed
        # version as well, which is how the first two attempts at this test slipped through.
        assert 100.0 < stats.max_drawdown < 300.0, (
            f'drawdown {stats.max_drawdown:.2f} on a portfolio that fell ~150: above the '
            f'window means the close wrote a quote-scale point under a portfolio-scale '
            f'peak, below it means the peak is on the quote scale')

class TestTheModelKeepsRealisedAndValuedApart:
    """The report carries both, and never folds one into the other."""

    def _row(self):
        """Build the portfolio row of a unit that ended holding one position."""
        mock, executor = _spot_session_holding_one_coin()
        mock.feed_tick(executor, bid=61999.0, ask=62001.0)
        portfolio = executor.portfolio
        portfolio.sample_equity()
        stats = portfolio.get_portfolio_statistics()
        stats.symbol = 'BTCUSD'
        stats.base_currency = 'BTC'
        stats.quote_currency = 'USD'
        stats.last_price = 62000.0

        unit = RunUnit(
            name='session_end_probe', symbol='BTCUSD',
            portfolio_stats=stats,
            open_positions=executor.get_open_positions(),
            session_end_policy='cancel/leave')
        return build_portfolio_report(_RUN_ID, [unit]).units[0]

    def test_the_open_position_is_reported(self):
        row = self._row()

        assert len(row.open_positions) == 1
        position = row.open_positions[0]
        assert position.direction == 'long'
        assert position.lots == pytest.approx(0.01)
        assert position.entry_price > 0
        assert position.valued is True
        assert position.last_price == 62000.0

    def test_it_is_not_a_completed_trade(self):
        row = self._row()

        assert row.total_trades == 0, 'an open position must not count as a trade'
        assert row.win_rate == 0.0
        assert row.net_profit == 0.0, 'nothing was realised'

    def test_its_value_is_in_the_wealth_view(self):
        row = self._row()

        # Bought 0.01 at ~60000, marked at 62000 → the account is worth more than it
        # started, and only final_equity says so.
        assert row.final_equity > row.initial_balance, (
            f'final_equity {row.final_equity} does not carry the coin at 62000')
        assert row.unrealized_pnl > 0

    def test_the_wealth_view_says_whether_it_is_a_valuation(self):
        row = self._row()

        assert row.final_equity_valued is True, (
            'everything open was priced, so the figure IS a mark-to-market')

    def test_no_adopted_flag_is_offered(self):
        """
        The row deliberately has no `adopted` field.

        Its only available derivation — an empty `entry_trades` — is a structural constant
        False: a position opened by this run has its executions synthesised before the
        portfolio ever sees it, and one restored from the carry-over has them round-tripped
        on purpose. Whether a position was inherited is answered by the cold-start section,
        keyed by the same position_id.
        """
        assert 'adopted' not in type(self._row().open_positions[0]).model_fields

    def test_the_policy_travels_with_the_row(self):
        row = self._row()

        assert row.session_end_policy == 'cancel/leave'

    def test_an_unvalued_position_says_so(self):
        """No tick, no mark — and no invented price."""
        mock = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
            initial_balances={'USD': 1000.0, 'BTC': 0.0})
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=59999.0, ask=60001.0)
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.01))
        mock.feed_tick(executor, bid=59999.0, ask=60001.0)

        stats = executor.portfolio.get_portfolio_statistics()
        stats.base_currency, stats.quote_currency = 'BTC', 'USD'
        # last_price deliberately left at its default: the session had no price to report
        unit = RunUnit(
            name='no_tick', symbol='BTCUSD', portfolio_stats=stats,
            open_positions=executor.get_open_positions())

        row = build_portfolio_report(_RUN_ID, [unit]).units[0]

        assert row.open_positions[0].valued is False
        assert row.open_positions[0].last_price == 0.0
        assert row.open_positions[0].entry_price > 0, 'the entry is known even without a tick'
