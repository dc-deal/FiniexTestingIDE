"""
FiniexTestingIDE - Portfolio Summary
Trading and portfolio statistics rendering

REFACTORED (C#003):
- Uses TradeSimulator directly instead of batch_results dict
- Accesses portfolio stats, execution stats, cost breakdown from TradeSimulator

Rendered in BOX format matching scenario details.
"""

from python.framework.trading_env.trade_simulator import TradeSimulator


class PortfolioSummary:
    """
    Portfolio and trading statistics summary.

    REFACTORED (C#003):
    - Uses TradeSimulator directly for portfolio data
    """

    def __init__(self, trade_simulator: TradeSimulator):
        """
        Initialize portfolio summary.

        Args:
            trade_simulator: TradeSimulator instance
        """
        self.trade_simulator = trade_simulator

    def render_per_scenario(self, renderer):
        """
        Render portfolio stats per scenario in BOX format.

        NOTE: Currently shows aggregated stats from TradeSimulator.
        Per-scenario stats would require scenario-specific TradeSimulator instances.

        Args:
            renderer: ConsoleRenderer instance
        """
        # Get aggregated portfolio stats from TradeSimulator
        portfolio_stats = self.trade_simulator.get_portfolio_stats()
        execution_stats = self.trade_simulator.get_execution_stats()
        cost_breakdown = self.trade_simulator.get_cost_breakdown()

        total_trades = portfolio_stats.get('total_trades', 0)

        if total_trades == 0:
            print()
            print("   No trades executed across all scenarios")
            print()
            return

        # Render single aggregated box
        print()
        self._render_aggregated_box(
            portfolio_stats,
            execution_stats,
            cost_breakdown,
            renderer
        )
        print()

    def render_aggregated(self, renderer):
        """
        Render aggregated portfolio stats.

        Args:
            renderer: ConsoleRenderer instance
        """
        portfolio_stats = self.trade_simulator.get_portfolio_stats()

        if portfolio_stats.get('total_trades', 0) == 0:
            return

        execution_stats = self.trade_simulator.get_execution_stats()
        cost_breakdown = self.trade_simulator.get_cost_breakdown()

        print()
        renderer.section_separator(width=120)
        renderer.print_bold("📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)")
        renderer.section_separator(width=120)

        self._render_aggregated_details(
            portfolio_stats,
            execution_stats,
            cost_breakdown,
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
