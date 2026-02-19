"""
FiniexTestingIDE - Trade History Summary
Detailed trade-by-trade history rendering for P&L verification

Renders:
- Per-scenario trade tables (chronological by entry_tick_index)
- Entry/exit prices, tick values, fees, P&L breakdown
- Aggregated trade statistics
"""

from typing import List

from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.order_types import OrderResult
from python.framework.types.portfolio_trade_record_types import CloseReason, TradeRecord
from python.framework.types.process_data_types import ProcessResult


class TradeHistorySummary:
    """
    Trade history summary renderer.

    Displays detailed trade-by-trade breakdown for each scenario,
    sorted chronologically by entry tick index.
    """

    def __init__(self, batch_execution_summary: BatchExecutionSummary) -> None:
        """
        Initialize trade history summary.

        Args:
            batch_execution_summary: Batch execution summary containing all scenario results
        """
        self._batch_execution_summary = batch_execution_summary
        self._process_results: List[ProcessResult] = batch_execution_summary.process_result_list

    def render_per_scenario(self, renderer: ConsoleRenderer) -> None:
        """
        Render trade history per scenario.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._process_results:
            print("No trade history available")
            return

        for idx, scenario in enumerate(self._process_results, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            self._render_scenario_trades(scenario, renderer)

    def render_aggregated(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregated trade statistics across all scenarios.

        Args:
            renderer: ConsoleRenderer instance
        """
        all_trades: List[TradeRecord] = []

        for scenario in self._process_results:
            if not scenario.tick_loop_results:
                continue
            trade_history = scenario.tick_loop_results.trade_history
            if trade_history:
                all_trades.extend(trade_history)

        if not all_trades:
            return

        print()
        renderer.section_separator()
        renderer.print_bold("📊 AGGREGATED TRADE STATISTICS")
        renderer.section_separator()

        # Collect all rejections
        all_rejections: List[OrderResult] = []
        for scenario in self._process_results:
            if not scenario.tick_loop_results:
                continue
            order_history = scenario.tick_loop_results.order_history
            if order_history:
                all_rejections.extend(o for o in order_history if o.is_rejected)

        self._render_aggregated_stats(all_trades, all_rejections, renderer)
        print()

    def _render_scenario_trades(
        self,
        scenario: ProcessResult,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render trade history for single scenario.

        Args:
            scenario: Scenario result to render
            renderer: ConsoleRenderer instance
        """
        if not scenario.tick_loop_results:
            return

        trade_history = scenario.tick_loop_results.trade_history
        if not trade_history:
            print(f"📋 {renderer.bold(scenario.scenario_name)}: No trades executed")
            return

        # Sort by entry_tick_index (chronological)
        sorted_trades = sorted(trade_history, key=lambda t: t.entry_tick_index)

        # Header
        trade_count = len(sorted_trades)
        total_pnl = sum(t.net_pnl for t in sorted_trades)
        currency = sorted_trades[0].account_currency if sorted_trades else "USD"

        pnl_str = self._format_pnl(total_pnl, currency, renderer)
        print(
            f"📋 {renderer.bold(scenario.scenario_name)}: {trade_count} trades | Total P&L: {pnl_str}")
        print()

        # Table header
        self._print_table_header(renderer)

        # Table rows
        for idx, trade in enumerate(sorted_trades, 1):
            self._print_trade_row(idx, trade, renderer)

        # Table footer
        self._print_table_footer(sorted_trades, renderer)

        # Rejected orders (if any)
        self._render_scenario_rejections(scenario, renderer)

    def _render_scenario_rejections(
        self,
        scenario: ProcessResult,
        renderer: ConsoleRenderer
    ) -> None:
        """Render rejected orders for a scenario (from order_history)."""
        order_history = scenario.tick_loop_results.order_history
        if not order_history:
            return

        rejections = [o for o in order_history if o.is_rejected]
        if not rejections:
            return

        print()
        print(renderer.yellow(
            f"   Rejected Orders: {len(rejections)}"
        ))

        # Rejection header
        header = f"   {'#':>3} | {'Order ID':<20} | {'Reason':<25} | {'Message'}"
        print(renderer.gray(header))
        print(renderer.gray("   " + "-" * 100))

        for idx, rej in enumerate(rejections, 1):
            reason_str = rej.rejection_reason.value if rej.rejection_reason else "unknown"
            msg = rej.rejection_message or ""
            row = f"   {idx:>3} | {rej.order_id:<20} | {reason_str:<25} | {msg}"
            print(renderer.yellow(row))

    def _print_table_header(self, renderer: ConsoleRenderer) -> None:
        """Print trade table header."""
        header = (
            f"   {'#':>3} | {'Dir':^5} | {'Lots':>5} | "
            f"{'Entry Price':>12} | {'Exit Price':>12} | "
            f"{'SL':>12} | {'TP':>12} | "
            f"{'Entry Tick':>10} | {'Exit Tick':>10} | {'Duration':>8} | "
            f"{'Gross P&L':>10} | {'Fees':>8} | {'Net P&L':>10} | {'Close Reason':>14}"
        )
        print(renderer.gray(header))
        print(renderer.gray("   " + "-" * 170))

    def _print_trade_row(
        self,
        idx: int,
        trade: TradeRecord,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Print single trade row.

        Args:
            idx: Trade number
            trade: TradeRecord to display
            renderer: ConsoleRenderer instance
        """
        # Direction with color
        if trade.direction == "LONG":
            dir_str = renderer.green("LONG ")
        else:
            dir_str = renderer.red("SHORT")

        # SL/TP formatting
        sl_str = f"{trade.stop_loss:>12.5f}" if trade.stop_loss is not None else f"{'—':>12}"
        tp_str = f"{trade.take_profit:>12.5f}" if trade.take_profit is not None else f"{'—':>12}"

        # Duration
        duration = trade.exit_tick_index - trade.entry_tick_index

        # P&L formatting
        gross_str = self._format_value(trade.gross_pnl, renderer)
        net_str = self._format_value(trade.net_pnl, renderer)

        # Close reason
        reason_str = trade.close_reason.value if trade.close_reason != CloseReason.MANUAL else ""

        # Build row
        row = (
            f"   {idx:>3} | {dir_str} | {trade.lots:>5.2f} | "
            f"{trade.entry_price:>12.5f} | {trade.exit_price:>12.5f} | "
            f"{sl_str} | {tp_str} | "
            f"{trade.entry_tick_index:>10} | {trade.exit_tick_index:>10} | {duration:>8} | "
            f"{gross_str:>10} | {trade.total_fees:>8.2f} | {net_str:>10} | {reason_str:>14}"
        )
        print(row)

    def _print_table_footer(
        self,
        trades: List[TradeRecord],
        renderer: ConsoleRenderer
    ) -> None:
        """
        Print trade table footer with totals.

        Args:
            trades: List of trades
            renderer: ConsoleRenderer instance
        """
        print(renderer.gray("   " + "-" * 170))

        total_gross = sum(t.gross_pnl for t in trades)
        total_fees = sum(t.total_fees for t in trades)
        total_net = sum(t.net_pnl for t in trades)
        currency = trades[0].account_currency if trades else "USD"

        gross_str = self._format_value(total_gross, renderer)
        net_str = self._format_value(total_net, renderer)

        total_row = (
            f"   {'':>3} | {'TOTAL':^5} | {'':>5} | "
            f"{'':>12} | {'':>12} | "
            f"{'':>12} | {'':>12} | "
            f"{'':>10} | {'':>10} | {'':>8} | "
            f"{gross_str:>10} | {total_fees:>8.2f} | {net_str:>10} {currency} |"
        )
        print(renderer.bold(total_row))

    def _render_aggregated_stats(
        self,
        trades: List[TradeRecord],
        rejections: List[OrderResult],
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render aggregated statistics for all trades.

        Args:
            trades: All trades across scenarios
            rejections: All rejected orders across scenarios
            renderer: ConsoleRenderer instance
        """
        if not trades:
            return

        # Basic counts
        total_trades = len(trades)
        long_trades = sum(1 for t in trades if t.direction == "LONG")
        short_trades = total_trades - long_trades

        # P&L stats
        winning_trades = [t for t in trades if t.net_pnl > 0]
        losing_trades = [t for t in trades if t.net_pnl <= 0]

        total_gross = sum(t.gross_pnl for t in trades)
        total_fees = sum(t.total_fees for t in trades)
        total_net = sum(t.net_pnl for t in trades)

        # Averages
        avg_gross = total_gross / total_trades if total_trades else 0
        avg_fees = total_fees / total_trades if total_trades else 0
        avg_net = total_net / total_trades if total_trades else 0

        # Duration stats
        durations = [t.exit_tick_index - t.entry_tick_index for t in trades]
        avg_duration = sum(durations) / len(durations) if durations else 0
        min_duration = min(durations) if durations else 0
        max_duration = max(durations) if durations else 0

        currency = trades[0].account_currency if trades else "USD"

        # Close reason counts
        sl_closes = sum(1 for t in trades if t.close_reason == CloseReason.SL_TRIGGERED)
        tp_closes = sum(1 for t in trades if t.close_reason == CloseReason.TP_TRIGGERED)
        scenario_closes = sum(1 for t in trades if t.close_reason == CloseReason.SCENARIO_END)
        manual_closes = total_trades - sl_closes - tp_closes - scenario_closes

        print(f"\n   {renderer.bold('📈 TRADE BREAKDOWN:')}")
        print(
            f"      Total Trades: {total_trades} (Long: {long_trades} | Short: {short_trades})")
        print(
            f"      Winners: {len(winning_trades)} | Losers: {len(losing_trades)}")
        print(
            f"      Close Reasons: SL={sl_closes} | TP={tp_closes} | Manual={manual_closes}"
            + (f" | Scenario End={scenario_closes}" if scenario_closes else "")
        )

        print(f"\n   {renderer.bold('💰 P&L BREAKDOWN:')}")
        gross_str = self._format_value(total_gross, renderer)
        net_str = self._format_value(total_net, renderer)
        print(f"      Gross P&L: {gross_str} {currency}")
        print(f"      Total Fees: -{total_fees:.2f} {currency}")
        print(f"      Net P&L: {net_str} {currency}")

        print(f"\n   {renderer.bold('📊 AVERAGES (per trade):')}")
        avg_gross_str = self._format_value(avg_gross, renderer)
        avg_net_str = self._format_value(avg_net, renderer)
        print(
            f"      Avg Gross: {avg_gross_str} | Avg Fees: -{avg_fees:.2f} | Avg Net: {avg_net_str}")

        print(f"\n   {renderer.bold('⏱️  DURATION (ticks):')}")
        print(
            f"      Avg: {avg_duration:.0f} | Min: {min_duration} | Max: {max_duration}")

        # Rejection breakdown (if any)
        if rejections:
            self._render_aggregated_rejections(rejections, renderer)

    def _render_aggregated_rejections(
        self,
        rejections: List[OrderResult],
        renderer: ConsoleRenderer
    ) -> None:
        """Render aggregated rejection breakdown by reason."""
        # Group by reason
        reason_counts = {}
        for rej in rejections:
            reason = rej.rejection_reason.value if rej.rejection_reason else "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        print(f"\n   {renderer.bold('Rejected Orders:')}")
        print(f"      Total Rejections: {len(rejections)}")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(renderer.yellow(f"      {reason}: {count}"))

    def _format_pnl(
        self,
        value: float,
        currency: str,
        renderer: ConsoleRenderer
    ) -> str:
        """
        Format P&L value with color and currency.

        Args:
            value: P&L value
            currency: Currency code
            renderer: ConsoleRenderer instance

        Returns:
            Formatted string
        """
        if value > 0:
            return renderer.green(f"+{value:.2f} {currency}")
        elif value < 0:
            return renderer.red(f"{value:.2f} {currency}")
        else:
            return f"{value:.2f} {currency}"

    def _format_value(self, value: float, renderer: ConsoleRenderer) -> str:
        """
        Format numeric value with color.

        Args:
            value: Numeric value
            renderer: ConsoleRenderer instance

        Returns:
            Formatted string
        """
        if value > 0:
            return renderer.green(f"+{value:.2f}")
        elif value < 0:
            return renderer.red(f"{value:.2f}")
        else:
            return f"{value:.2f}"
