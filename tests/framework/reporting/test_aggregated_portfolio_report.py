"""
Aggregated-Portfolio Report Builder Tests (#397).

`build_aggregated_portfolio_report` rolls the per-scenario portfolio / execution / pending model
rows up per account currency into the rich detail view (combined + margin/spot split for mixed
batches), with weighted-average latency. Tested with REAL PortfolioUnitRow / ExecutionStatsRow /
PendingOrdersUnitRow fixtures — the formulas must match the retired `PortfolioAggregator`.
"""

import io
import re
from contextlib import redirect_stdout

import pytest

from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report,
)
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsRow,
    ExecutionStatsTotals,
    PendingOrdersReport,
    PendingOrdersUnitRow,
    PortfolioReport,
    PortfolioUnitRow,
)
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


def _pf(name, currency='USD', symbol='EURUSD', spot=False, trades=2, win=1, lose=1,
        profit=100.0, loss=40.0, max_dd=12.0, max_eq=1000.0, max_dd_pct=0.0, fees=5.0,
        spread=3.0, maker=0.0, taker=0.0, initial=1000.0, current=1060.0, long=1, short=1,
        balances=None, initial_balances=None, last_price=0.0) -> PortfolioUnitRow:
    # A spot row is stamped the way the portfolio builder stamps it: the currency split from the
    # broker config and the unit's own value estimate — never read back out of the symbol.
    base, quote = (symbol[:-3], symbol[-3:]) if spot else ('', '')
    balances, initial_balances = balances or {}, initial_balances or {}
    est = (lambda held: held.get(quote, 0.0) + held.get(base, 0.0) * last_price)
    spot_current = est(balances) if spot and last_price > 0 else 0.0
    spot_initial = est(initial_balances) if spot and last_price > 0 else 0.0
    return PortfolioUnitRow(
        name=name, symbol=symbol, currency=currency, total_trades=trades,
        winning_trades=win, losing_trades=lose, win_rate=(win / trades if trades else 0.0),
        profit_factor=(profit / loss if loss else 0.0), total_profit=profit, total_loss=loss,
        net_profit=profit - loss, account_max_drawdown=max_dd, total_fees=fees, spot_mode=spot,
        total_long_trades=long, total_short_trades=short, max_equity=max_eq,
        account_max_dd_pct=max_dd_pct,
        current_balance=current, initial_balance=initial, total_spread_cost=spread,
        maker_fee=maker, taker_fee=taker, base_currency=base, quote_currency=quote,
        spot_est_current=spot_current, spot_est_initial=spot_initial,
        balances=balances, initial_balances=initial_balances, last_price=last_price)


def _ex(name, sent=2, executed=2, rejected=0, sl_tp=0, symbol='EURUSD') -> ExecutionStatsRow:
    return ExecutionStatsRow(
        name=name, symbol=symbol, orders_submitted=sent, orders_executed=executed,
        orders_denied=0, orders_rejected=rejected, orders_cancelled=0, orders_expired=0,
        orders_undelivered=0, orders_unaccounted=0, sl_tp_triggered=sl_tp)


def _pe(name, resolved=2, filled=2, avg=None, mn=None, mx=None, count=0, symbol='EURUSD') -> PendingOrdersUnitRow:
    return PendingOrdersUnitRow(
        name=name, symbol=symbol, total_resolved=resolved, total_filled=filled,
        avg_latency_ms=avg, min_latency_ms=mn, max_latency_ms=mx, latency_count=count)


_ZERO_TOTALS = ExecutionStatsTotals(
    orders_submitted=0, orders_executed=0, orders_rejected=0, sl_tp_triggered=0)


def _build(pf_rows, ex_rows=None, pe_rows=None):
    return build_aggregated_portfolio_report(_RUN_ID, 
        PortfolioReport(run_id=_RUN_ID, units=pf_rows, aggregates=[]),
        ExecutionStatsReport(run_id=_RUN_ID, units=ex_rows or [], totals=_ZERO_TOTALS),
        PendingOrdersReport(run_id=_RUN_ID, units=pe_rows or []))


class TestBuild:
    def test_pure_margin(self):
        rep = _build(
            [_pf('s1', profit=100, loss=40, initial=1000, current=1060),
             _pf('s2', profit=60, loss=20, initial=1000, current=1040)],
            [_ex('s1', sent=3, executed=2, rejected=1), _ex('s2')])
        assert len(rep.currencies) == 1
        cur = rep.currencies[0]
        assert cur.currency == 'USD' and not cur.is_spot and not cur.is_mixed
        assert cur.margin is None and cur.spot is None
        c = cur.combined
        assert c.headline.total_trades == 4 and c.headline.total_profit == 160.0
        assert c.initial_balance == 2000.0 and c.final_balance == 2100.0
        assert c.balance_pnl == 100.0 and round(c.balance_pnl_pct, 2) == 5.0
        assert c.orders_submitted == 5 and c.orders_executed == 4 and c.orders_rejected == 1
        # avg win/loss as amounts; recovery = pnl / |worst-dd|
        assert c.avg_win == 160.0 / 2 and c.avg_loss == 60.0 / 2

    def test_weighted_latency(self):
        # avg = (40*3 + 80*1) / (3+1) = 50
        rep = _build(
            [_pf('s1'), _pf('s2')],
            pe_rows=[_pe('s1', avg=40.0, mn=20.0, mx=60.0, count=3),
                     _pe('s2', avg=80.0, mn=80.0, mx=120.0, count=1)])
        c = rep.currencies[0].combined
        assert c.pending_avg_latency_ms == 50.0
        assert c.pending_min_latency_ms == 20.0 and c.pending_max_latency_ms == 120.0

    def test_pure_spot(self):
        rep = _build([_pf('s1', symbol='BTCUSD', spot=True, last_price=100.0,
                           balances={'USD': 500.0, 'BTC': 2.0},
                           initial_balances={'USD': 1000.0, 'BTC': 0.0})])
        cur = rep.currencies[0]
        assert cur.is_spot and not cur.is_mixed
        c = cur.combined
        assert len(c.spot_scenarios) == 1
        s = c.spot_scenarios[0]
        assert s.base_currency == 'BTC' and s.quote_currency == 'USD'
        assert s.has_base_holdings and s.est_current == 500.0 + 2.0 * 100.0  # 700
        assert c.spot_total_est_current == 700.0 and c.spot_has_base_holdings

    def test_the_spot_split_is_the_stamped_one_not_the_symbol_string(self):
        # `BTCUSDT` split three from the end reads base BTCU, quote SDT — the stamped split
        # from the broker config is the only one (#265).
        row = _pf('s1', symbol='BTCUSD', spot=True, last_price=100.0,
                  balances={'USDT': 500.0, 'BTC': 2.0}, initial_balances={'USDT': 1000.0})
        row = row.model_copy(update={'symbol': 'BTCUSDT', 'base_currency': 'BTC',
                                     'quote_currency': 'USDT', 'spot_est_current': 700.0,
                                     'spot_est_initial': 1000.0})

        s = _build([row]).currencies[0].combined.spot_scenarios[0]

        assert (s.base_currency, s.quote_currency) == ('BTC', 'USDT')
        assert (s.quote_balance, s.base_balance, s.est_current) == (500.0, 2.0, 700.0)

    def test_mixed_currency_split(self):
        rep = _build([
            _pf('m1', symbol='EURUSD', spot=False),
            _pf('sp1', symbol='BTCUSD', spot=True, last_price=100.0,
                balances={'USD': 500.0, 'BTC': 1.0}, initial_balances={'USD': 600.0})])
        cur = rep.currencies[0]
        assert cur.is_mixed and cur.margin is not None and cur.spot is not None
        assert cur.margin.label == 'Margin' and cur.spot.label == 'Spot'
        assert cur.margin.headline.unit_count == 1 and cur.spot.headline.unit_count == 1
        assert len(cur.spot.spot_scenarios) == 1

    def test_two_currencies(self):
        rep = _build([_pf('s1', currency='USD'), _pf('s2', currency='EUR')])
        assert [c.currency for c in rep.currencies] == ['EUR', 'USD']  # sorted

    def test_maker_taker_sum(self):
        # Spot fees split into maker/taker, summed across the currency group (#3).
        rep = _build([_pf('sp1', symbol='BTCUSD', spot=True, maker=1.5, taker=2.5),
                      _pf('sp2', symbol='BTCUSD', spot=True, maker=0.5, taker=1.0)])
        c = rep.currencies[0].combined
        assert c.maker_fee == 2.0 and c.taker_fee == 3.5


class TestRender:
    def test_aggregated_section_renders(self):
        rep = _build([_pf('s1', profit=100, loss=40, maker=1.5, taker=2.5)], [_ex('s1')], [_pe('s1')])
        summary = PortfolioSummary(
            PortfolioReport(run_id=_RUN_ID, units=[], aggregates=[]),
            PendingOrdersReport(run_id=_RUN_ID, units=[]),
            ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=_ZERO_TOTALS),
            rep)
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render_aggregated(ConsoleRenderer())
        out = re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())
        assert 'AGGREGATED PORTFOLIO' in out
        assert 'TRADING SUMMARY' in out and 'ORDER EXECUTION' in out
        assert 'COST BREAKDOWN' in out and 'RISK METRICS' in out
        # Layout A — all five cost categories incl. maker/taker (#3)
        assert 'Maker:' in out and 'Taker:' in out and 'Total Fees:' in out


class TestExecutionRateDerived:
    """The executive used to divide in the printout — the rate is a model figure now (#391)."""

    def test_execution_rate_pct(self):
        report = _build([_pf('s1')], ex_rows=[_ex('s1', sent=8, executed=6)])
        assert report.currencies[0].combined.execution_rate_pct == pytest.approx(75.0)

    def test_zero_orders_submitted_is_zero_not_a_division(self):
        report = _build([_pf('s1')], ex_rows=[_ex('s1', sent=0, executed=0)])
        assert report.currencies[0].combined.execution_rate_pct == 0.0


class TestTheWorstDrawdownIsDescribedByOneScenario:
    """
    Amount, percentage and scenario name on the risk line must come from the SAME row.

    The percentage used to be the worst absolute drawdown divided by the highest peak
    equity in the group — and those two can belong to different scenarios, so the console
    printed one scenario's decline over another's peak, beside a third figure's name. An
    aggregate is not a description: the deepest decline happened somewhere, and the line
    has to say where and how deep it was THERE (#497).
    """

    def test_the_percentage_belongs_to_the_worst_drawdown_not_to_the_highest_peak(self):
        deepest = _pf('deep', max_dd=300.0, max_eq=1_000.0, max_dd_pct=30.0)
        richest = _pf('rich', max_dd=50.0, max_eq=9_000.0, max_dd_pct=0.6)

        row = _build([deepest, richest]).currencies[0].combined

        assert row.account_max_drawdown_scenario == 'deep'
        assert row.highest_equity_scenario == 'rich'
        assert row.account_max_dd_pct == pytest.approx(30.0), (
            'the old construction gave 300 / 9000 = 3.3 % — one scenario\'s decline over '
            'another scenario\'s peak, which describes neither of them')

    def test_the_peak_beside_the_drawdown_is_that_accounts_and_the_highest_is_named_apart(self):
        # Both figures were `max_equity`: the headline carried the deepest account's peak, the
        # rich row the highest of any, and the console printed the second under the first's
        # drawdown. One name, two numbers.
        deepest = _pf('deep', max_dd=300.0, max_eq=1_000.0, max_dd_pct=30.0)
        richest = _pf('rich', max_dd=50.0, max_eq=9_000.0, max_dd_pct=0.6)

        row = _build([deepest, richest]).currencies[0].combined

        assert (row.headline.max_equity, row.headline.account_max_drawdown_unit) == (
            1_000.0, 'deep')
        assert (row.highest_equity, row.highest_equity_scenario) == (9_000.0, 'rich')


class TestSeveralAccountsAddUpOnlyAsATotal:
    """
    A backtest of several scenarios is several independent accounts. Their closing equities
    add up to a total no account ever held, so the total is served as one — beside the
    capital it started from — and the one-account figure has none to describe.
    """

    def test_the_sum_is_a_total_and_final_equity_is_left_undefined(self):
        a = _pf('a', initial=10_000.0).model_copy(update={'final_equity': 9_950.0})
        b = _pf('b', initial=10_000.0).model_copy(update={'final_equity': 10_020.0})

        headline = _build([a, b]).currencies[0].combined.headline

        assert headline.final_equity is None
        assert (headline.total_final_equity, headline.total_initial_balance) == (
            pytest.approx(19_970.0), pytest.approx(20_000.0))

    def test_one_account_is_its_own_total(self):
        a = _pf('a', initial=10_000.0).model_copy(update={'final_equity': 9_950.0})

        headline = _build([a]).currencies[0].combined.headline

        assert headline.final_equity == headline.total_final_equity == pytest.approx(9_950.0)

    def test_the_recovery_factor_is_undefined_over_several_accounts(self):
        # Their summed P&L over one account's decline is a quotient of two populations.
        several = _build([_pf('a', max_dd=10.0), _pf('b', max_dd=20.0)]).currencies[0].combined
        one = _build([_pf('a', max_dd=10.0)]).currencies[0].combined

        assert several.recovery_factor is None
        assert one.recovery_factor == pytest.approx(one.balance_pnl / 10.0)

    def test_a_group_where_no_account_declined_still_names_an_account_and_its_peak(self):
        # Every drawdown 0.0 is a tie: the first account wins it, as in the ledger fold. It used
        # to answer max_equity 0.0 and no account while the unit rows held their real peaks.
        headline = _build([_pf('a', max_dd=0.0, max_eq=10_000.0),
                           _pf('b', max_dd=0.0, max_eq=12_000.0)]).currencies[0].combined.headline

        assert (headline.max_equity, headline.account_max_drawdown_unit) == (10_000.0, 'a')

    def test_a_group_that_never_declined_reports_no_percentage(self):
        row = _build([_pf('a', max_dd=0.0, max_dd_pct=0.0)]).currencies[0].combined

        assert row.account_max_dd_pct == 0.0
