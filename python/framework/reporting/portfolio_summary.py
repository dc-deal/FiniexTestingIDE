"""
FiniexTestingIDE - Portfolio Summary
Trading and portfolio statistics rendering

REFACTORED (C#003):
- Uses ScenarioSetPerformanceManager instead of TradeSimulator
- Reads portfolio stats from ScenarioPerformanceStats
- Supports per-scenario AND aggregated portfolio display
- FULLY TYPED: Uses dataclass attributes instead of dict.get()

Rendered in BOX format matching scenario details.
"""

from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.types.trading_env_types import PortfolioStats, ExecutionStats, CostBreakdown


class PortfolioSummary:
    """
    Portfolio and trading statistics summary.

    REFACTORED (C#003):
    - Uses ScenarioSetPerformanceManager for portfolio data (per scenario + aggregated)
    - FULLY TYPED: Direct attribute access instead of dict.get()
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

        # Convert to dict format for renderer (renderer expects dicts)
        scenario_dicts = []
        for scenario in scenarios:
            scenario_dict = {
                'scenario_set_name': scenario.scenario_name,
                'portfolio_statistics': scenario.portfolio_stats,
                'execution_statistics': scenario.execution_stats,
                'cost_breakdown': scenario.cost_breakdown
            }
            scenario_dicts.append(scenario_dict)

        # Use grid renderer
        print()
        renderer.render_portfolio_grid(
            scenarios=scenario_dicts,
            columns=3,      # 3 boxes per row
            box_width=38    # Same width as scenario boxes
        )

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

        if aggregated_portfolio.total_trades == 0:
            return

        print()
        renderer.section_separator()
        renderer.print_bold("📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)")
        renderer.section_separator()

        self._render_aggregated_details(
            aggregated_portfolio,
            aggregated_execution,
            aggregated_costs,
            renderer
        )
        print()

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
        win_rate = portfolio_stats.win_rate

        total_profit = portfolio_stats.total_profit
        total_loss = portfolio_stats.total_loss
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

        print(f"\n{renderer.bold('   💸 COST BREAKDOWN:')}")
        print(f"      Spread Cost: ${spread_cost:.2f}  |  "
              f"Commission: ${commission:.2f}  |  "
              f"Swap: ${swap:.2f}")
        print(f"      Total Costs: ${total_costs:.2f}")

    def _aggregate_portfolio_stats(self, scenarios) -> PortfolioStats:
        """Aggregate portfolio stats from all scenarios."""
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_profit = 0.0
        total_loss = 0.0
        total_spread_cost = 0.0
        total_commission = 0.0
        total_swap = 0.0

        for scenario in scenarios:
            stats = scenario.portfolio_stats
            total_trades += stats.total_trades
            winning_trades += stats.winning_trades
            losing_trades += stats.losing_trades
            total_profit += stats.total_profit
            total_loss += stats.total_loss
            total_spread_cost += stats.total_spread_cost
            total_commission += stats.total_commission
            total_swap += stats.total_swap

        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else (
            0.0 if total_profit == 0 else float('inf'))

        return PortfolioStats(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_profit=total_profit,
            total_loss=total_loss,
            max_drawdown=0.0,  # Not aggregated
            max_equity=0.0,     # Not aggregated
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_spread_cost=total_spread_cost,
            total_commission=total_commission,
            total_swap=total_swap,
            total_fees=total_spread_cost + total_commission + total_swap
        )

    def _aggregate_execution_stats(self, scenarios) -> ExecutionStats:
        """Aggregate execution stats from all scenarios."""
        orders_sent = 0
        orders_executed = 0
        orders_rejected = 0
        total_commission = 0.0
        total_spread_cost = 0.0

        for scenario in scenarios:
            stats = scenario.execution_stats
            orders_sent += stats.orders_sent
            orders_executed += stats.orders_executed
            orders_rejected += stats.orders_rejected
            total_commission += stats.total_commission
            total_spread_cost += stats.total_spread_cost

        return ExecutionStats(
            orders_sent=orders_sent,
            orders_executed=orders_executed,
            orders_rejected=orders_rejected,
            total_commission=total_commission,
            total_spread_cost=total_spread_cost
        )

    def _aggregate_cost_breakdown(self, scenarios) -> CostBreakdown:
        """Aggregate cost breakdown from all scenarios."""
        total_spread_cost = 0.0
        total_commission = 0.0
        total_swap = 0.0

        for scenario in scenarios:
            costs = scenario.cost_breakdown
            total_spread_cost += costs.total_spread_cost
            total_commission += costs.total_commission
            total_swap += costs.total_swap

        return CostBreakdown(
            total_spread_cost=total_spread_cost,
            total_commission=total_commission,
            total_swap=total_swap,
            total_fees=total_spread_cost + total_commission + total_swap
        )
