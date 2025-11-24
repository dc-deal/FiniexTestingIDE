"""
FiniexTestingIDE - Portfolio Aggregator
Currency-grouped portfolio aggregation with time divergence validation

Responsibilities:
- Group scenarios by account currency
- Aggregate portfolio/execution/cost stats per currency group
- Validate time divergence between scenarios
- Prevent incorrect cross-currency aggregation
"""

from typing import Dict, List
from datetime import datetime

from python.framework.types.process_data_types import ProcessResult
from python.framework.types.trading_env_types import (
    PortfolioStats,
    ExecutionStats,
    CostBreakdown
)
from python.framework.types.portfolio_aggregation_types import AggregatedPortfolio


class PortfolioAggregator:
    """
    Aggregates portfolio statistics grouped by currency.

    Prevents cross-currency aggregation errors by grouping scenarios
    with same account currency. Validates time divergence to warn
    users about unrealistic aggregations across large time periods.
    """

    # Time span threshold for divergence warning (days)
    DIVERGENCE_THRESHOLD_DAYS = 30

    def __init__(self, scenarios: List[ProcessResult]):
        """
        Initialize aggregator with scenarios.

        Args:
            scenarios: List of scenario results to aggregate
        """
        self.scenarios = scenarios

    def aggregate_by_currency(self) -> Dict[str, AggregatedPortfolio]:
        """
        Aggregate scenarios grouped by account currency.

        Groups scenarios by their account currency to prevent cross-currency
        conversion errors. Each currency group is aggregated separately.

        Returns:
            Dict mapping currency code to aggregated portfolio
            Example: {"USD": AggregatedPortfolio(...), "EUR": ...}
        """
        # Group scenarios by currency
        currency_groups: Dict[str, List[ProcessResult]] = {}

        for scenario in self.scenarios:
            if not scenario.tick_loop_results:
                continue
            currency = scenario.tick_loop_results.portfolio_stats.currency

            if currency not in currency_groups:
                currency_groups[currency] = []

            currency_groups[currency].append(scenario)

        # Aggregate each currency group
        result: Dict[str, AggregatedPortfolio] = {}

        for currency, group_scenarios in currency_groups.items():
            result[currency] = self._aggregate_group(group_scenarios, currency)

        return result

    def _aggregate_group(
        self,
        scenarios: List[ProcessResult],
        currency: str
    ) -> AggregatedPortfolio:
        """
        Aggregate a single currency group.

        Args:
            scenarios: Scenarios in this currency group
            currency: Account currency code

        Returns:
            Aggregated portfolio for this currency
        """
        portfolio_stats = self._aggregate_portfolio_stats(scenarios)
        execution_stats = self._aggregate_order_execution_stats(scenarios)
        cost_breakdown = self._aggregate_cost_breakdown(scenarios)

        time_span_days, has_divergence = self._validate_time_divergence(
            scenarios)

        return AggregatedPortfolio(
            currency=currency,
            scenario_names=[s.scenario_name for s in scenarios],
            scenario_count=len(scenarios),
            portfolio_stats=portfolio_stats,
            execution_stats=execution_stats,
            cost_breakdown=cost_breakdown,
            time_span_days=time_span_days,
            has_time_divergence_warning=has_divergence
        )

    def _aggregate_portfolio_stats(
        self,
        scenarios: List[ProcessResult]
    ) -> PortfolioStats:
        """
        Aggregate portfolio statistics from scenarios.

        Args:
            scenarios: Scenarios to aggregate

        Returns:
            Aggregated portfolio stats
        """
        total_trades = 0
        total_long_trades = 0
        total_short_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_profit = 0.0
        total_loss = 0.0
        total_spread_cost = 0.0
        total_commission = 0.0
        total_swap = 0.0

        for scenario in scenarios:
            stats = scenario.tick_loop_results.portfolio_stats
            total_trades += stats.total_trades
            total_long_trades += stats.total_long_trades
            total_short_trades += stats.total_short_trades
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

        unique_brokers = set(
            s.tick_loop_results.portfolio_stats.broker_name for s in scenarios)

        if len(unique_brokers) == 1:
            broker_name = list(unique_brokers)[0]
        else:
            first_broker = scenarios[0].tick_loop_results.portfolio_stats.broker_name
            broker_name = f"{first_broker} (+{len(unique_brokers)-1} more)"
        currency = scenarios[0].tick_loop_results.portfolio_stats.currency
        configured_currency = scenarios[0].tick_loop_results.portfolio_stats.configured_account_currency

        return PortfolioStats(
            total_trades=total_trades,
            total_long_trades=total_long_trades,
            total_short_trades=total_short_trades,
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
            total_fees=total_spread_cost + total_commission + total_swap,
            currency=currency,
            broker_name=broker_name,
            configured_account_currency=configured_currency,
            current_conversion_rate=None  # - Not meaningful in aggregation
        )

    def _aggregate_order_execution_stats(
        self,
        scenarios: List[ProcessResult]
    ) -> ExecutionStats:
        """
        Aggregate order execution statistics from scenarios.

        Args:
            scenarios: Scenarios to aggregate

        Returns:
            Aggregated execution stats
        """
        orders_sent = 0
        orders_executed = 0
        orders_rejected = 0
        total_commission = 0.0
        total_spread_cost = 0.0

        for scenario in scenarios:
            stats = scenario.tick_loop_results.execution_stats
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

    def _aggregate_cost_breakdown(
        self,
        scenarios: List[ProcessResult]
    ) -> CostBreakdown:
        """
        Aggregate cost breakdown from scenarios.

        Args:
            scenarios: Scenarios to aggregate

        Returns:
            Aggregated cost breakdown
        """
        total_spread_cost = 0.0
        total_commission = 0.0
        total_swap = 0.0

        for scenario in scenarios:
            costs = scenario.tick_loop_results.cost_breakdown
            total_spread_cost += costs.total_spread_cost
            total_commission += costs.total_commission
            total_swap += costs.total_swap

        return CostBreakdown(
            total_spread_cost=total_spread_cost,
            total_commission=total_commission,
            total_swap=total_swap,
            total_fees=total_spread_cost + total_commission + total_swap,
            currency=scenarios[0].tick_loop_results.cost_breakdown.currency
        )

    def _validate_time_divergence(
        self,
        scenarios: List[ProcessResult]
    ) -> tuple[int, bool]:
        """
        Validate time divergence between scenarios.

        Calculates the time span between earliest and latest tick across
        all scenarios. Issues warning if span exceeds threshold.

        Args:
            scenarios: Scenarios to validate

        Returns:
            Tuple of (time_span_days, has_divergence_warning)
        """
        all_dates: List[datetime] = []

        for scenario in scenarios:
            stats = scenario.tick_loop_results.tick_range_stats
            if stats.first_tick_time and stats.last_tick_time:
                all_dates.append(stats.first_tick_time)
                all_dates.append(stats.last_tick_time)

        if not all_dates:
            return 0, False

        min_date = min(all_dates)
        max_date = max(all_dates)
        time_span_days = (max_date - min_date).days

        has_divergence = time_span_days > self.DIVERGENCE_THRESHOLD_DAYS

        return time_span_days, has_divergence
