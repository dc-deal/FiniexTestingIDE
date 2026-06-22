"""
Run console renderer tests (#403 Phase 2).

`RunConsoleRenderer` owns the canonical end-of-run section order for both pipelines. The two
behaviours worth pinning are (1) the per-currency AGGREGATE blocks render only for a multi-unit
run (`unit_count > 1`) — redundant for a single-scenario sim run and the always-single live
session — and (2) a `None` section slot is skipped (render-if-present, so live omits the sim-only
sections). A recording spy stands in for the sub-presenters here: this exercises the renderer's
ORCHESTRATION (which method it calls when), not the presenters' data mapping.
"""

from python.framework.reporting.console.run_console_renderer import RunConsoleRenderer


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
        assert trade.calls == ['per_scenario']  # aggregated gated off (unit_count=1)
