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

from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report)
from python.framework.types.api.report_types import (
    ExecutionStatsReport, ExecutionStatsRow, ExecutionStatsTotals, PendingOrdersReport,
    PendingOrdersUnitRow, PortfolioReport, PortfolioUnitRow)
from python.framework.utils.console_renderer import ConsoleRenderer


def _pf(name, currency='USD', symbol='EURUSD', spot=False, trades=2, win=1, lose=1,
        profit=100.0, loss=40.0, max_dd=12.0, max_eq=1000.0, fees=5.0, spread=3.0,
        maker=0.0, taker=0.0, initial=1000.0, current=1060.0, long=1, short=1,
        balances=None, initial_balances=None, last_price=0.0) -> PortfolioUnitRow:
    return PortfolioUnitRow(
        name=name, symbol=symbol, currency=currency, total_trades=trades,
        winning_trades=win, losing_trades=lose, win_rate=(win / trades if trades else 0.0),
        profit_factor=(profit / loss if loss else 0.0), total_profit=profit, total_loss=loss,
        net_profit=profit - loss, max_drawdown=max_dd, total_fees=fees, spot_mode=spot,
        total_long_trades=long, total_short_trades=short, max_equity=max_eq,
        current_balance=current, initial_balance=initial, total_spread_cost=spread,
        maker_fee=maker, taker_fee=taker,
        balances=balances or {}, initial_balances=initial_balances or {}, last_price=last_price)


def _ex(name, sent=2, executed=2, rejected=0, sl_tp=0, symbol='EURUSD') -> ExecutionStatsRow:
    return ExecutionStatsRow(
        name=name, symbol=symbol, orders_sent=sent, orders_executed=executed,
        orders_rejected=rejected, sl_tp_triggered=sl_tp)


def _pe(name, resolved=2, filled=2, avg=None, mn=None, mx=None, count=0, symbol='EURUSD') -> PendingOrdersUnitRow:
    return PendingOrdersUnitRow(
        name=name, symbol=symbol, total_resolved=resolved, total_filled=filled,
        avg_latency_ms=avg, min_latency_ms=mn, max_latency_ms=mx, latency_count=count)


_ZERO_TOTALS = ExecutionStatsTotals(
    orders_sent=0, orders_executed=0, orders_rejected=0, sl_tp_triggered=0)


def _build(pf_rows, ex_rows=None, pe_rows=None):
    return build_aggregated_portfolio_report(
        PortfolioReport(units=pf_rows, aggregates=[]),
        ExecutionStatsReport(units=ex_rows or [], totals=_ZERO_TOTALS),
        PendingOrdersReport(units=pe_rows or []))


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
        assert c.orders_sent == 5 and c.orders_executed == 4 and c.orders_rejected == 1
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
            PortfolioReport(units=[], aggregates=[]),
            PendingOrdersReport(units=[]),
            ExecutionStatsReport(units=[], totals=_ZERO_TOTALS),
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
