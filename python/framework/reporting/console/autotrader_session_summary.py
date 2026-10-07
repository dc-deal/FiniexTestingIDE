"""
AutoTrader session summary (#403 Phase 2) — the AutoTrader closing block.

The AutoTrader counterpart to the sim Executive Summary: the last section of the unified end-of-run
console. Renders the session-outcome stats (duration · ticks · shutdown/emergency · balance ·
orders · #389 analytics · clipping) and the output-file locations. The emergency cause stays here
prominently (§35); the warnings/errors list itself is the shared `WarningsSummary` section above
(both pipelines). Prints via the shared ConsoleRenderer so it lands in the one captured block.
"""

from pathlib import Path
from typing import List, Optional

from python.framework.reporting.console.feed_stability_summary import format_disturbance_line
from python.framework.reporting.console.order_counts_line import order_endings_text
from python.framework.types.api.report_types import (
    ColdStartReport,
    ReconcileDivergenceRow,
    RunSummary,
    SafetyReport,
    TradeHistoryReport,
    VenueAccountReport,
    VenueSnapshotRow,
    WarningsErrorsReport,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.live_types.broker_truth_types import BrokerTruthPart
from python.framework.types.live_types.reconciliation_types import ReconcileState
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.utils.console_renderer import ConsoleRenderer


class AutotraderSessionSummary:
    """The AutoTrader closing block: session stats + output locations."""

    def __init__(
        self,
        result: AutoTraderResult,
        trade_report: Optional[TradeHistoryReport],
        run_dir: Optional[Path],
        run_summary: Optional[RunSummary] = None,
        warnings_errors_report: Optional[WarningsErrorsReport] = None,
        cold_start_report: Optional[ColdStartReport] = None,
        safety_report: Optional[SafetyReport] = None,
        venue_account_report: Optional[VenueAccountReport] = None,
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
            safety_report: The risk denominator this session ran against and how far the
                account moved (#356 / #314) — absent when no baseline was ever taken
            venue_account_report: What the venue held at the start and the end, and what the
                reconciliation recorded between (#362) — absent when the session asked its
                venue nothing
        """
        self._result = result
        self._trade_report = trade_report
        self._run_dir = run_dir
        self._run_summary = run_summary
        self._warnings_errors_report = warnings_errors_report
        self._cold_start_report = cold_start_report
        self._safety_report = safety_report
        self._venue_account_report = venue_account_report

    def render(self, renderer: ConsoleRenderer) -> None:
        """Render the closing block (session stats + cold start + safety + venue + locations)."""
        self._render_stats(renderer)
        self._render_cold_start(renderer)
        self._render_safety(renderer)
        self._render_venue_account(renderer)
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
        if report.applied:
            print('🧬 Cold Start (inherited at boot)')
        else:
            # The boot refused, so nothing below was applied. Saying "adopted" here would
            # describe a session that never traded as one that inherited a book.
            print(renderer.red('🧬 Cold Start — NOT APPLIED (the boot refused to start)'))
        verb = 'adopted' if report.applied else 'would have been adopted'
        if report.adopted:
            print(f'  Orders {verb}:  {len(report.adopted)}')
            for row in report.adopted:
                filled = f' ({row.filled_lots} filled)' if row.filled_lots else ''
                print(f'    {row.order_id}  {row.direction} {row.lots} @ {row.price}'
                      f'{filled}  ref={row.broker_ref}')
        if report.restored_positions:
            restored = 'restored' if report.applied else 'would have been restored'
            print(f'  Positions {restored}: {len(report.restored_positions)} '
                  f'(entry prices remembered, fees charged to the earlier run)')
            for row in report.restored_positions:
                print(f'    {row.position_id}  {row.direction} {row.lots} '
                      f'@ {row.entry_price}  {row.status}')
        if report.book_shortfall:
            print(renderer.red(
                f'  ⚠ Book shortfall: {report.book_shortfall} — the account held less than '
                f'the restored book claimed'))
        if report.skipped:
            print(f'  Left alone:      {len(report.skipped)} '
                  f'({", ".join(report.skipped_reasons)})')
        if report.decision_logic_class and report.decision_logic_accounted_for is not None:
            verdict = ('accounted for' if report.decision_logic_accounted_for
                       else 'not accounted for')
            note = f' — {report.decision_logic_note}' if report.decision_logic_note else ''
            print(f'  {report.decision_logic_class}: {verdict}{note}')

    def _render_safety(self, renderer: ConsoleRenderer) -> None:
        """
        The risk denominator and how far the account moved against it (#356 / #314).

        Every figure here names its baseline, which is the whole reason the section exists: a
        bare "-12 %" cannot be traced to the number that produced it, and four quantities in
        this codebase are called "initial". Nothing is computed — the extremes are running
        maxima captured by the loop and the worst day was chosen in the builder, because a
        renderer that picks a maximum out of thirty rows has built its own aggregate and the
        API will then answer the same question differently.
        """
        report = self._safety_report
        # The coordinator only builds this report once a baseline exists, and the block is
        # written entirely against that record. The second half of the guard is therefore
        # belt and braces on purpose: this is the closing block of an AutoTrader session, and a
        # renderer crashing here would take the whole session summary with it.
        if report is None or report.baseline is None:
            return

        print()
        armed = '' if report.enabled else ' — LIMITS OFF, measured only'
        print(f'🛡️ Safety (risk baseline){armed}')

        baseline = report.baseline
        if report.baseline_restored:
            # The origin token already says `restored_carry_over`, so printing both would be
            # the same fact twice. This is the case #356 exists for, and it gets the words.
            print(f'  Baseline:       {baseline.kind.value} = {report.baseline_value:.2f} '
                  f'(taken {baseline.taken_at_utc})')
            print('                  RESTORED from the previous session — the drawdown '
                  'continues from there rather than starting over')
        else:
            print(f'  Baseline:       {baseline.kind.value} = {report.baseline_value:.2f} '
                  f'({baseline.origin.value}, taken {baseline.taken_at_utc})')

        used = ('' if report.soft_limit_used_pct is None
                else f' — {report.soft_limit_used_pct:.0f}% of the soft limit')
        when = f' at {report.worst_drawdown_pct_at}' if report.worst_drawdown_pct_at else ''
        print(f'  Worst drawdown: {-report.worst_drawdown_abs:.2f} '
              f'({-report.worst_drawdown_pct:.2f}%){when}{used}')
        # Two extremes, two moments — only ever printed when they ARE two, which on the
        # default fixed baseline is never. A high-water mark moves, and then the deepest
        # amount and the deepest share are different ticks.
        if report.worst_drawdown_abs_at != report.worst_drawdown_pct_at:
            print(f'    (deepest amount {-report.worst_drawdown_abs:.2f} at '
                  f'{report.worst_drawdown_abs_at} — the baseline moved between the two)')

        engaged = (f'engaged {report.block_count}x' if report.block_count
                   else 'never engaged')
        state = (renderer.red(f'STILL BLOCKED: {report.reason_at_end}')
                 if report.blocked_at_end else 'clear at session end')
        print(f'  Entry block:    {engaged}, {state}')

        if report.days:
            hit = (f', {report.days_limit_hit} over a daily limit'
                   if report.days_limit_hit else '')
            print(f'  Daily:          {len(report.days)} day(s), worst {report.worst_day} '
                  f'{-report.worst_day_loss_abs:.2f} '
                  f'({-report.worst_day_loss_pct:.2f}% of that day){hit}')
            for row in report.days:
                if row.limit_hit:
                    print(renderer.yellow(
                        f'    {row.day}: daily limit hit — {-row.worst_loss_abs:.2f} '
                        f'({-row.worst_loss_pct:.2f}% of {row.baseline_value:.2f})'))

        if report.flatten_fired:
            print(renderer.red(f'  🚨 HARD STOP fired: {report.flatten_reason}'))
            if report.flatten_completed:
                print('     the book was confirmed flat before the session ended')
            else:
                still = ', '.join(report.flatten_unconfirmed) or 'none reported'
                print(renderer.red(
                    f'     NOT confirmed flat — still open at the venue: {still}. '
                    f'Check the account by hand.'))

    def _render_venue_account(self, renderer: ConsoleRenderer) -> None:
        """
        What the venue held when the session started and when it ended, and what the
        reconciliation recorded between (#362).

        The venue's own account beside the session's — the one view in this block that does not
        come from the session's books. Every count is on the model; a part the venue did not
        give is printed as unread, never as nothing, because an empty answer says the venue
        holds nothing.
        """
        report = self._venue_account_report
        if report is None:
            return

        for row in report.units:
            print()
            print('🏦 Venue (broker truth)')
            print(f'  At start:       {_snapshot_text(row.at_start)}')
            print(f'  At end:         {_snapshot_text(row.at_end)}')
            if not row.reconcile_lines:
                print('  Reconciliation: no divergence recorded')
                continue
            lines = 'line' if row.reconcile_lines == 1 else 'lines'
            last = ('the last clean' if row.last_reconcile_state is ReconcileState.CLEAN
                    else renderer.yellow('the last divergent'))
            print(f'  Reconciliation: {row.reconcile_lines} {lines} written, '
                  f'{row.divergent_lines} divergent — {last}')
            if row.last_divergence is not None:
                print(f'                  last divergence: '
                      f'{_divergence_text(row.last_divergence)}')

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
                   if self._warnings_errors_report else None)
        if outcome is RunOutcome.FINISHED_WITH_ERRORS:
            print(renderer.yellow(
                '  ⚠️  FINISHED WITH ERRORS — '
                f'{result.count_logged(LogLevel.ERROR)} error(s) logged during the session'))

        if result.portfolio_stats:
            pnl = result.portfolio_stats.total_profit - result.portfolio_stats.total_loss
            print(f'  Balance:        {result.portfolio_stats.current_balance:.2f} '
                  f'(P&L: {pnl:+.2f} realised)')
            # #492: a session may END holding something. The headline is what an operator
            # reads first, so a position left standing by policy has to appear HERE and not
            # only in the portfolio section further down — otherwise the one figure they
            # look at describes a flat account that is not flat.
            if result.open_positions:
                print(f'  Still open:     {len(result.open_positions)} position(s) '
                      f'({result.portfolio_stats.unrealized_pnl:+.2f} unrealised)'
                      + (f' · policy {result.session_end_policy}'
                         if result.session_end_policy else ''))

        if result.execution_stats:
            stats = result.execution_stats
            endings = order_endings_text(stats, renderer)
            print(f'  Orders:         {stats.orders_submitted} submitted · '
                  + (f'{stats.orders_adopted} adopted · ' if stats.orders_adopted else '')
                  + f'{stats.orders_executed} executed'
                  + (f' · {endings}' if endings else ''))

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


def _snapshot_text(snapshot: Optional[VenueSnapshotRow]) -> str:
    """
    One venue read as a console line.

    Args:
        snapshot: The read, None when it was not recorded

    Returns:
        Open orders, positions where read, and the balances, each as the venue gave it
    """
    if snapshot is None:
        return 'not recorded'
    parts: List[str] = []
    if snapshot.venue_order_count is not None:
        orders = 'open order' if snapshot.venue_order_count == 1 else 'open orders'
        parts.append(f'{snapshot.venue_order_count} {orders}')
    elif BrokerTruthPart.VENUE_ORDERS in snapshot.unread_parts:
        parts.append('open orders unread')
    if snapshot.venue_position_count is not None:
        positions = 'position' if snapshot.venue_position_count == 1 else 'positions'
        parts.append(f'{snapshot.venue_position_count} {positions}')
    elif BrokerTruthPart.VENUE_POSITIONS in snapshot.unread_parts:
        parts.append('positions unread')
    if snapshot.venue_balances is not None:
        parts.extend([f'{asset} {amount:.10g}' for asset, amount in snapshot.venue_balances.items()]
                     or ['no balances'])
    elif BrokerTruthPart.VENUE_BALANCES in snapshot.unread_parts:
        parts.append('balances unread')
    return ' · '.join(parts)


def _divergence_text(divergence: ReconcileDivergenceRow) -> str:
    """
    A divergent picture as a console line — each non-empty bucket by its served name.

    Args:
        divergence: The picture

    Returns:
        The buckets that hold something, members by identity, positions as counts
    """
    parts: List[str] = []
    for name, value in divergence.model_dump().items():
        if isinstance(value, list) and value:
            parts.append(f'{name.replace("_", " ")}: {", ".join(value)}')
        elif isinstance(value, int) and value:
            parts.append(f'{name.replace("_", " ")}: {value}')
    return '; '.join(parts)
