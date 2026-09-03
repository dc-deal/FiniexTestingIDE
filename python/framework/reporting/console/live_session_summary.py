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

from python.framework.reporting.console.feed_stability_summary import format_disturbance_line
from python.framework.types.api.report_types import (
    ColdStartReport,
    RunSummary,
    TradeHistoryReport,
    WarningsErrorsReport,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.utils.console_renderer import ConsoleRenderer


class LiveSessionSummary:
    """The AutoTrader closing block: session stats + output locations."""

    def __init__(
        self,
        result: AutoTraderResult,
        trade_report: Optional[TradeHistoryReport],
        run_dir: Optional[Path],
        run_summary: Optional[RunSummary] = None,
        warnings_errors_report: Optional[WarningsErrorsReport] = None,
        cold_start_report: Optional[ColdStartReport] = None,
    ):
        """
        Args:
            result: The completed session result (stats + warning/error buffers)
            trade_report: Unified trade-history report — its #389 analytics line is appended
            run_dir: The session's run directory (output-locations section)
            run_summary: Cross-section KPI summary — supplies the #451 disturbance line
            warnings_errors_report: Warnings/errors model — supplies the canonical run
                grading (#372), so the outcome is read rather than re-asked of the result
            cold_start_report: What the boot step inherited (#355 / #493) — absent when there
                was nothing to inherit
        """
        self._result = result
        self._trade_report = trade_report
        self._run_dir = run_dir
        self._run_summary = run_summary
        self._warnings_errors_report = warnings_errors_report
        self._cold_start_report = cold_start_report

    def render(self, renderer: ConsoleRenderer) -> None:
        """Render the closing block (session stats + cold start + output locations)."""
        self._render_stats(renderer)
        self._render_cold_start(renderer)
        self._render_output_locations(renderer)

    def _render_cold_start(self, renderer: ConsoleRenderer) -> None:
        """
        What this session INHERITED, when it inherited anything (#355 / #493).

        Rendered rather than computed: every figure here is on the model. It matters for a
        reader because two numbers in the block above mean something different when the
        session started with a position — the entry fee of an inherited position was charged
        to the run before this one, so the trade's net P&L carries it while this run's fee
        total does not.
        """
        report = self._cold_start_report
        if report is None:
            return

        print()
        print('🧬 Cold Start (inherited at boot)')
        if report.adopted:
            print(f'  Orders adopted:  {len(report.adopted)}')
            for row in report.adopted:
                filled = f' ({row.filled_lots} filled)' if row.filled_lots else ''
                print(f'    {row.order_id}  {row.direction} {row.lots} @ {row.price}'
                      f'{filled}  ref={row.broker_ref}')
        if report.restored_positions:
            print(f'  Positions restored: {len(report.restored_positions)} '
                  f'(entry prices remembered, fees charged to the earlier run)')
            for row in report.restored_positions:
                print(f'    {row.position_id}  {row.direction} {row.lots} '
                      f'@ {row.entry_price}  {row.status}')
        if report.book_shortfall:
            print(renderer.red(
                f'  ⚠ Book shortfall: {report.book_shortfall} — the account held less than '
                f'the restored book claimed'))
        if report.skipped:
            reasons = sorted({row.reason for row in report.skipped})
            print(f'  Left alone:      {len(report.skipped)} ({", ".join(reasons)})')
        if report.algo_name and report.algo_accounted_for is not None:
            verdict = 'accounted for' if report.algo_accounted_for else 'not accounted for'
            note = f' — {report.algo_note}' if report.algo_note else ''
            print(f'  {report.algo_name}: {verdict}{note}')

    def _render_stats(self, renderer: ConsoleRenderer) -> None:
        """Session outcome statistics + the #389 analytics line."""
        result = self._result
        print('=' * 60)
        print('📋 AutoTrader Session Summary')
        print('=' * 60)
        print(f'  Duration:       {result.session_duration_s:.1f}s')
        print(f'  Ticks:          {result.ticks_processed:,}')
        print(f'  Clipped:        {result.ticks_clipped:,}')
        # A Ctrl+C also ends as 'emergency' — name it, so a deliberate stop does not
        # read like a crash on the operator's own screen.
        operator_stop = ' (operator stop)' if result.operator_interrupted else ''
        print(f'  Shutdown:       {result.shutdown_mode}{operator_stop}')
        if result.shutdown_mode == 'emergency' and result.emergency_reason:
            print(renderer.red(f'  ❌ EMERGENCY CAUSE: {result.emergency_reason}'))
        outcome = (self._warnings_errors_report.outcome.run_outcome
                   if self._warnings_errors_report else '')
        if outcome == RunOutcome.FINISHED_WITH_ERRORS.value:
            print(renderer.yellow(
                '  ⚠️  FINISHED WITH ERRORS — '
                f'{result.count_logged(LogLevel.ERROR)} error(s) logged during the session'))

        if result.portfolio_stats:
            pnl = result.portfolio_stats.total_profit - result.portfolio_stats.total_loss
            print(f'  Balance:        {result.portfolio_stats.current_balance:.2f} '
                  f'(P&L: {pnl:+.2f})')

        if result.execution_stats:
            print(f'  Orders:         {result.execution_stats.orders_sent} sent, '
                  f'{result.execution_stats.orders_executed} executed, '
                  f'{result.execution_stats.orders_rejected} rejected')

        # Trade analytics (#389/#393) — model-sourced, one line per account currency.
        for a in (self._trade_report.analytics if self._trade_report else []):
            win_r = f'{a.avg_win_r:+.2f}' if a.avg_win_r is not None else 'n/a'
            loss_r = f'{a.avg_loss_r:+.2f}' if a.avg_loss_r is not None else 'n/a'
            print(f'  Analytics:      expectancy {a.expectancy:+.3f}R | '
                  f'win-R {win_r} / loss-R {loss_r} | '
                  f'R-trades {a.r_trade_count}/{a.trade_count} ({a.currency})')

        clipping = result.clipping_summary
        if clipping.total_ticks > 0:
            print(f'  Clipping ratio: {clipping.clipping_ratio:.1%} '
                  f'(max stale: {clipping.max_stale_ms:.1f}ms, '
                  f'avg proc: {clipping.avg_processing_ms:.2f}ms)')

        # Feed disturbance (#451) — a session that ran through an outage must say so here.
        disturbance = (
            format_disturbance_line(self._run_summary) if self._run_summary else '')
        if disturbance:
            print(renderer.yellow(f'  {disturbance}'))

    def _render_output_locations(self, renderer: ConsoleRenderer) -> None:
        """Output-file locations (log dir + event log)."""
        if self._run_dir is None:
            return
        result = self._result
        print('-' * 60)
        print(f'  Log directory:  {self._run_dir}')
        if result.trade_history or result.order_history:
            trades_n = len(result.trade_history) if result.trade_history else 0
            orders_n = len(result.order_history) if result.order_history else 0
            print(f'  Event log:      events.csv ({trades_n} trades, {orders_n} orders)')
        print('=' * 60)
