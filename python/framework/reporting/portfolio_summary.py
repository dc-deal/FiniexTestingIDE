"""
FiniexTestingIDE - Portfolio Summary
Trading and portfolio statistics rendering

Rendered in BOX format matching scenario details.
"""

from typing import Dict

from python.framework.reporting.grid.console_box_renderer import ConsoleBoxRenderer
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.pending_order_stats_types import PendingOrderStats
from python.framework.types.trading_env_stats_types import ExecutionStats, CostBreakdown
from python.framework.types.portfolio_aggregation_types import AggregatedPortfolio, AggregatedPortfolioStats, PortfolioStats
from python.framework.utils.math_utils import force_negative, force_positive


class PortfolioSummary:
    """
    Portfolio and trading statistics summary.
    """

    def __init__(self, batch_execution_summary: BatchExecutionSummary):
        """
        Initialize portfolio summary.
        """
        self.batch_execution_summary = batch_execution_summary

    def render_per_scenario(self, box_renderer: ConsoleBoxRenderer):
        """
        Render portfolio stats per scenario in BOX format.

        Args:
            renderer: ConsoleRenderer instance
        """
        # Use grid renderer
        print()
        box_renderer.render_portfolio_grid(
            batch_summary=self.batch_execution_summary,
            columns=3,      # 3 boxes per row
            box_width=38    # Same width as scenario boxes
        )

    def render_aggregated(self,
                          renderer: ConsoleRenderer,
                          aggregated_portfolios: Dict[str, AggregatedPortfolio]):
        """
        Render aggregated portfolio stats grouped by currency.

        Renders separate aggregation for each currency group to avoid
        cross-currency conversion issues.

        Args:
            renderer: ConsoleRenderer instance
            aggregated_portfolios: Dict mapping currency to aggregated portfolio
        """
        if not aggregated_portfolios:
            return

        # Check if we have multiple currency groups
        has_multiple_currencies = len(aggregated_portfolios) > 1

        print()
        renderer.section_separator()

        if has_multiple_currencies:
            renderer.print_bold("📊 AGGREGATED PORTFOLIO (BY CURRENCY)")
        else:
            renderer.print_bold("📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)")

        renderer.section_separator()

        # Render each currency group
        for currency, aggregated in aggregated_portfolios.items():
            self._render_currency_group(
                renderer, aggregated, has_multiple_currencies)
            renderer.print_separator(width=120, char="·")

        # Render multi-currency warning if applicable
        if has_multiple_currencies or self._has_any_time_divergence(aggregated_portfolios):
            self._render_aggregation_warnings(
                renderer, aggregated_portfolios)

        print()

    def _render_currency_group(
        self,
        renderer: ConsoleRenderer,
        aggregated: AggregatedPortfolio,
        show_currency_header: bool
    ):
        """
        Render single currency group aggregation.

        Args:
            renderer: ConsoleRenderer instance
            aggregated: Aggregated portfolio for this currency
            show_currency_header: Whether to show currency-specific header
        """
        if show_currency_header:
            print()
            scenario_names = ", ".join(aggregated.scenario_names)
            print(
                f"\n{renderer.bold(f'   💰 {aggregated.currency} GROUP ({aggregated.scenario_count} scenarios)')}")
            print(f"      Scenarios: {scenario_names}")

        self._render_aggregated_details(
            aggregated.portfolio_stats,
            aggregated.execution_stats,
            aggregated.cost_breakdown,
            aggregated.pending_stats,
            renderer
        )

    def _render_aggregated_details(
        self,
        portfolio_stats: AggregatedPortfolioStats,
        execution_stats: ExecutionStats,
        cost_breakdown: CostBreakdown,
        pending_stats: PendingOrderStats,
        renderer: ConsoleRenderer
    ):
        """Render detailed aggregated portfolio stats."""
        # Trading summary
        total_trades = portfolio_stats.total_trades
        winning_trades = portfolio_stats.winning_trades
        losing_trades = portfolio_stats.losing_trades
        long_trades = portfolio_stats.total_long_trades
        short_trades = portfolio_stats.total_short_trades
        win_rate = portfolio_stats.win_rate
        # min/max aggregated
        max_drawdown = portfolio_stats.max_drawdown
        max_drawdown_scenario = portfolio_stats.max_drawdown_scenario
        max_equity = portfolio_stats.max_equity
        max_equity_scenario = portfolio_stats.max_equity_scenario

        total_profit = portfolio_stats.total_profit
        total_loss = portfolio_stats.total_loss
        total_pnl = total_profit - total_loss

        print(f"\n{renderer.bold('   TRADING SUMMARY:')}")
        print(f"      Total Trades: {total_trades} (L/S: {long_trades}/{short_trades}) |  "
              f"Win/Loss: {winning_trades}W/{losing_trades}L  |  "
              f"Win Rate: {win_rate:.1%}")

        # Get currency from portfolio stats
        currency = portfolio_stats.currency

        pnl_str = renderer.pnl(total_pnl, currency)

        print(f"      Total P&L: {pnl_str}  |  "
              f"Profit: {renderer.pnl(force_positive(total_profit), currency)}  |  "
              f"Loss: {renderer.pnl(force_negative(total_loss), currency)}")

        # Profit factor
        profit_factor = portfolio_stats.profit_factor
        if profit_factor == float('inf'):
            pf_str = "∞ (no losses)"
        else:
            pf_str = f"{profit_factor:.2f}"
        print(f"      Profit Factor: {pf_str}")

        # Order execution
        orders_sent = execution_stats.orders_sent
        orders_executed = execution_stats.orders_executed
        orders_rejected = execution_stats.orders_rejected

        print(f"\n{renderer.bold('   ORDER EXECUTION:')}")
        print(f"      Orders Sent: {orders_sent}  |  "
              f"Executed: {orders_executed}  |  "
              f"Rejected: {orders_rejected}")

        if orders_sent > 0:
            exec_rate = orders_executed / orders_sent
            print(f"      Execution Rate: {exec_rate:.1%}")

        # Pending order statistics (green)
        self._render_pending_stats(renderer, pending_stats)

        # Cost breakdown
        spread_cost = cost_breakdown.total_spread_cost
        commission = cost_breakdown.total_commission
        swap = cost_breakdown.total_swap
        total_costs = spread_cost + commission + swap
        currency = cost_breakdown.currency

        print(f"\n{renderer.bold('   💸 COST BREAKDOWN:')}")
        print(f"      Spread Cost: {renderer.pnl(force_negative(spread_cost), currency)} |  "
              f"Commission: {renderer.pnl(force_negative(commission), currency)} |  "
              f"Swap: {renderer.pnl(force_negative(swap), currency)}")
        print(
            f"      Total Costs: {renderer.pnl(force_negative(total_costs), currency)}")

        # Risk Metrics
        max_dd_pct = 0.0
        if portfolio_stats.max_equity > 0:
            max_dd_pct = max_drawdown / \
                portfolio_stats.max_equity * 100

        print(f"\n   📉 RISK METRICS:")
        print(f"      Max Drawdown: {renderer.pnl(force_negative(max_drawdown), currency)} "
              f"({max_dd_pct:.1f}%) - Scenario: {max_drawdown_scenario}")
        print(f"      Max Equity: {renderer.pnl(force_positive(max_equity), currency)} "
              f"- Scenario: {max_equity_scenario}")

    @staticmethod
    def _render_pending_stats(
        renderer: ConsoleRenderer,
        pending_stats: PendingOrderStats
    ) -> None:
        """
        Render pending order statistics in ORDER EXECUTION section.

        Args:
            renderer: Console renderer for formatting
            pending_stats: Aggregated pending order statistics
        """
        if not pending_stats or pending_stats.total_resolved == 0:
            return

        # Pending resolved breakdown
        filled = pending_stats.total_filled
        rejected = pending_stats.total_rejected
        force_closed = pending_stats.total_force_closed
        timed_out = pending_stats.total_timed_out

        resolved_line = f"      Pending Resolved: {renderer.green(f'{filled} filled')}"
        if rejected > 0:
            resolved_line += f" | {rejected} rejected"
        if timed_out > 0:
            resolved_line += f" | {renderer.yellow(f'{timed_out} timed out')}"
        if force_closed > 0:
            resolved_line += f" | {renderer.yellow(f'{force_closed} force-closed')}"
        print(resolved_line)

        # Latency stats (tick-based or ms-based)
        if pending_stats.min_latency_ticks is not None:
            avg = pending_stats.avg_latency_ticks
            min_val = pending_stats.min_latency_ticks
            max_val = pending_stats.max_latency_ticks
            print(renderer.green(
                f"      Avg Latency: {avg:.1f} ticks (min: {min_val} | max: {max_val})"))

        elif pending_stats.min_latency_ms is not None:
            avg = pending_stats.avg_latency_ms
            min_val = pending_stats.min_latency_ms
            max_val = pending_stats.max_latency_ms
            print(renderer.green(
                f"      Avg Latency: {avg:.0f}ms (min: {min_val:.0f}ms | max: {max_val:.0f}ms)"))

    def _has_any_time_divergence(
        self,
        aggregated_portfolios: Dict[str, AggregatedPortfolio]
    ) -> bool:
        """
        Check if any currency group has time divergence warning.

        Args:
            aggregated_portfolios: Dict of aggregated portfolios

        Returns:
            True if any group has time divergence
        """
        return any(agg.has_time_divergence_warning for agg in aggregated_portfolios.values())

    def _render_aggregation_warnings(
        self,
        renderer: ConsoleRenderer,
        aggregated_portfolios: Dict[str, AggregatedPortfolio]
    ):
        """
        Render warnings about aggregation limitations.

        Args:
            renderer: ConsoleRenderer instance
            aggregated_portfolios: Dict of aggregated portfolios
        """
        has_multiple_currencies = len(aggregated_portfolios) > 1
        has_time_divergence = self._has_any_time_divergence(
            aggregated_portfolios)

        if not (has_multiple_currencies or has_time_divergence):
            return

        print()
        print(renderer.yellow("⚠️  AGGREGATION LIMITATIONS:"))
        print()

        if has_time_divergence:
            # Find groups with divergence
            divergent_groups = [
                (currency, agg) for currency, agg in aggregated_portfolios.items()
                if agg.has_time_divergence_warning
            ]

            print("   1. TIME DIVERGENCE:")
            for currency, agg in divergent_groups:
                print(
                    f"      {currency} Group: Scenarios span {agg.time_span_days} days")
            print("      Market conditions, volatility, and rates differ significantly.")
            print(
                "      Aggregated P&L is statistical only, not representative of portfolio performance.")
            print()

        if has_multiple_currencies:
            print("   2. MULTI-CURRENCY:")
            print(
                "      Cross-currency aggregation is not performed to avoid conversion errors.")
            print("      Each currency group shows accurate P&L in its own currency.")
            print(
                "      For portfolio-level P&L, implement real-time conversion (Post-MVP).")
            print()

        print("   3. RECOMMENDATION:")
        print("      Use currency-grouped reports above for accurate P&L values.")
