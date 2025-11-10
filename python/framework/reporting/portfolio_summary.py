"""
FiniexTestingIDE - Portfolio Summary
Trading and portfolio statistics rendering

Rendered in BOX format matching scenario details.
"""

from typing import Dict

from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.process_data_types import BatchExecutionSummary, ProcessResult
from python.framework.types.trading_env_types import PortfolioStats, ExecutionStats, CostBreakdown
from python.framework.types.portfolio_aggregation_types import AggregatedPortfolio


class PortfolioSummary:
    """
    Portfolio and trading statistics summary.
    """

    def __init__(self, batch_execution_summary: BatchExecutionSummary):
        """
        Initialize portfolio summary.
        """
        self.batch_execution_summary = batch_execution_summary

    def render_per_scenario(self, renderer):
        """
        Render portfolio stats per scenario in BOX format.

        Args:
            renderer: ConsoleRenderer instance
        """
        scenarios = self.batch_execution_summary.scenario_list

        # Use grid renderer
        print()
        renderer.render_portfolio_grid(
            scenarios=scenarios,
            columns=3,      # 3 boxes per row
            box_width=38    # Same width as scenario boxes
        )

    def render_aggregated(self, renderer, aggregated_portfolios: Dict[str, AggregatedPortfolio]):
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
            self._render_aggregation_warnings(renderer, aggregated_portfolios)

        print()

    def _render_currency_group(
        self,
        renderer,
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
            scenario_list = ", ".join(aggregated.scenario_names)
            print(
                f"\n{renderer.bold(f'   💰 {aggregated.currency} GROUP ({aggregated.scenario_count} scenarios)')}")
            print(f"      Scenarios: {scenario_list}")

        self._render_aggregated_details(
            aggregated.portfolio_stats,
            aggregated.execution_stats,
            aggregated.cost_breakdown,
            renderer
        )

    def _render_aggregated_details(
        self,
        portfolio_stats: PortfolioStats,
        execution_stats: ExecutionStats,
        cost_breakdown: CostBreakdown,
        renderer
    ):
        """Render detailed aggregated portfolio stats."""
        # Trading summary
        total_trades = portfolio_stats.total_trades
        winning_trades = portfolio_stats.winning_trades
        losing_trades = portfolio_stats.losing_trades
        long_trades = portfolio_stats.total_long_trades
        short_trades = portfolio_stats.total_short_trades
        win_rate = portfolio_stats.win_rate

        total_profit = portfolio_stats.total_profit
        total_loss = portfolio_stats.total_loss
        total_pnl = total_profit - total_loss

        print(f"\n{renderer.bold('   📈 TRADING SUMMARY:')}")
        print(f"      Total Trades: {total_trades} (L/S: {long_trades}/{short_trades}) |  "
              f"Win/Loss: {winning_trades}W/{losing_trades}L  |  "
              f"Win Rate: {win_rate:.1%}")

        # Get currency from portfolio stats
        currency = portfolio_stats.currency

        pnl_color = renderer.green if total_pnl >= 0 else renderer.red
        pnl_str = pnl_color(format_currency_simple(total_pnl, currency))

        print(f"      Total P&L: {pnl_str}  |  "
              f"Profit: {format_currency_simple(total_profit, currency)}  |  "
              f"Loss: {format_currency_simple(total_loss, currency)}")

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

        print(f"\n{renderer.bold('   📋 ORDER EXECUTION:')}")
        print(f"      Orders Sent: {orders_sent}  |  "
              f"Executed: {orders_executed}  |  "
              f"Rejected: {orders_rejected}")

        if orders_sent > 0:
            exec_rate = orders_executed / orders_sent
            print(f"      Execution Rate: {exec_rate:.1%}")

        # Cost breakdown
        spread_cost = cost_breakdown.total_spread_cost
        commission = cost_breakdown.total_commission
        swap = cost_breakdown.total_swap
        total_costs = spread_cost + commission + swap
        currency = cost_breakdown.currency

        print(f"\n{renderer.bold('   💸 COST BREAKDOWN:')}")
        print(f"      Spread Cost: {format_currency_simple(spread_cost, currency)}  |  "
              f"Commission: {format_currency_simple(commission, currency)}  |  "
              f"Swap: {format_currency_simple(swap, currency)}")
        print(
            f"      Total Costs: {format_currency_simple(total_costs, currency)}")

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
        renderer,
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
