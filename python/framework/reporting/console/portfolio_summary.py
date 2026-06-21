"""
FiniexTestingIDE - Portfolio Summary

Per-scenario portfolio results render **linearly** (one block per unit) purely from the
unified report model (#393) — `PortfolioReport` (full projection) + `PendingOrdersReport` +
`ExecutionStatsReport` (order counts). The per-currency **aggregated** section renders from the
`AggregatedPortfolioReport` model (#397) — `PortfolioAggregator` retired.
"""

from typing import Dict, List, Optional

from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.api.report_types import (
    ActiveOrderRow, AggregatedPortfolioCurrency, AggregatedPortfolioReport, AggregatedPortfolioRow,
    ExecutionStatsReport, ExecutionStatsRow, PendingOrdersReport, PendingOrdersUnitRow,
    PortfolioReport, PortfolioUnitRow)
from python.framework.types.trading_env_types.currency_codes import format_currency_simple
from python.framework.utils.math_utils import force_negative, force_positive


class PortfolioSummary(AbstractBatchSummarySection):
    """
    Portfolio and trading statistics summary.

    Per-scenario: linear blocks from the model. Aggregated: per-currency from the model (#397).
    """

    _section_title = '💰 PORTFOLIO & TRADING RESULTS'

    def __init__(
        self,
        report: PortfolioReport,
        pending_report: PendingOrdersReport,
        execution_report: ExecutionStatsReport,
        aggregated_report: AggregatedPortfolioReport,
    ):
        """
        Initialize portfolio summary.

        Args:
            report: The unified portfolio report (per-unit full projection + aggregates)
            pending_report: The unified pending-orders report (per-unit lifecycle + active)
            execution_report: The unified execution-stats report (per-unit order counts)
            aggregated_report: The aggregated per-currency portfolio report (#397)
        """
        self._report = report
        self._pending_report = pending_report
        self._execution_report = execution_report
        self._aggregated_report = aggregated_report

    # ============================================
    # Per-scenario (linear, from the model)
    # ============================================

    def render_per_scenario(self, renderer: ConsoleRenderer):
        """
        Render portfolio stats per scenario as linear blocks (one below another).

        Args:
            renderer: ConsoleRenderer instance
        """
        self._render_section_header(renderer)

        if not self._report.units:
            print("No portfolio data available")
            return

        pending_by_name: Dict[str, PendingOrdersUnitRow] = {
            u.name: u for u in self._pending_report.units}
        execution_by_name: Dict[str, ExecutionStatsRow] = {
            u.name: u for u in self._execution_report.units}

        print()
        for idx, unit in enumerate(self._report.units, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
            self._render_unit_block(
                unit, pending_by_name.get(unit.name),
                execution_by_name.get(unit.name), renderer)

        # Active order detail tables (per scenario with active orders)
        self._render_active_order_details(renderer)

    def _render_unit_block(
        self,
        unit: PortfolioUnitRow,
        pending: Optional[PendingOrdersUnitRow],
        execution: Optional[ExecutionStatsRow],
        renderer: ConsoleRenderer
    ) -> None:
        """Render a single scenario's portfolio block (linear)."""
        currency_disp = f"{unit.currency} [SPOT]" if unit.spot_mode else unit.currency
        broker = (unit.broker_name[:30] if unit.broker_name else '—')
        data = f" | Data: {unit.data_source}" if unit.data_source else ''
        print(f"💰 {renderer.bold(unit.name)} — {broker} ({currency_disp}){data}")
        if unit.has_error:
            print(renderer.red("   ⚠️ CRITICAL: Errors detected"))

        # No-trades case
        if unit.total_trades == 0:
            orders = self._orders_line(execution, renderer)
            print(f"   No trades executed" + (f" | {orders}" if orders else ""))
            active = self._active_summary(pending, renderer)
            if active:
                print(f"   {active}")
            return

        print(
            f"   Trades: {unit.total_trades} ({unit.winning_trades}W/{unit.losing_trades}L) | "
            f"Win {unit.win_rate:.1%} | "
            f"Long/Short {unit.total_long_trades}/{unit.total_short_trades}")

        rate = f" @ {unit.conversion_rate:.4f}" if unit.conversion_rate is not None else ''
        print(f"   P&L: {renderer.pnl(unit.net_profit, unit.currency)}{rate}")

        for line in self._balance_lines(unit, renderer):
            if line:
                print(f"   {line}")

        max_dd_pct = (unit.max_drawdown / unit.max_equity * 100) if unit.max_equity > 0 else 0.0
        print(
            f"   Max DD: {renderer.pnl(force_negative(unit.max_drawdown), unit.currency)} "
            f"({max_dd_pct:.1f}%) | "
            f"Max Equity: {renderer.pnl(force_positive(unit.max_equity), unit.currency)}")

        print(
            f"   Cost: spread {renderer.pnl(force_negative(unit.total_spread_cost), unit.currency)} | "
            f"comm {renderer.pnl(force_negative(unit.total_commission), unit.currency)} | "
            f"swap {renderer.pnl(force_negative(unit.total_swap), unit.currency)} | "
            f"maker {renderer.pnl(force_negative(unit.maker_fee), unit.currency)} | "
            f"taker {renderer.pnl(force_negative(unit.taker_fee), unit.currency)}")

        orders = self._orders_line(execution, renderer)
        if orders:
            print(f"   {orders}")
        pending_line = self._pending_line(pending, renderer)
        if pending_line:
            print(f"   {pending_line}")
        active = self._active_summary(pending, renderer)
        if active:
            print(f"   {active}")

    def _balance_lines(
        self, unit: PortfolioUnitRow, renderer: ConsoleRenderer) -> List[str]:
        """Balance + init (+ spot estimated P&L) lines — spot-aware, from the model."""
        currency = unit.currency
        if not unit.spot_mode:
            initial_str = format_currency_simple(unit.initial_balance, currency)
            current_str = format_currency_simple(unit.current_balance, currency)
            if unit.current_balance > unit.initial_balance:
                current_str = renderer.green(current_str)
            elif unit.current_balance < unit.initial_balance:
                current_str = renderer.red(current_str)
            return [f"Balance: {current_str} (init {initial_str})"]

        # Spot mode — dual balance + estimated portfolio value
        symbol = unit.symbol
        quote = symbol[-3:] if len(symbol) >= 6 else currency
        base = symbol[:-3] if len(symbol) >= 6 else ''
        quote_bal = unit.balances.get(quote, 0.0)
        base_bal = unit.balances.get(base, 0.0)
        quote_init = unit.initial_balances.get(quote, 0.0)
        base_init = unit.initial_balances.get(base, 0.0)
        base_fmt = f'{base_bal:,.4f}' if base_bal < 100 else f'{base_bal:,.2f}'
        base_init_fmt = f'{base_init:,.4f}' if base_init < 100 else f'{base_init:,.2f}'

        lines = [
            f"Bal: {format_currency_simple(quote_bal, quote)} | {base} {base_fmt}",
            f"Init: {format_currency_simple(quote_init, quote)} | {base} {base_init_fmt}",
        ]
        if unit.last_price > 0:
            est_current = quote_bal + (base_bal * unit.last_price)
            est_initial = quote_init + (base_init * unit.last_price)
            est_pnl = est_current - est_initial
            est_pnl_pct = (est_pnl / est_initial * 100) if est_initial > 0 else 0.0
            sign = '+' if est_pnl >= 0 else ''
            price_str = format_currency_simple(unit.last_price, quote)
            lines.append(
                f"Est: {sign}{format_currency_simple(est_pnl, quote)} "
                f"({sign}{est_pnl_pct:.2f}%) @ {base} {price_str}")
        return lines

    @staticmethod
    def _orders_line(
        execution: Optional[ExecutionStatsRow], renderer: ConsoleRenderer) -> str:
        """Order execution counts line (from the execution-stats model)."""
        if execution is None:
            return ''
        line = f"Orders: {execution.orders_executed}/{execution.orders_sent} executed"
        if execution.orders_rejected > 0:
            line += f" | {renderer.yellow(f'Rej: {execution.orders_rejected}')}"
        return line

    @staticmethod
    def _pending_line(
        pending: Optional[PendingOrdersUnitRow], renderer: ConsoleRenderer) -> str:
        """Pending-order latency summary line (green), or '' when no latency data."""
        if pending is None or pending.min_latency_ms is None:
            return ''
        text = (
            f"Pending: avg {pending.avg_latency_ms:.0f}ms "
            f"({pending.min_latency_ms:.0f}-{pending.max_latency_ms:.0f})")
        anomalies = []
        if pending.total_force_closed > 0:
            anomalies.append(f"{pending.total_force_closed} forced")
        if pending.total_timed_out > 0:
            anomalies.append(f"{pending.total_timed_out} timeout")
        if anomalies:
            text += " | " + renderer.yellow(" | ".join(anomalies))
        return renderer.green(text)

    @staticmethod
    def _active_summary(
        pending: Optional[PendingOrdersUnitRow], renderer: ConsoleRenderer) -> str:
        """Active orders at run end summary line (cyan), or '' when none."""
        if pending is None:
            return ''
        parts = []
        if pending.active_limit_orders:
            parts.append(f"{len(pending.active_limit_orders)} limits")
        if pending.active_stop_orders:
            parts.append(f"{len(pending.active_stop_orders)} stops")
        return renderer.cyan(f"Active: {' | '.join(parts)}") if parts else ''

    def _render_active_order_details(self, renderer: ConsoleRenderer) -> None:
        """Render active-order detail tables for each unit with active orders (from the model)."""
        for unit in self._pending_report.units:
            active = unit.active_limit_orders + unit.active_stop_orders
            if not active:
                continue
            print()
            print(renderer.cyan(
                f"   ┌─ Active Orders at Scenario End: {unit.name} "
                f"({len(active)} order{'s' if len(active) > 1 else ''}) ─┐"))
            self._render_active_order_table(renderer, active)

    @staticmethod
    def _render_active_order_table(
        renderer: ConsoleRenderer, orders: List[ActiveOrderRow]) -> None:
        """Render the active-order table (from the model's ActiveOrderRows)."""
        header = (
            f"   {'ID':<16} {'Type':<12} {'Dir':<6} "
            f"{'Trigger':>12} {'Limit':>12} {'Lots':>10} {'SL/TP'}"
        )
        print(renderer.cyan(header))
        print(renderer.cyan(f"   {'─' * 80}"))
        for order in orders:
            limit_str = f"{order.limit_price:.2f}" if order.limit_price else '—'
            sl_str = f"{order.stop_loss:.2f}" if order.stop_loss else '—'
            tp_str = f"{order.take_profit:.2f}" if order.take_profit else '—'
            line = (
                f"   {order.order_id:<16} {order.order_type.upper():<12} "
                f"{order.direction.upper():<6} "
                f"{order.entry_price:>12.2f} {limit_str:>12} "
                f"{order.lots:>10g} {sl_str}/{tp_str}"
            )
            print(renderer.cyan(line))

    # ============================================
    # Aggregated (per currency, from the model — #397)
    # ============================================

    def render_aggregated(self, renderer: ConsoleRenderer):
        """
        Render the aggregated portfolio per currency from the model (#397).

        Args:
            renderer: ConsoleRenderer instance
        """
        currencies = self._aggregated_report.currencies
        if not currencies:
            return

        has_multiple_currencies = len(currencies) > 1

        print()
        renderer.section_separator()
        if has_multiple_currencies:
            renderer.print_bold("📊 AGGREGATED PORTFOLIO (BY CURRENCY)")
        else:
            renderer.print_bold("📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)")
        renderer.section_separator()

        for cur in currencies:
            self._render_currency_group(renderer, cur, has_multiple_currencies)
            renderer.print_separator(width=120, char="·")

        # The aggregation-limitation notices (multi-currency / time-divergence) moved to the
        # post-run validator → the WARNINGS & ERRORS section (no decisions in reports, #395/#397).
        print()

    def _render_currency_group(
        self,
        renderer: ConsoleRenderer,
        cur: AggregatedPortfolioCurrency,
        show_currency_header: bool
    ):
        """
        Render one currency group's combined aggregate (margin + spot together).

        Args:
            renderer: ConsoleRenderer instance
            cur: Aggregated currency group
            show_currency_header: Whether to show the currency-specific header
        """
        if show_currency_header:
            print()
            scenario_names = ", ".join(cur.scenario_names)
            print(
                f"\n{renderer.bold(f'   💰 {cur.currency} GROUP ({cur.scenario_count} scenarios)')}")
            print(f"      Scenarios: {scenario_names}")

        self._render_aggregated_details(cur.combined, cur.currency, renderer)

    def _render_aggregated_details(
        self,
        row: AggregatedPortfolioRow,
        currency: str,
        renderer: ConsoleRenderer
    ):
        """Render detailed aggregated portfolio stats from the model row (#397)."""
        h = row.headline
        total_pnl = h.total_profit - h.total_loss

        print(f"\n{renderer.bold('   TRADING SUMMARY:')}")
        print(f"      Total Trades: {h.total_trades} (L/S: {row.total_long_trades}/{row.total_short_trades}) |  "
              f"Win/Loss: {h.winning_trades}W/{h.losing_trades}L  |  "
              f"Win Rate: {h.win_rate:.1%}")

        print(f"      Total P&L: {renderer.pnl(total_pnl, currency)}  |  "
              f"Profit: {renderer.pnl(force_positive(h.total_profit), currency)}  |  "
              f"Loss: {renderer.pnl(force_negative(h.total_loss), currency)}")

        # Profit factor
        if h.profit_factor == float('inf'):
            pf_str = "∞ (no losses)"
        else:
            pf_str = f"{h.profit_factor:.2f}"
        print(f"      Profit Factor: {pf_str}")

        print(f"\n{renderer.bold('   ORDER EXECUTION:')}")
        print(f"      Orders Sent: {row.orders_sent}  |  "
              f"Executed: {row.orders_executed}  |  "
              f"Rejected: {row.orders_rejected}")

        if row.orders_sent > 0:
            print(f"      Execution Rate: {row.orders_executed / row.orders_sent:.1%}")

        # Pending order statistics (green)
        self._render_pending_stats(renderer, row)

        # Cost breakdown (layout A — all five categories, zeros where n/a)
        total_costs = (row.total_spread_cost + row.total_commission + row.total_swap
                       + row.maker_fee + row.taker_fee)

        print(f"\n{renderer.bold('   💸 COST BREAKDOWN:')}")
        print(f"      Spread: {renderer.pnl(force_negative(row.total_spread_cost), currency)} |  "
              f"Commission: {renderer.pnl(force_negative(row.total_commission), currency)} |  "
              f"Swap: {renderer.pnl(force_negative(row.total_swap), currency)}")
        print(f"      Maker: {renderer.pnl(force_negative(row.maker_fee), currency)} |  "
              f"Taker: {renderer.pnl(force_negative(row.taker_fee), currency)} |  "
              f"Total Fees: {renderer.pnl(force_negative(total_costs), currency)}")

        print(f"\n   📉 RISK METRICS:")
        print(f"      Max Drawdown: {renderer.pnl(force_negative(h.max_drawdown), currency)} "
              f"({row.max_dd_pct:.1f}%) - Scenario: {row.max_drawdown_scenario}")
        print(f"      Max Equity: {renderer.pnl(force_positive(row.max_equity), currency)} "
              f"- Scenario: {row.max_equity_scenario}")

    @staticmethod
    def _render_pending_stats(
        renderer: ConsoleRenderer,
        row: AggregatedPortfolioRow
    ) -> None:
        """
        Render aggregated pending-order statistics from the model row (#397).

        Args:
            renderer: Console renderer for formatting
            row: Aggregated portfolio row carrying the pending fields
        """
        has_resolved = row.pending_total_resolved > 0
        has_active = row.pending_active_limit_count or row.pending_active_stop_count
        if not has_resolved and not has_active:
            return

        # Pending resolved breakdown (only if orders were resolved)
        if has_resolved:
            resolved_line = f"      Pending Resolved: {renderer.green(f'{row.pending_total_filled} filled')}"
            if row.pending_total_rejected > 0:
                resolved_line += f" | {row.pending_total_rejected} rejected"
            if row.pending_total_timed_out > 0:
                resolved_line += f" | {renderer.yellow(f'{row.pending_total_timed_out} timed out')}"
            if row.pending_total_force_closed > 0:
                resolved_line += f" | {renderer.yellow(f'{row.pending_total_force_closed} force-closed')}"
            print(resolved_line)

        # Latency stats (ms-based)
        if row.pending_min_latency_ms is not None:
            print(renderer.green(
                f"      Avg Latency: {row.pending_avg_latency_ms:.0f}ms "
                f"(min: {row.pending_min_latency_ms:.0f}ms | max: {row.pending_max_latency_ms:.0f}ms)"))

        # Active orders at scenario end (bot's pending plan)
        active_parts = []
        if row.pending_active_limit_count:
            active_parts.append(f"{row.pending_active_limit_count} limits")
        if row.pending_active_stop_count:
            active_parts.append(f"{row.pending_active_stop_count} stops")
        if active_parts:
            print(renderer.cyan(
                f"      Active Orders: {' | '.join(active_parts)}"))
