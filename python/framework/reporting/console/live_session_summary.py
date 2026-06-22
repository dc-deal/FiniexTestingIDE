"""
Live session summary (#403 Phase 2) — the AutoTrader closing block.

The live counterpart to the sim Executive Summary: the last section of the unified end-of-run
console. Renders the session-outcome stats (duration · ticks · shutdown/emergency · balance ·
orders · #389 analytics · clipping) and the output-file locations. The emergency cause stays here
prominently (§35); the warnings/errors list itself is the shared `WarningsSummary` section above
(both pipelines). Prints via the shared ConsoleRenderer so it lands in the one captured block.
"""

from pathlib import Path
from typing import Optional

from python.framework.types.api.report_types import TradeHistoryReport
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.utils.console_renderer import ConsoleRenderer


class LiveSessionSummary:
    """The AutoTrader closing block: session stats + output locations."""

    def __init__(
        self,
        result: AutoTraderResult,
        trade_report: Optional[TradeHistoryReport],
        run_dir: Optional[Path],
    ):
        """
        Args:
            result: The completed session result (stats + warning/error buffers)
            trade_report: Unified trade-history report — its #389 analytics line is appended
            run_dir: The session's run directory (output-locations section)
        """
        self._result = result
        self._trade_report = trade_report
        self._run_dir = run_dir

    def render(self, renderer: ConsoleRenderer) -> None:
        """Render the closing block (session stats + output locations)."""
        self._render_stats(renderer)
        self._render_output_locations(renderer)

    def _render_stats(self, renderer: ConsoleRenderer) -> None:
        """Session outcome statistics + the #389 analytics line."""
        result = self._result
        print('=' * 60)
        print('📋 AutoTrader Session Summary')
        print('=' * 60)
        print(f"  Duration:       {result.session_duration_s:.1f}s")
        print(f"  Ticks:          {result.ticks_processed:,}")
        print(f"  Clipped:        {result.ticks_clipped:,}")
        print(f"  Shutdown:       {result.shutdown_mode}")
        if result.shutdown_mode == 'emergency' and result.emergency_reason:
            print(renderer.red(f"  ❌ EMERGENCY CAUSE: {result.emergency_reason}"))

        if result.portfolio_stats:
            pnl = result.portfolio_stats.total_profit - result.portfolio_stats.total_loss
            print(f"  Balance:        {result.portfolio_stats.current_balance:.2f} "
                  f"(P&L: {pnl:+.2f})")

        if result.execution_stats:
            print(f"  Orders:         {result.execution_stats.orders_sent} sent, "
                  f"{result.execution_stats.orders_executed} executed, "
                  f"{result.execution_stats.orders_rejected} rejected")

        # Trade analytics (#389/#393) — model-sourced, one line per account currency.
        for a in (self._trade_report.analytics if self._trade_report else []):
            print(f"  Analytics:      expectancy {a.expectancy:+.3f}R | "
                  f"win-R {a.avg_win_r:+.2f} / loss-R {a.avg_loss_r:+.2f} | "
                  f"R-trades {a.r_trade_count}/{a.trade_count} ({a.currency})")

        clipping = result.clipping_summary
        if clipping.total_ticks > 0:
            print(f"  Clipping ratio: {clipping.clipping_ratio:.1%} "
                  f"(max stale: {clipping.max_stale_ms:.1f}ms, "
                  f"avg proc: {clipping.avg_processing_ms:.2f}ms)")

    def _render_output_locations(self, renderer: ConsoleRenderer) -> None:
        """Output-file locations (log dir + event log)."""
        if self._run_dir is None:
            return
        result = self._result
        print('-' * 60)
        print(f"  Log directory:  {self._run_dir}")
        if result.trade_history or result.order_history:
            trades_n = len(result.trade_history) if result.trade_history else 0
            orders_n = len(result.order_history) if result.order_history else 0
            print(f"  Event log:      events.csv ({trades_n} trades, {orders_n} orders)")
        print('=' * 60)
