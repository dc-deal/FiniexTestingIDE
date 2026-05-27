"""
FiniexTestingIDE - Trade History Summary
Detailed trade-by-trade history rendering for P&L verification

Renders:
- Per-scenario trade tables (chronological by entry_tick_index)
- Entry/exit prices, tick values, fees, P&L breakdown
- Aggregated trade statistics
"""

from typing import Dict, List

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason, EntryType, TradeRecord
from python.framework.types.process_data_types import ProcessResult


class TradeHistorySummary(AbstractBatchSummarySection):
    """
    Trade history summary renderer.

    Displays detailed trade-by-trade breakdown for each scenario,
    sorted chronologically by entry tick index.
    """

    _section_title = '📋 TRADE HISTORY (PER SCENARIO)'

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
        self._render_section_header(renderer)

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
                all_rejections.extend(
                    o for o in order_history if o.is_rejected)

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

        # Pre-compute entry trade_id frequency for shared(Nx) annotation (#330).
        # When a position is partially closed N times, all derived TradeRecords
        # carry the SAME entry_trades list — the renderer flags the duplicates.
        shared_counts: Dict[str, int] = {}
        for t in sorted_trades:
            for bt in t.entry_trades:
                shared_counts[bt.trade_id] = shared_counts.get(bt.trade_id, 0) + 1

        # Table header
        self._print_table_header(renderer)

        # Table rows
        for idx, trade in enumerate(sorted_trades, 1):
            self._print_trade_row(idx, trade, renderer, shared_counts)

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
            f"   {'#':>3} | {'Side':^5} | {'ET':>2} | {'Lots':>5} | "
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
        renderer: ConsoleRenderer,
        shared_counts: Dict[str, int]
    ) -> None:
        """
        Print single trade row plus per-execution sub-lines (#330).

        Args:
            idx: Trade number
            trade: TradeRecord to display
            renderer: ConsoleRenderer instance
            shared_counts: trade_id frequency map across the scenario, used
                for the shared(Nx) annotation on shared entry executions
        """
        # Trade-event side (BUY/SELL — what the close operation was), colored
        # by the underlying position direction. Falls back to the position
        # direction string for legacy TradeRecords without exit_side populated.
        if trade.exit_side is not None:
            side_text = trade.exit_side.value.upper().ljust(5)
        else:
            side_text = ('LONG ' if trade.direction == OrderDirection.LONG else 'SHORT')
        if trade.direction == OrderDirection.LONG:
            dir_str = renderer.green(side_text)
        else:
            dir_str = renderer.red(side_text)

        # Entry type (M=Market, L=Limit, S=Stop, SL=Stop-Limit)
        entry_type_map = {
            EntryType.MARKET: "M",
            EntryType.LIMIT: "L",
            EntryType.STOP: "S",
            EntryType.STOP_LIMIT: "SL",
        }
        entry_type_str = entry_type_map.get(trade.entry_type, "?")

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
            f"   {idx:>3} | {dir_str} | {entry_type_str:>2} | {trade.lots:>5.2f} | "
            f"{trade.entry_price:>12.5f} | {trade.exit_price:>12.5f} | "
            f"{sl_str} | {tp_str} | "
            f"{trade.entry_tick_index:>10} | {trade.exit_tick_index:>10} | {duration:>8} | "
            f"{gross_str:>10} | {trade.total_fees:>8.2f} | {net_str:>10} | {reason_str:>14}"
        )
        print(row)

        # Per-execution sub-lines (#330) — render the underlying BrokerTrades
        # that produced this aggregate row. Single-fill case is still emitted
        # (1+1) so the format is consistent across single- and multi-fill records.
        for bt in trade.entry_trades:
            self._render_subline(
                'in', bt, shared_counts.get(bt.trade_id, 1), renderer,
                trade_record_lots=trade.lots,
            )
        for bt in trade.exit_trades:
            self._render_subline('out', bt, 1, renderer)

    def _render_subline(
        self,
        side: str,
        broker_trade: BrokerTrade,
        shared_count: int,
        renderer: ConsoleRenderer,
        trade_record_lots: float = None,
    ) -> None:
        """
        Render one per-execution sub-line under an aggregate trade row (#330).

        Args:
            side: 'in' (entry side) or 'out' (exit side)
            broker_trade: Atomic execution to render
            shared_count: How many TradeRecords share this entry trade_id.
                Exit side passes 1 (no sharing on close side by construction).
            renderer: ConsoleRenderer instance
            trade_record_lots: TradeRecord.lots (this close-event's share). When
                shared > 1 and differs from broker_trade.volume, the renderer
                appends "this trade: X of Y" to clarify the partial-close share
                vs. the full original entry execution.
        """
        maker_taker = 'maker' if broker_trade.is_maker else 'taker'
        side_label = 'in ' if side == 'in' else 'out'

        suffix = ""
        if shared_count > 1:
            suffix = f"  shared({shared_count}x)"
            if trade_record_lots is not None and trade_record_lots != broker_trade.volume:
                suffix += f"  (this trade: {trade_record_lots:g} of {broker_trade.volume:g})"

        text = (
            f"     └─ {side_label}  {broker_trade.trade_id:<24}  "
            f"vol {broker_trade.volume:>7.5f}  "
            f"price {broker_trade.price:>12.5f}  "
            f"fee {broker_trade.fee:>7.4f}  "
            f"{maker_taker}"
            f"{suffix}"
        )
        print(renderer.gray(text))

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
            f"   {'':>3} | {'TOTAL':^5} | {' ':1} | {'':>5} | "
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
        long_trades = sum(1 for t in trades if t.direction ==
                          OrderDirection.LONG)
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
        sl_closes = sum(1 for t in trades if t.close_reason ==
                        CloseReason.SL_TRIGGERED)
        tp_closes = sum(1 for t in trades if t.close_reason ==
                        CloseReason.TP_TRIGGERED)
        scenario_closes = sum(
            1 for t in trades if t.close_reason == CloseReason.SCENARIO_END)
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

        # Slippage statistics (#340) — sim-side equivalent of the live audit
        # SLIPPAGE counter. Same field source (TradeRecord.entry_submission_tick_*
        # / exit_submission_tick_*) and direction-aware sign convention.
        self._render_slippage_stats(trades, renderer)

        # Rejection breakdown (if any)
        if rejections:
            self._render_aggregated_rejections(rejections, renderer)

    def _render_slippage_stats(
        self,
        trades: List[TradeRecord],
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render submission-tick-vs-fill-price slippage statistics (#340).

        Sign convention is direction-aware:
            BUY  fill: adverse = fill_price - submission_tick_mid_price
            SELL fill: adverse = submission_tick_mid_price - fill_price
        Positive = paid worse than the mid the algo saw at submission
        (typical: half-spread + latency drift). Negative = price improvement.

        Args:
            trades: All TradeRecord across scenarios
            renderer: ConsoleRenderer instance
        """
        entry_samples = [
            self._adverse_slippage(t.entry_price, t.entry_submission_tick_mid_price, t.entry_side)
            for t in trades
            if t.entry_submission_tick_mid_price is not None and t.entry_side is not None
        ]
        exit_samples = [
            self._adverse_slippage(t.exit_price, t.exit_submission_tick_mid_price, t.exit_side)
            for t in trades
            if t.exit_submission_tick_mid_price is not None and t.exit_side is not None
        ]

        if not entry_samples and not exit_samples:
            return  # No submission-tick data captured (e.g. legacy run, sim cleanup-only)

        print(f"\n   {renderer.bold('📐 SLIPPAGE (submission tick vs fill):')}")
        print(f"      Samples: {len(entry_samples)} entry / {len(exit_samples)} exit "
              f"(of {len(trades)} trades)")

        if entry_samples:
            avg, p50, p95, max_v, avg_pct, p95_pct = self._slippage_aggregates(
                entry_samples,
                ref_prices=[t.entry_submission_tick_mid_price for t in trades
                            if t.entry_submission_tick_mid_price is not None and t.entry_side is not None],
            )
            print(f"      Entry:  avg={avg:+.5f} ({avg_pct:+.4f}%)  p50={p50:+.5f}  "
                  f"p95={p95:+.5f} ({p95_pct:+.4f}%)  max={max_v:+.5f}")

        if exit_samples:
            avg, p50, p95, max_v, avg_pct, p95_pct = self._slippage_aggregates(
                exit_samples,
                ref_prices=[t.exit_submission_tick_mid_price for t in trades
                            if t.exit_submission_tick_mid_price is not None and t.exit_side is not None],
            )
            print(f"      Exit:   avg={avg:+.5f} ({avg_pct:+.4f}%)  p50={p50:+.5f}  "
                  f"p95={p95:+.5f} ({p95_pct:+.4f}%)  max={max_v:+.5f}")

        print(f"      Sign convention: positive = paid worse than submission mid (adverse).")

    @staticmethod
    def _adverse_slippage(fill_price: float, submission_mid: float, side: OrderSide) -> float:
        """Direction-aware adverse slippage delta (positive = worse than mid)."""
        if side is OrderSide.BUY:
            return fill_price - submission_mid
        return submission_mid - fill_price

    @staticmethod
    def _slippage_aggregates(samples: List[float], ref_prices: List[float]):
        """Compute avg/p50/p95/max in price units and avg/p95 in percent vs the ref mid prices."""
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        avg = sum(sorted_samples) / n
        p50 = sorted_samples[n // 2]
        p95 = sorted_samples[min(n - 1, int(n * 0.95))]
        max_v = max(sorted_samples, key=abs)
        # Percent values: each sample normalised by its own ref price, then
        # aggregate. Cross-symbol robust (different magnitudes).
        pct_samples = sorted(
            (s / r * 100.0 if r else 0.0)
            for s, r in zip(samples, ref_prices)
        )
        avg_pct = sum(pct_samples) / n
        p95_pct = pct_samples[min(n - 1, int(n * 0.95))]
        return avg, p50, p95, max_v, avg_pct, p95_pct

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
