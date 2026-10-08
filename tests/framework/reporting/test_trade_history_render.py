"""
Trade-History Console Presenter Tests (#393).

TradeHistorySummary is a thin presenter over the report model: it reads
TradeHistoryReport rows (+ analytics) + OrderHistoryReport, never raw records. Tested
by feeding a hand-built model and asserting the rendered text — the audit table, the
#330 execution sub-lines, the MAE/MFE/R columns, and the #389 analytics block.
"""

import io
from contextlib import redirect_stdout
from typing import Optional

from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.types.api.report_types import (
    ExecutionRow,
    ExecutionStatsReport,
    ExecutionStatsRow,
    ExecutionStatsTotals,
    OrderHistoryReport,
    OrderHistoryRow,
    TradeAnalytics,
    TradeHistoryReport,
    TradeHistoryRow,
    TradeScenarioTotals,
)
from python.framework.types.trading_env_types.order_types import OrderStatus
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


def _row() -> TradeHistoryRow:
    return TradeHistoryRow(
        position_id='p1', symbol='EURUSD', direction='long', lots=0.1,
        entry_price=1.1000, entry_time='2025-10-13T08:00:00+00:00',
        exit_price=1.1020, exit_time='2025-10-13T08:30:00+00:00', duration_s=1800.0,
        close_reason='tp_triggered', gross_pnl=20.8, total_fees=0.8, net_pnl=20.0,
        currency='USD', mae_distance=3.5, mfe_distance=22.0, price_unit='pip', r_multiple=2.0,
        scenario_name='AUDUSD_cont_00', entry_tick_index=10, exit_tick_index=99,
        entry_type='market', entry_side='buy', exit_side='sell',
        entry_executions=[ExecutionRow(
            trade_id='e1', side='buy', volume=0.1, price=1.1000, fee=0.8,
            fee_currency='USD', liquidity='taker', timestamp='')],
        exit_executions=[ExecutionRow(
            trade_id='x1', side='sell', volume=0.1, price=1.1020, fee=0.0,
            fee_currency='USD', liquidity='taker', timestamp='')],
    )


def _report() -> TradeHistoryReport:
    return TradeHistoryReport(run_id=_RUN_ID, 
        trades=[_row()], count=1, symbols=['EURUSD'],
        analytics=[TradeAnalytics(
            currency='USD', trade_count=1,
            expectancy=2.0, avg_win_r=2.0, avg_loss_r=0.0, r_trade_count=1,
            avg_mae_winners=-1.0, avg_mae_losers=0.0, avg_mfe_losers=0.0,
            gross_pnl=20.8, net_pnl=20.0, total_fees=0.8)],
        scenario_totals=[TradeScenarioTotals(
            scenario_name='AUDUSD_cont_00', currency='USD', trade_count=1,
            gross_pnl=20.8, net_pnl=20.0, total_fees=0.8)])


def _empty_orders() -> OrderHistoryReport:
    return OrderHistoryReport(run_id=_RUN_ID, orders=[], count=0, symbols=[])


def _execution(failed: int = 0) -> ExecutionStatsReport:
    """The scenario's execution counts, `failed` of its rows having failed."""
    row = ExecutionStatsRow(
        name='AUDUSD_cont_00', symbol='EURUSD', orders_submitted=failed, orders_adopted=0,
        orders_executed=0, orders_denied=0, orders_rejected=failed, orders_cancelled=0,
        orders_expired=0, orders_undelivered=0, orders_unaccounted=0, sl_tp_triggered=0,
        orders_failed=failed)
    return ExecutionStatsReport(run_id=_RUN_ID, units=[row],
                                totals=ExecutionStatsTotals(orders_failed=failed))


def _rejected_orders(count: int) -> OrderHistoryReport:
    """The order history holding `count` refused orders of the scenario."""
    rows = [OrderHistoryRow(order_id=f'pos_eurusd_{n}', symbol='EURUSD',
                            status=OrderStatus.REJECTED, commission=0.0,
                            scenario_name='AUDUSD_cont_00') for n in range(count)]
    return OrderHistoryReport(run_id=_RUN_ID, orders=rows, count=count, symbols=['EURUSD'])


def _render(method_name: str, orders: Optional[OrderHistoryReport] = None,
            execution: Optional[ExecutionStatsReport] = None) -> str:
    summary = TradeHistorySummary(_report(), orders or _empty_orders(),
                                  execution or _execution())
    buf = io.StringIO()
    with redirect_stdout(buf):
        getattr(summary, method_name)(ConsoleRenderer())
    return buf.getvalue()


def test_per_scenario_table_from_model():
    out = _render('render_per_scenario')
    assert 'AUDUSD_cont_00' in out          # scenario grouping (scenario_name)
    assert 'MAE' in out and 'MFE' in out     # #389 columns
    assert '└─ in' in out and '└─ out' in out  # #330 execution sub-lines
    assert 'e1' in out                       # execution trade_id


def test_aggregated_analytics_from_model():
    out = _render('render_aggregated')
    assert 'TRADE ANALYTICS' in out          # #389 block
    assert 'Expectancy' in out
    assert 'TRADE BREAKDOWN' in out          # derived from rows


class TestTheFailedOrders:
    """How many failed comes from the counts; which ones from the history, which may be capped."""

    def test_a_capped_history_says_how_many_it_lists(self):
        out = _render('render_per_scenario', _rejected_orders(2), _execution(failed=5))

        assert 'Failed Orders: 5 (2 listed)' in out

    def test_a_complete_history_says_nothing_more(self):
        out = _render('render_per_scenario', _rejected_orders(2), _execution(failed=2))

        assert 'Failed Orders: 2' in out and 'listed' not in out

    def test_nothing_failed_prints_nothing(self):
        assert 'Failed Orders' not in _render('render_per_scenario')
