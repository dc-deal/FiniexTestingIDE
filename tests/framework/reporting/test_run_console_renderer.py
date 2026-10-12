"""
Run console renderer tests (#403 Phase 2).

`RunConsoleRenderer` owns the canonical end-of-run section order for both pipelines. The two
behaviours worth pinning are (1) the per-currency AGGREGATE blocks render only for a multi-unit
run (`unit_count > 1`) — redundant for a single-scenario sim run and the always-single live
session — and (2) a `None` section slot is skipped (render-if-present, so live omits the sim-only
sections). A recording spy stands in for the sub-presenters here: this exercises the renderer's
ORCHESTRATION (which method it calls when), not the presenters' data mapping — except for the
slippage block, whose once-and-only-once is checked against the real trade presenter too.
"""

import io
from contextlib import redirect_stdout
from typing import List

from python.framework.reporting.console.run_console_renderer import RunConsoleRenderer
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsTotals,
    OrderHistoryReport,
    TradeAnalytics,
    TradeHistoryReport,
    TradeHistoryRow,
    TradeScenarioTotals,
)
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20261011_120000_a1b2c3d4'
_SLIPPAGE_HEADING = 'SLIPPAGE (submission tick vs fill)'


class _SpyRenderer:
    """A no-op ConsoleRenderer stand-in (the renderer calls print_separator for the footer)."""

    def print_separator(self, *args, **kwargs):
        pass


class _SpyPresenter:
    """Records which render methods the renderer invokes (flexible signatures)."""

    def __init__(self):
        self.calls = []

    def render(self, *args, **kwargs):
        self.calls.append('render')

    def render_per_scenario(self, *args, **kwargs):
        self.calls.append('per_scenario')

    def render_aggregated(self, *args, **kwargs):
        self.calls.append('aggregated')

    def render_bottleneck_analysis(self, *args, **kwargs):
        self.calls.append('bottleneck')

    def render_warmup(self, *args, **kwargs):
        self.calls.append('warmup')

    def render_slippage(self, *args, **kwargs):
        self.calls.append('slippage')


def _render(unit_count: int, summary_detail: bool):
    """Drive a renderer with spy portfolio/trade/performance slots; return their call lists."""
    portfolio, trade, performance = _SpyPresenter(), _SpyPresenter(), _SpyPresenter()
    RunConsoleRenderer(
        unit_count=unit_count, threshold=9,
        portfolio_summary=portfolio, trade_history_summary=trade,
        performance_summary=performance,
    ).render_all(renderer=_SpyRenderer(), summary_detail=summary_detail)
    return portfolio, trade, performance


class TestAggregateGating:
    """The AGGREGATE blocks are gated on unit_count > 1."""

    def test_single_unit_skips_aggregated_and_bottleneck(self):
        portfolio, trade, performance = _render(unit_count=1, summary_detail=True)
        assert 'aggregated' not in portfolio.calls
        assert 'aggregated' not in trade.calls
        assert 'aggregated' not in performance.calls
        # The "worst across scenarios" bottleneck analysis is cross-unit too → gone for 1 unit.
        assert 'bottleneck' not in performance.calls

    def test_multi_unit_renders_aggregated_and_bottleneck(self):
        portfolio, trade, performance = _render(unit_count=3, summary_detail=True)
        assert 'aggregated' in portfolio.calls
        assert 'aggregated' in trade.calls
        assert 'aggregated' in performance.calls
        assert 'bottleneck' in performance.calls

    def test_per_scenario_follows_summary_detail(self):
        portfolio, _, _ = _render(unit_count=3, summary_detail=False)
        assert 'per_scenario' not in portfolio.calls
        portfolio, _, _ = _render(unit_count=3, summary_detail=True)
        assert 'per_scenario' in portfolio.calls


class TestRenderIfPresent:
    """A None slot is skipped — the renderer never touches absent sections."""

    def test_none_slots_are_skipped(self):
        # Only a trade slot is provided; the renderer must not raise on the (live) None slots.
        trade = _SpyPresenter()
        RunConsoleRenderer(
            unit_count=1, threshold=9, trade_history_summary=trade,
        ).render_all(renderer=_SpyRenderer(), summary_detail=True)
        # aggregated gated off (unit_count=1); the slippage block follows the unit's table
        assert trade.calls == ['per_scenario', 'slippage']


def _trade(scenario_name: str) -> TradeHistoryRow:
    """
    One closed trade whose entry slipped against its submission mid.

    Args:
        scenario_name: The unit it belongs to

    Returns:
        The trade row
    """
    return TradeHistoryRow(
        position_id=f'pos_{scenario_name}', symbol='ETHUSD', direction='long', lots=0.01,
        entry_price=2500.0, entry_time='2026-10-11T08:00:00+00:00',
        exit_price=2510.0, exit_time='2026-10-11T08:30:00+00:00', duration_s=1800.0,
        close_reason='', gross_pnl=0.1, total_fees=0.05, net_pnl=0.05, currency='USD',
        scenario_name=scenario_name, entry_tick_index=1, exit_tick_index=5,
        entry_slippage=0.25, entry_slippage_pct=0.01)


def _trade_console(unit_names: List[str]) -> str:
    """
    Render a run of the given units, with their detail, through the real trade presenter.

    Args:
        unit_names: One unit per name, one trade each, all in USD

    Returns:
        The printed text
    """
    report = TradeHistoryReport(
        run_id=_RUN_ID, trades=[_trade(name) for name in unit_names], count=len(unit_names),
        symbols=['ETHUSD'],
        analytics=[TradeAnalytics(
            currency='USD', trade_count=len(unit_names), expectancy=0.0, avg_win_r=None,
            avg_loss_r=None, r_trade_count=0, avg_mae_winners=0.0, avg_mae_losers=0.0,
            avg_mfe_losers=0.0)],
        scenario_totals=[TradeScenarioTotals(
            scenario_name=name, currency='USD', trade_count=1, gross_pnl=0.1, net_pnl=0.05,
            total_fees=0.05) for name in unit_names])
    summary = TradeHistorySummary(
        report, OrderHistoryReport(run_id=_RUN_ID, orders=[], count=0, symbols=[]),
        ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()))
    buf = io.StringIO()
    with redirect_stdout(buf):
        RunConsoleRenderer(
            unit_count=len(unit_names), threshold=9, trade_history_summary=summary,
        ).render_all(renderer=ConsoleRenderer(), summary_detail=True)
    return buf.getvalue()


class TestTheSlippageBlockIsPrintedOnce:
    """
    The submission-vs-fill slippage block sits in the aggregated trade statistics, which a single
    unit skips as redundant with its own table. An AutoTrader session is always one unit, so the
    block follows a single unit's table instead, and a run of several prints it once, in the
    aggregate.
    """

    def test_a_single_unit_prints_it_after_its_table(self):
        _, trade, _ = _render(unit_count=1, summary_detail=True)
        assert trade.calls == ['per_scenario', 'slippage']

    def test_several_units_print_it_only_in_the_aggregate(self):
        _, trade, _ = _render(unit_count=3, summary_detail=True)
        assert 'slippage' not in trade.calls
        assert 'aggregated' in trade.calls

    def test_a_single_unit_without_its_table_prints_none(self):
        _, trade, _ = _render(unit_count=1, summary_detail=False)
        assert trade.calls == []

    def test_the_real_presenter_prints_it_once_for_one_unit(self):
        assert _trade_console(['ethusd_session']).count(_SLIPPAGE_HEADING) == 1

    def test_the_real_presenter_prints_it_once_for_several_units(self):
        assert _trade_console(['s1', 's2']).count(_SLIPPAGE_HEADING) == 1
