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
    FillSlippageSummary,
    OrderHistoryReport,
    OrderHistoryRow,
    TradeAnalytics,
    TradeHistoryReport,
    TradeHistoryRow,
    TradeScenarioTotals,
)
from python.framework.types.fill_slippage_types import SlippageReference
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


def _execution(failed: int = 0, slippage=()) -> ExecutionStatsReport:
    """The scenario's execution counts, `failed` of its rows having failed, and the fills'
    slippage per reference kind as the statistics serve it."""
    row = ExecutionStatsRow(
        name='AUDUSD_cont_00', symbol='EURUSD', orders_submitted=failed, orders_adopted=0,
        orders_executed=0, orders_denied=0, orders_rejected=failed, orders_cancelled=0,
        orders_expired=0, orders_undelivered=0, orders_unaccounted=0, sl_tp_triggered=0,
        orders_failed=failed)
    return ExecutionStatsReport(run_id=_RUN_ID, units=[row],
                                totals=ExecutionStatsTotals(orders_failed=failed),
                                fill_slippage_totals=list(slippage))


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


def test_an_expectancy_nobody_measured_prints_no_number(monkeypatch):
    report = _report()
    report.analytics[0].expectancy = None
    report.analytics[0].r_trade_count = 0
    monkeypatch.setattr('tests.framework.reporting.test_trade_history_render._report', lambda: report)

    out = _render('render_aggregated')

    assert 'Expectancy: n/a' in out


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


class TestTheSlippageBlockOnItsOwn:
    """
    `render_slippage` prints the fills' slippage without the aggregated statistics it otherwise
    sits in — what a single unit gets after its table. It renders what the execution statistics
    serve and computes nothing (#631).
    """

    def _render_slippage(self, totals) -> str:
        """
        Render the block alone.

        Args:
            totals: The execution statistics' fill_slippage_totals

        Returns:
            The printed text
        """
        summary = TradeHistorySummary(_report(), _empty_orders(), _execution(slippage=totals))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render_slippage(ConsoleRenderer())
        return buf.getvalue()

    def test_each_reference_kind_has_its_own_line_as_served(self):
        out = self._render_slippage([
            FillSlippageSummary(measured_against=SlippageReference.SUBMISSION_MID, fills=212,
                                fills_measured=212, adverse_pct_mean=0.0041,
                                adverse_pct_max=0.0612),
            FillSlippageSummary(measured_against=SlippageReference.PROTECTIVE_LEVEL, fills=9,
                                fills_measured=9, adverse_pct_mean=0.0187, adverse_pct_max=0.073),
        ])

        assert 'SLIPPAGE (each fill against the price its order named)' in out
        assert 'submission_mid    212 fills (212 measured)   mean +0.0041%   max +0.0612%' in out
        assert 'protective_level  9 fills (9 measured)   mean +0.0187%   max +0.0730%' in out
        assert 'TRADE BREAKDOWN' not in out          # the aggregated statistics stay out

    def test_a_kind_whose_reference_was_never_captured_says_so(self):
        out = self._render_slippage([FillSlippageSummary(
            measured_against=SlippageReference.SUBMISSION_MID, fills=2, fills_measured=0)])

        assert '2 fills (0 measured)   not measured — the reference was not captured' in out

    def test_nothing_recorded_prints_nothing(self):
        assert self._render_slippage([]) == ''
