# Lines 1-15 - Update imports and docstring
"""
FiniexTestingIDE - Portfolio Summary
Trading and portfolio statistics rendering

REFACTORED (C#003):
- Uses ScenarioSetPerformanceManager instead of TradeSimulator
- Reads portfolio stats from ScenarioPerformanceStats
- Supports per-scenario AND aggregated portfolio display

Rendered in BOX format matching scenario details.
"""

from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.trading_env.trade_simulator import TradeSimulator


class PortfolioSummary:
    """
    Portfolio and trading statistics summary.

    REFACTORED (C#003):
    - Uses ScenarioSetPerformanceManager for portfolio data (per scenario + aggregated)
    """

    def __init__(self, performance_log: ScenarioSetPerformanceManager):
        """
        Initialize portfolio summary.

        Args:
            performance_log: ScenarioSetPerformanceManager instance with portfolio stats
        """
        self.performance_log = performance_log

    def render_per_scenario(self, renderer):
        """
        Render portfolio stats per scenario in BOX format.

        Now reads from ScenarioPerformanceStats (each has own portfolio_stats).

        Args:
            renderer: ConsoleRenderer instance
        """
        scenarios = self.performance_log.get_all_scenarios()

        if not scenarios:
            print()
            print("   No scenarios available")
            print()
            return

        # Check if ANY scenario has trades
        any_trades = any(
            scenario.portfolio_stats.get('total_trades', 0) > 0
            for scenario in scenarios
        )

        if not any_trades:
            print()
            print("   No trades executed across all scenarios")
            print()
            return

        # Render each scenario's portfolio in BOX format
        print()
        for scenario in scenarios:
            portfolio_stats = scenario.portfolio_stats
            execution_stats = scenario.execution_stats
            cost_breakdown = scenario.cost_breakdown

            # Skip scenarios with no trades
            if portfolio_stats.get('total_trades', 0) == 0:
                continue

            self._render_scenario_box(
                scenario.scenario_name,
                portfolio_stats,
                execution_stats,
                cost_breakdown,
                renderer
            )
            print()

    def render_aggregated(self, renderer):
        """
        Render aggregated portfolio stats across ALL scenarios.

        Aggregates portfolio_stats from all ScenarioPerformanceStats.

        Args:
            renderer: ConsoleRenderer instance
        """
        scenarios = self.performance_log.get_all_scenarios()

        if not scenarios:
            return

        # Aggregate portfolio stats from all scenarios
        aggregated_portfolio = self._aggregate_portfolio_stats(scenarios)
        aggregated_execution = self._aggregate_execution_stats(scenarios)
        aggregated_costs = self._aggregate_cost_breakdown(scenarios)

        if aggregated_portfolio.get('total_trades', 0) == 0:
            return

        print()
        renderer.section_separator(width=120)
        renderer.print_bold("📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)")
        renderer.section_separator(width=120)

        self._render_aggregated_details(
            aggregated_portfolio,
            aggregated_execution,
            aggregated_costs,
            renderer
        )
        print()

    def _render_aggregated_box(self, portfolio_stats, execution_stats, cost_breakdown, renderer):
        """Render aggregated portfolio in BOX format."""
        total_trades = portfolio_stats.get('total_trades', 0)
        winning = portfolio_stats.get('winning_trades', 0)
        losing = portfolio_stats.get('losing_trades', 0)
        win_rate = portfolio_stats.get('win_rate', 0.0)

        total_profit = portfolio_stats.get('total_profit', 0.0)
        total_loss = portfolio_stats.get('total_loss', 0.0)
        total_pnl = total_profit - total_loss

        spread_cost = cost_breakdown.get('total_spread_cost', 0.0)
        orders_executed = execution_stats.get('orders_executed', 0)
        orders_sent = execution_stats.get('orders_sent', 0)

        # Format P&L with color
        if total_pnl >= 0:
            pnl_str = renderer.green(f"+${total_pnl:.2f}")
        else:
            pnl_str = renderer.red(f"${total_pnl:.2f}")

        # Create box
        lines = [
            "💰 All Scenarios",
            f"Trades: {total_trades} ({winning}W/{losing}L)",
            f"Win Rate: {win_rate:.1%}",
            f"P&L: {pnl_str}",
            f"Spread: ${spread_cost:.2f}",
            f"Orders: {orders_executed}/{orders_sent}"
        ]

        box_lines = renderer.render_box(lines, box_width=38)
        for line in box_lines:
            print(line)

    def _render_aggregated_details(self, portfolio_stats, execution_stats, cost_breakdown, renderer):
        """Render detailed aggregated portfolio stats."""
        # Trading summary
        total_trades = portfolio_stats.get('total_trades', 0)
        winning_trades = portfolio_stats.get('winning_trades', 0)
        losing_trades = portfolio_stats.get('losing_trades', 0)
        win_rate = portfolio_stats.get('win_rate', 0.0)

        total_profit = portfolio_stats.get('total_profit', 0.0)
        total_loss = portfolio_stats.get('total_loss', 0.0)
        total_pnl = total_profit - total_loss

        print(f"\n{renderer.bold('   📈 TRADING SUMMARY:')}")
        print(f"      Total Trades: {total_trades}  |  "
              f"Win/Loss: {winning_trades}W/{losing_trades}L  |  "
              f"Win Rate: {win_rate:.1%}")

        pnl_color = renderer.green if total_pnl >= 0 else renderer.red
        pnl_str = pnl_color(f"${total_pnl:.2f}")

        print(f"      Total P&L: {pnl_str}  |  "
              f"Profit: ${total_profit:.2f}  |  "
              f"Loss: ${total_loss:.2f}")

        # Profit factor
        profit_factor = portfolio_stats.get('profit_factor', 0.0)
        if profit_factor == float('inf'):
            pf_str = "∞ (no losses)"
        else:
            pf_str = f"{profit_factor:.2f}"
        print(f"      Profit Factor: {pf_str}")

        # Order execution
        orders_sent = execution_stats.get('orders_sent', 0)
        orders_executed = execution_stats.get('orders_executed', 0)
        orders_rejected = execution_stats.get('orders_rejected', 0)

        print(f"\n{renderer.bold('   📋 ORDER EXECUTION:')}")
        print(f"      Orders Sent: {orders_sent}  |  "
              f"Executed: {orders_executed}  |  "
              f"Rejected: {orders_rejected}")

        if orders_sent > 0:
            exec_rate = orders_executed / orders_sent
            print(f"      Execution Rate: {exec_rate:.1%}")

        # Cost breakdown
        spread_cost = cost_breakdown.get('total_spread_cost', 0.0)
        commission = cost_breakdown.get('total_commission', 0.0)
        swap = cost_breakdown.get('total_swap', 0.0)
        total_costs = spread_cost + commission + swap

        print(f"\n{renderer.bold('   💸 COST BREAKDOWN:')}")
        print(f"      Spread Cost: ${spread_cost:.2f}  |  "
              f"Commission: ${commission:.2f}  |  "
              f"Swap: ${swap:.2f}")
        print(f"      Total Costs: ${total_costs:.2f}")

    def _render_scenario_box(self, scenario_name, portfolio_stats, execution_stats, cost_breakdown, renderer):
        """Render portfolio box for single scenario."""
        total_trades = portfolio_stats.get('total_trades', 0)
        winning = portfolio_stats.get('winning_trades', 0)
        losing = portfolio_stats.get('losing_trades', 0)
        win_rate = portfolio_stats.get('win_rate', 0.0)

        total_profit = portfolio_stats.get('total_profit', 0.0)
        total_loss = portfolio_stats.get('total_loss', 0.0)
        total_pnl = total_profit - total_loss

        spread_cost = cost_breakdown.get('total_spread_cost', 0.0)
        orders_executed = execution_stats.get('orders_executed', 0)

        # Format P&L with color
        if total_pnl >= 0:
            pnl_str = renderer.green(f"+${total_pnl:.2f}")
        else:
            pnl_str = renderer.red(f"${total_pnl:.2f}")

        # Create box
        lines = [
            f"💰 {scenario_name[:26]}",
            f"Trades: {total_trades} ({winning}W/{losing}L)",
            f"Win Rate: {win_rate:.1%}",
            f"P&L: {pnl_str}",
            f"Spread: ${spread_cost:.2f}",
            f"Orders: {orders_executed}"
        ]

        box_lines = renderer.render_box(lines, box_width=38)
        for line in box_lines:
            print(line)

    def _aggregate_portfolio_stats(self, scenarios) -> dict:
        """Aggregate portfolio stats from all scenarios."""
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_profit = 0.0
        total_loss = 0.0

        for scenario in scenarios:
            stats = scenario.portfolio_stats
            total_trades += stats.get('total_trades', 0)
            winning_trades += stats.get('winning_trades', 0)
            losing_trades += stats.get('losing_trades', 0)
            total_profit += stats.get('total_profit', 0.0)
            total_loss += stats.get('total_loss', 0.0)

        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else 0.0

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'profit_factor': profit_factor
        }

    def _aggregate_execution_stats(self, scenarios) -> dict:
        """Aggregate execution stats from all scenarios."""
        orders_sent = 0
        orders_executed = 0
        orders_rejected = 0

        for scenario in scenarios:
            stats = scenario.execution_stats
            orders_sent += stats.get('orders_sent', 0)
            orders_executed += stats.get('orders_executed', 0)
            orders_rejected += stats.get('orders_rejected', 0)

        return {
            'orders_sent': orders_sent,
            'orders_executed': orders_executed,
            'orders_rejected': orders_rejected
        }

    def _aggregate_cost_breakdown(self, scenarios) -> dict:
        """Aggregate cost breakdown from all scenarios."""
        total_spread_cost = 0.0
        total_commission = 0.0
        total_swap = 0.0

        for scenario in scenarios:
            costs = scenario.cost_breakdown
            total_spread_cost += costs.get('total_spread_cost', 0.0)
            total_commission += costs.get('total_commission', 0.0)
            total_swap += costs.get('total_swap', 0.0)

        return {
            'total_spread_cost': total_spread_cost,
            'total_commission': total_commission,
            'total_swap': total_swap,
            'total_fees': total_spread_cost + total_commission + total_swap
        }
