"""
FiniexTestingIDE - Trade History Summary
Detailed trade-by-trade history rendering for P&L verification

Renders (purely from the unified report model, #393):
- Per-scenario trade tables (chronological by entry_tick_index) + #330 execution sub-lines
- Entry/exit prices, SL/TP, fees, P&L, MAE/MFE/R (#389) columns
- Aggregated trade statistics + the #389 analytics block + rejection breakdown
"""

from typing import Dict, List

from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import (
    ExecutionRow, OrderHistoryReport, OrderHistoryRow, TradeAnalytics, TradeHistoryReport,
    TradeHistoryRow, TradeScenarioTotals)
from python.framework.utils.console_renderer import ConsoleRenderer


# EntryType.value → compact table glyph
_ENTRY_TYPE_GLYPH = {'market': 'M', 'limit': 'L', 'stop': 'S', 'stop_limit': 'SL'}


class TradeHistorySummary(AbstractBatchSummarySection):
    """
    Trade history summary renderer — a thin presenter over the report model (#393).

    Groups the report's rows by their run unit (`scenario_name`), renders each table
    chronologically, and derives nothing itself: every number comes from the model
    (`TradeHistoryReport` rows + `TradeAnalytics`); rejections from `OrderHistoryReport`.
    """

    _section_title = '📋 TRADE HISTORY (PER SCENARIO)'

    def __init__(self, report: TradeHistoryReport, order_report: OrderHistoryReport) -> None:
        """
        Initialize trade history summary.

        Args:
            report: The unified trade-history report (rows + analytics)
            order_report: The unified order-history report (rejection source)
        """
        self._report = report
        self._order_report = order_report
        # Per-scenario footer totals from the model (no renderer re-sum)
        self._scenario_totals_by_name = {
            t.scenario_name: t for t in report.scenario_totals}

    def _grouped(self) -> Dict[str, List[TradeHistoryRow]]:
        """Group rows by scenario_name, preserving first-appearance order."""
        groups: Dict[str, List[TradeHistoryRow]] = {}
        for row in self._report.trades:
            groups.setdefault(row.scenario_name, []).append(row)
        return groups

    def render_per_scenario(self, renderer: ConsoleRenderer) -> None:
        """
        Render trade history per scenario.

        Args:
            renderer: ConsoleRenderer instance
        """
        self._render_section_header(renderer)

        if not self._report.trades:
            print("No trade history available")
            return

        for idx, (scenario_name, rows) in enumerate(self._grouped().items(), 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()
            self._render_scenario_trades(scenario_name, rows, renderer)

    def render_aggregated(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregated trade statistics + the #389 analytics block.

        Args:
            renderer: ConsoleRenderer instance
        """
        rows = self._report.trades
        if not rows:
            return

        print()
        renderer.section_separator()
        renderer.print_bold("📊 AGGREGATED TRADE STATISTICS")
        renderer.section_separator()

        # Group by currency — no cross-currency P&L mixing (like the portfolio section).
        groups: Dict[str, List[TradeHistoryRow]] = {}
        for row in rows:
            groups.setdefault(row.currency, []).append(row)
        analytics_by_ccy = {a.currency: a for a in self._report.analytics}
        multi = len(groups) > 1

        for currency in sorted(groups):
            group_rows = groups[currency]
            if multi:
                print(f"\n{renderer.bold(f'   💰 {currency} GROUP ({len(group_rows)} trades)')}")
            self._render_aggregated_stats(
                group_rows, analytics_by_ccy.get(currency), renderer)
            if multi:
                renderer.print_separator(width=120, char="·")

        # Rejections are currency-agnostic — render once.
        rejections = [o for o in self._order_report.orders if o.status == 'rejected']
        if rejections:
            self._render_aggregated_rejections(rejections, renderer)
        print()

    def _render_scenario_trades(
        self,
        scenario_name: str,
        rows: List[TradeHistoryRow],
        renderer: ConsoleRenderer
    ) -> None:
        """Render trade history for a single scenario (already its rows)."""
        # Sort by entry_tick_index (chronological) — ordering is a presentation concern
        sorted_rows = sorted(rows, key=lambda r: r.entry_tick_index)

        # Per-scenario totals from the model aggregate (not a re-sum)
        totals = self._scenario_totals_by_name[scenario_name]
        pnl_str = self._format_pnl(totals.net_pnl, totals.currency, renderer)
        print(
            f"📋 {renderer.bold(scenario_name)}: {totals.trade_count} trades | "
            f"Total P&L: {pnl_str}")
        print()

        # Pre-compute entry execution trade_id frequency for the shared(Nx) annotation
        # (#330). A partially-closed position's derived rows carry the SAME entry
        # executions — the renderer flags the duplicates.
        shared_counts: Dict[str, int] = {}
        for r in sorted_rows:
            for ex in r.entry_executions:
                shared_counts[ex.trade_id] = shared_counts.get(ex.trade_id, 0) + 1

        self._print_table_header(renderer)
        for idx, row in enumerate(sorted_rows, 1):
            self._print_trade_row(idx, row, renderer, shared_counts)
        self._print_table_footer(totals, renderer)

        self._render_scenario_rejections(scenario_name, renderer)

    def _render_scenario_rejections(
        self, scenario_name: str, renderer: ConsoleRenderer) -> None:
        """Render rejected orders for a scenario (from the order-history model)."""
        rejections = [
            o for o in self._order_report.orders
            if o.status == 'rejected' and o.scenario_name == scenario_name
        ]
        if not rejections:
            return

        print()
        print(renderer.yellow(f"   Rejected Orders: {len(rejections)}"))
        header = f"   {'#':>3} | {'Order ID':<20} | {'Reason':<25} | {'Message'}"
        print(renderer.gray(header))
        print(renderer.gray("   " + "-" * 100))
        for idx, rej in enumerate(rejections, 1):
            reason_str = rej.rejection_reason or "unknown"
            row = f"   {idx:>3} | {rej.order_id:<20} | {reason_str:<25} | {rej.rejection_message}"
            print(renderer.yellow(row))

    def _print_table_header(self, renderer: ConsoleRenderer) -> None:
        """Print trade table header."""
        header = (
            f"   {'#':>3} | {'Side':^5} | {'ET':>2} | {'Lots':>5} | "
            f"{'Entry Price':>12} | {'Exit Price':>12} | "
            f"{'SL':>12} | {'TP':>12} | "
            f"{'Entry Tick':>10} | {'Exit Tick':>10} | {'Duration':>8} | "
            f"{'Gross P&L':>10} | {'Fees':>8} | {'Net P&L':>10} | "
            f"{'MAE':>7} | {'MFE':>7} | {'R':>6} | {'Close Reason':>14}"
        )
        print(renderer.gray(header))
        print(renderer.gray("   " + "-" * 200))

    def _print_trade_row(
        self,
        idx: int,
        row: TradeHistoryRow,
        renderer: ConsoleRenderer,
        shared_counts: Dict[str, int]
    ) -> None:
        """Print single trade row plus per-execution sub-lines (#330)."""
        # Trade-event side (the close operation), colored by position direction.
        side_text = (row.exit_side.upper().ljust(5) if row.exit_side
                     else ('LONG ' if row.direction == 'long' else 'SHORT'))
        dir_str = renderer.green(side_text) if row.direction == 'long' else renderer.red(side_text)

        entry_type_str = _ENTRY_TYPE_GLYPH.get(row.entry_type, "?")

        sl_str = f"{row.stop_loss:>12.5f}" if row.stop_loss is not None else f"{'—':>12}"
        tp_str = f"{row.take_profit:>12.5f}" if row.take_profit is not None else f"{'—':>12}"
        duration = row.exit_tick_index - row.entry_tick_index

        gross_str = self._format_value(row.gross_pnl, renderer)
        net_str = self._format_value(row.net_pnl, renderer)
        r_str = f"{row.r_multiple:>6.2f}" if row.r_multiple is not None else f"{'—':>6}"
        reason_str = row.close_reason  # '' for manual

        print(
            f"   {idx:>3} | {dir_str} | {entry_type_str:>2} | {row.lots:>5.2f} | "
            f"{row.entry_price:>12.5f} | {row.exit_price:>12.5f} | "
            f"{sl_str} | {tp_str} | "
            f"{row.entry_tick_index:>10} | {row.exit_tick_index:>10} | {duration:>8} | "
            f"{gross_str:>10} | {row.total_fees:>8.2f} | {net_str:>10} | "
            f"{row.mae_pips:>7.1f} | {row.mfe_pips:>7.1f} | {r_str} | {reason_str:>14}"
        )

        # Per-execution sub-lines (#330) — from the model's ExecutionRows.
        for ex in row.entry_executions:
            self._render_subline('in', ex, shared_counts.get(ex.trade_id, 1), renderer, row.lots)
        for ex in row.exit_executions:
            self._render_subline('out', ex, 1, renderer)

    def _render_subline(
        self,
        side: str,
        execution: ExecutionRow,
        shared_count: int,
        renderer: ConsoleRenderer,
        trade_row_lots: float = None,
    ) -> None:
        """Render one per-execution sub-line under an aggregate trade row (#330)."""
        side_label = 'in ' if side == 'in' else 'out'
        suffix = ""
        if shared_count > 1:
            suffix = f"  shared({shared_count}x)"
            if trade_row_lots is not None and trade_row_lots != execution.volume:
                suffix += f"  (this trade: {trade_row_lots:g} of {execution.volume:g})"

        text = (
            f"     └─ {side_label}  {execution.trade_id:<24}  "
            f"vol {execution.volume:>7.5f}  "
            f"price {execution.price:>12.5f}  "
            f"fee {execution.fee:>7.4f}  "
            f"{execution.liquidity}"
            f"{suffix}"
        )
        print(renderer.gray(text))

    def _print_table_footer(
        self, totals: TradeScenarioTotals, renderer: ConsoleRenderer) -> None:
        """Print trade table footer with the per-scenario totals (from the model aggregate)."""
        print(renderer.gray("   " + "-" * 200))
        gross_str = self._format_value(totals.gross_pnl, renderer)
        net_str = self._format_value(totals.net_pnl, renderer)
        total_row = (
            f"   {'':>3} | {'TOTAL':^5} | {' ':1} | {'':>5} | "
            f"{'':>12} | {'':>12} | {'':>12} | {'':>12} | "
            f"{'':>10} | {'':>10} | {'':>8} | "
            f"{gross_str:>10} | {totals.total_fees:>8.2f} | {net_str:>10} | "
            f"{'':>7} | {'':>7} | {'':>6} | {totals.currency} |"
        )
        print(renderer.bold(total_row))

    def _render_aggregated_stats(
        self,
        rows: List[TradeHistoryRow],
        analytics: TradeAnalytics,
        renderer: ConsoleRenderer
    ) -> None:
        """Render aggregated statistics for one currency group (all from the model rows)."""
        total_trades = len(rows)
        long_trades = sum(1 for r in rows if r.direction == 'long')
        short_trades = total_trades - long_trades

        winning_trades = [r for r in rows if r.net_pnl > 0]
        losing_trades = [r for r in rows if r.net_pnl <= 0]

        # P&L totals come straight from the model aggregate — analytics is always present here
        # (one per currency; render_aggregated returns early when there are no trades).
        total_gross = analytics.gross_pnl
        total_fees = analytics.total_fees
        total_net = analytics.net_pnl
        avg_gross = total_gross / total_trades if total_trades else 0
        avg_fees = total_fees / total_trades if total_trades else 0
        avg_net = total_net / total_trades if total_trades else 0

        durations = [r.exit_tick_index - r.entry_tick_index for r in rows]
        avg_duration = sum(durations) / len(durations) if durations else 0
        min_duration = min(durations) if durations else 0
        max_duration = max(durations) if durations else 0

        currency = rows[0].currency if rows else "USD"

        sl_closes = sum(1 for r in rows if r.close_reason == 'sl_triggered')
        tp_closes = sum(1 for r in rows if r.close_reason == 'tp_triggered')
        scenario_closes = sum(1 for r in rows if r.close_reason == 'scenario_end')
        manual_closes = total_trades - sl_closes - tp_closes - scenario_closes

        print(f"\n   {renderer.bold('📈 TRADE BREAKDOWN:')}")
        print(f"      Total Trades: {total_trades} (Long: {long_trades} | Short: {short_trades})")
        print(f"      Winners: {len(winning_trades)} | Losers: {len(losing_trades)}")
        print(
            f"      Close Reasons: SL={sl_closes} | TP={tp_closes} | Manual={manual_closes}"
            + (f" | Scenario End={scenario_closes}" if scenario_closes else ""))

        print(f"\n   {renderer.bold('💰 P&L BREAKDOWN:')}")
        print(f"      Gross P&L: {self._format_value(total_gross, renderer)} {currency}")
        print(f"      Total Fees: -{total_fees:.2f} {currency}")
        print(f"      Net P&L: {self._format_value(total_net, renderer)} {currency}")

        print(f"\n   {renderer.bold('📊 AVERAGES (per trade):')}")
        print(
            f"      Avg Gross: {self._format_value(avg_gross, renderer)} | "
            f"Avg Fees: -{avg_fees:.2f} | Avg Net: {self._format_value(avg_net, renderer)}")

        print(f"\n   {renderer.bold('⏱️  DURATION (ticks):')}")
        print(f"      Avg: {avg_duration:.0f} | Min: {min_duration} | Max: {max_duration}")

        self._render_analytics(analytics, renderer)
        self._render_slippage_stats(rows, renderer)

    def _render_analytics(self, analytics, renderer: ConsoleRenderer) -> None:
        """Render the #389 trade-analytics block from the (per-currency) TradeAnalytics."""
        a = analytics
        currency = a.currency
        print(f"\n   {renderer.bold('📐 TRADE ANALYTICS (#389):')}")
        print(
            f"      Expectancy: {a.expectancy:+.3f} R  |  "
            f"R-trades: {a.r_trade_count}/{a.trade_count} (with a stop loss)")
        print(f"      Avg Win-R: {a.avg_win_r:+.3f}  |  Avg Loss-R: {a.avg_loss_r:+.3f}")
        print(
            f"      Avg MAE — winners: {self._format_value(a.avg_mae_winners, renderer)} "
            f"| losers: {self._format_value(a.avg_mae_losers, renderer)} {currency}")
        print(
            f"      Avg MFE — losers:  {self._format_value(a.avg_mfe_losers, renderer)} {currency}  "
            f"(left on the table)")

    def _render_slippage_stats(
        self, rows: List[TradeHistoryRow], renderer: ConsoleRenderer) -> None:
        """Render submission-tick-vs-fill-price slippage statistics (#340), from the model."""
        entry = [(r.entry_slippage, r.entry_slippage_pct) for r in rows if r.entry_slippage is not None]
        exit_ = [(r.exit_slippage, r.exit_slippage_pct) for r in rows if r.exit_slippage is not None]
        if not entry and not exit_:
            return  # no submission-tick data captured (legacy / cleanup-only)

        print(f"\n   {renderer.bold('📐 SLIPPAGE (submission tick vs fill):')}")
        print(f"      Samples: {len(entry)} entry / {len(exit_)} exit (of {len(rows)} trades)")
        if entry:
            avg, p50, p95, max_v, avg_pct, p95_pct = self._slippage_aggregates(entry)
            print(f"      Entry:  avg={avg:+.5f} ({avg_pct:+.4f}%)  p50={p50:+.5f}  "
                  f"p95={p95:+.5f} ({p95_pct:+.4f}%)  max={max_v:+.5f}")
        if exit_:
            avg, p50, p95, max_v, avg_pct, p95_pct = self._slippage_aggregates(exit_)
            print(f"      Exit:   avg={avg:+.5f} ({avg_pct:+.4f}%)  p50={p50:+.5f}  "
                  f"p95={p95:+.5f} ({p95_pct:+.4f}%)  max={max_v:+.5f}")
        print(f"      Sign convention: positive = paid worse than submission mid (adverse).")

    @staticmethod
    def _slippage_aggregates(samples):
        """avg/p50/p95/max over the price deltas + avg/p95 over the percent values."""
        prices = sorted(s for s, _ in samples)
        pcts = sorted(p for _, p in samples)
        n = len(prices)
        avg = sum(prices) / n
        p50 = prices[n // 2]
        p95 = prices[min(n - 1, int(n * 0.95))]
        max_v = max(prices, key=abs)
        avg_pct = sum(pcts) / n
        p95_pct = pcts[min(n - 1, int(n * 0.95))]
        return avg, p50, p95, max_v, avg_pct, p95_pct

    def _render_aggregated_rejections(
        self, rejections: List[OrderHistoryRow], renderer: ConsoleRenderer) -> None:
        """Render aggregated rejection breakdown by reason."""
        reason_counts: Dict[str, int] = {}
        for rej in rejections:
            reason = rej.rejection_reason or "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        print(f"\n   {renderer.bold('Rejected Orders:')}")
        print(f"      Total Rejections: {len(rejections)}")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(renderer.yellow(f"      {reason}: {count}"))

    def _format_pnl(self, value: float, currency: str, renderer: ConsoleRenderer) -> str:
        """Format P&L value with color and currency."""
        if value > 0:
            return renderer.green(f"+{value:.2f} {currency}")
        elif value < 0:
            return renderer.red(f"{value:.2f} {currency}")
        return f"{value:.2f} {currency}"

    def _format_value(self, value: float, renderer: ConsoleRenderer) -> str:
        """Format numeric value with color."""
        if value > 0:
            return renderer.green(f"+{value:.2f}")
        elif value < 0:
            return renderer.red(f"{value:.2f}")
        return f"{value:.2f}"
