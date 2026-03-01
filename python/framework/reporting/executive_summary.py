"""
FiniexTestingIDE - Executive Summary
One-screen overview of batch execution results

Provides comprehensive summary:
- Execution results (success rate, timing with warmup/tickrun split)
- In-time performance (simulated market time)
- Real-time performance (tick processing speed)
- Portfolio aggregation (P&L, win rate, metrics)
- System resources
"""

import psutil
from typing import Dict
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.pending_order_stats_types import PendingOrderStats
from python.framework.types.stress_test_types import StressTestConfig
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.portfolio_aggregator import PortfolioAggregator
from python.framework.types.currency_codes import format_currency_simple


class ExecutiveSummary:
    """
    Generates executive summary for batch execution.

    Aggregates all key metrics into single-screen overview.
    Designed for quick performance assessment.
    """

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        app_config: AppConfigManager
    ):
        """
        Initialize executive summary.

        Args:
            batch_execution_summary: Batch execution results
            app_config: Application configuration
        """
        self._batch_summary = batch_execution_summary
        self._app_config = app_config

    def render(self, renderer: ConsoleRenderer):
        """
        Render executive summary sections.

        Args:
            renderer: Console renderer for formatting
        """
        self._render_stress_test_warning(renderer)
        self._render_execution_results(renderer)
        print()
        self._render_data_sources(renderer)
        print()
        self._render_time_performance(renderer)
        print()
        self._render_portfolio_performance(renderer)
        print()
        self._render_system_resources(renderer)

    def _render_stress_test_warning(self, renderer: ConsoleRenderer):
        """
        Render prominent stress test warning if any scenario has active stress tests.

        Args:
            renderer: Console renderer for formatting
        """
        scenarios = self._batch_summary.single_scenario_list

        # Group scenarios by stress test config signature for compact output
        config_groups: dict[str, list[str]] = {}
        for scenario in scenarios:
            config = StressTestConfig.from_dict(scenario.stress_test_config)
            if not config.has_any_enabled():
                continue
            # Build config signature string
            parts = []
            if config.reject_open_order and config.reject_open_order.enabled:
                ro = config.reject_open_order
                parts.append(
                    f"reject_open_order: probability={ro.probability:.0%}, seed={ro.seed}")
            signature = " | ".join(parts)
            if signature not in config_groups:
                config_groups[signature] = []
            config_groups[signature].append(scenario.name)

        if not config_groups:
            return

        # Build warning block
        renderer.print_bold(renderer.red("⚠️  STRESS TEST ACTIVE ⚠️"))
        renderer.print_separator(width=68)
        print(renderer.red(
            "Results are AFFECTED by stress test injection!"))
        print(renderer.red(
            "Errors and rejections may be INTENTIONAL."))
        print()

        for signature, scenario_names in config_groups.items():
            print(renderer.yellow(f"  → {signature}"))
            print(renderer.yellow(
                f"    Scenarios ({len(scenario_names)}): {', '.join(scenario_names)}"))

        print()

    def _render_execution_results(self, renderer: ConsoleRenderer):
        """Render execution results section."""
        process_results = self._batch_summary.process_result_list
        scenarios = self._batch_summary.single_scenario_list

        total_scenarios = len(scenarios)
        successful = sum(1 for r in process_results if r.success)
        failed = total_scenarios - successful
        success_rate = (successful / total_scenarios *
                        100) if total_scenarios > 0 else 0

        # Get timing breakdown
        batch_time = self._batch_summary.batch_execution_time
        warmup_time = self._batch_summary.batch_warmup_time
        tickrun_time = self._batch_summary.batch_tickrun_time

        # Get execution config
        parallel = self._app_config.get_default_parallel_scenarios()
        max_workers = self._app_config.get_default_max_parallel_scenarios()

        # Calculate status
        if failed == 0:
            status_str = renderer.green("✅ Complete Success")
        elif successful == 0:
            status_str = renderer.red("❌ Complete Failure")
        else:
            status_str = renderer.yellow(
                f"⚠️ Partial ({successful}/{total_scenarios})")

        # Count disabled scenarios
        disabled = sum(1 for s in scenarios if hasattr(
            s, 'enabled') and not s.enabled)

        # Get first failed scenario info
        first_failed = next(
            (r for r in process_results if not r.success), None)

        # Render section
        renderer.print_bold("EXECUTION RESULTS")
        renderer.print_separator(width=68)
        print(f"Scenarios:          {total_scenarios} executed" +
              (f" ({disabled} disabled)" if disabled > 0 else ""))
        print(
            f"Success Rate:       {success_rate:.1f}% ({successful}/{total_scenarios} successful)")
        print(f"Status:             {status_str}")

        # Show first failed scenario details
        if first_failed:
            self._render_first_failure(renderer, first_failed, failed)

        # Batch time with warmup/tickrun split
        if warmup_time > 0:
            print(
                f"Batch Time:         {batch_time:.1f}s (warmup: {warmup_time:.1f}s | tickrun: {tickrun_time:.1f}s)")
        else:
            # Mounted mode - no warmup
            print(f"Batch Time:         {batch_time:.1f}s")

        print(f"Mode:               {'Parallel' if parallel else 'Sequential'}" +
              (f" (max {max_workers} workers)" if parallel else ""))

    def _render_data_sources(self, renderer: ConsoleRenderer):
        """Render data sources summary section."""
        scenarios = self._batch_summary.single_scenario_list

        # Aggregate by data_broker_type
        broker_type_stats = {}
        for scenario in scenarios:
            bt = scenario.data_broker_type
            if bt not in broker_type_stats:
                broker_type_stats[bt] = {
                    'count': 0,
                    'symbols': set()
                }
            broker_type_stats[bt]['count'] += 1
            broker_type_stats[bt]['symbols'].add(scenario.symbol)

        # Render section
        renderer.print_bold("DATA SOURCES")
        renderer.print_separator(width=68)

        market_config = MarketConfigManager()

        for broker_type, stats in sorted(broker_type_stats.items()):
            symbols_str = ', '.join(sorted(stats['symbols']))
            # Truncate if too long
            if len(symbols_str) > 40:
                symbols_str = symbols_str[:37] + "..."

            market_type = market_config.get_market_type(broker_type).value
            print(
                f"{broker_type} [{market_type}]".ljust(24) +
                f"{stats['count']} scenario(s) ({symbols_str})")

    def _render_first_failure(self, renderer: ConsoleRenderer, failed_result, total_failed: int):
        """
        Render first failed scenario details.

        Args:
            renderer: Console renderer
            failed_result: First failed ProcessResult
            total_failed: Total count of failed scenarios
        """
        scenario_name = failed_result.scenario_name
        error_msg = failed_result.error_message or "No error message available"

        # Extract first meaningful error line
        lines = [line.strip()
                 for line in error_msg.split('\n') if line.strip()]
        error_lines = [
            line for line in lines
            if not (line.startswith("Scenario '") and "failed validation:" in line)
        ]

        first_error = error_lines[0] if error_lines else "Unknown error"
        remaining_errors = len(error_lines) - 1
        remaining_scenarios = total_failed - 1

        # Truncate error if too long
        max_len = 80
        if len(first_error) > max_len:
            first_error = first_error[:max_len - 3] + "..."

        # Add suffixes
        error_suffix = f" (+ {remaining_errors} more)" if remaining_errors > 0 else ""
        scenario_suffix = f" (+ {remaining_scenarios} more)" if remaining_scenarios > 0 else ""

        # Color highlights for errors
        print(
            f"{renderer.red('Failed Scenario:    ')}{scenario_name}{scenario_suffix}")
        print(f"{renderer.red('Error:              ')}{first_error}{error_suffix}")

    def _render_time_performance(self, renderer: ConsoleRenderer):
        """Render in-time and real-time performance sections."""
        # Calculate in-time statsf
        in_time_stats = self._calculate_in_time_stats()

        # Calculate real-time stats using ONLY tick run time
        total_ticks = sum(
            r.tick_loop_results.coordination_statistics.ticks_processed
            for r in self._batch_summary.process_result_list
            if r.tick_loop_results and r.tick_loop_results.coordination_statistics
        )

        # Use tick run time (excludes warmup)
        tickrun_time = self._batch_summary.batch_tickrun_time
        ticks_per_second = total_ticks / tickrun_time if tickrun_time > 0 else 0
        speedup = (in_time_stats['total_hours'] * 3600) / \
            tickrun_time if tickrun_time > 0 else 0

        # Render IN-TIME section
        renderer.print_bold("IN-TIME PERFORMANCE (Simulated Market Time)")
        renderer.print_separator(width=68)
        print(
            f"Total Simulation:   {in_time_stats['total_hours']:.1f} hours ({in_time_stats['total_days']:.1f} days)")
        print(f"Avg per Scenario:   {in_time_stats['avg_hours']:.2f} hours")
        print(f"Ticks Processed:    {total_ticks:,} total")
        print(
            f"Ticks/Hour:         {in_time_stats['ticks_per_hour']:,.0f} (market density)")

        print()

        # Render REAL-TIME section (tick processing speed)
        renderer.print_bold("REAL-TIME PERFORMANCE (Tick Processing Speed)")
        renderer.print_separator(width=68)
        print(f"Tick Run Time:      {tickrun_time:.1f} seconds")
        print(
            f"Ticks/Second:       {ticks_per_second:,.0f} (processing rate)")
        print(
            f"Speedup:            {speedup:,.0f}x ({in_time_stats['total_hours']:.0f} hours → {tickrun_time:.0f} seconds)")

    def _render_portfolio_performance(self, renderer: ConsoleRenderer):
        """Render aggregated portfolio performance."""
        # Aggregate portfolios by currency
        aggregator = PortfolioAggregator(
            self._batch_summary.process_result_list)
        aggregated = aggregator.aggregate_by_currency()

        if not aggregated:
            renderer.print_bold("PORTFOLIO PERFORMANCE")
            renderer.print_separator(width=68)
            print("No portfolio data available")
            return

        # Render each currency
        for currency, agg_portfolio in aggregated.items():
            self._render_currency_portfolio(
                renderer, currency, agg_portfolio)

    def _render_currency_portfolio(self, renderer: ConsoleRenderer, currency: str, agg_portfolio):
        """
        Render portfolio metrics for single currency.

        Args:
            renderer: Console renderer
            currency: Currency code (EUR, USD, etc.)
            agg_portfolio: Aggregated portfolio statistics
        """
        # Get all ProcessResults for this currency
        currency_results = [
            r for r in self._batch_summary.process_result_list
            if r.tick_loop_results and
            r.tick_loop_results.portfolio_stats.currency == currency
        ]

        # Calculate initial and final balances from ProcessResults
        initial = sum(
            r.tick_loop_results.portfolio_stats.initial_balance
            for r in currency_results
        )
        final = sum(
            r.tick_loop_results.portfolio_stats.current_balance
            for r in currency_results
        )
        pnl = final - initial
        pnl_pct = (pnl / initial * 100) if initial > 0 else 0

        # Get metrics from aggregated portfolio stats
        portfolio_stats = agg_portfolio.portfolio_stats
        total_trades = portfolio_stats.total_trades
        winning = portfolio_stats.winning_trades
        losing = portfolio_stats.losing_trades
        win_rate = portfolio_stats.win_rate

        # Avg win/loss size
        avg_win = portfolio_stats.total_profit / winning if winning > 0 else 0
        avg_loss = abs(portfolio_stats.total_loss) / \
            losing if losing > 0 else 0

        # Profit factor
        profit_factor = portfolio_stats.total_profit / \
            abs(portfolio_stats.total_loss) if portfolio_stats.total_loss != 0 else 0

        # Recovery factor
        recovery_factor = pnl / \
            abs(portfolio_stats.max_drawdown) if portfolio_stats.max_drawdown != 0 else 0

        # Max drawdown percentage
        max_dd_pct = (portfolio_stats.max_drawdown / portfolio_stats.max_equity *
                      100) if portfolio_stats.max_equity > 0 else 0

        # print(f"DEBUG: portfolio_stats.win_rate = {portfolio_stats.win_rate}")
        # print(f"DEBUG: win_rate variable = {win_rate}")

        # Calculate scenario count and avg initial
        scenario_count = agg_portfolio.scenario_count
        avg_initial = initial / scenario_count if scenario_count > 0 else 0

        avg_spread = portfolio_stats.total_spread_cost / \
            total_trades if total_trades > 0 else 0

        # Render section
        renderer.print_bold(f"PORTFOLIO PERFORMANCE ({currency})")
        renderer.print_separator(width=68)
        print(f"Scenarios:          {scenario_count}")
        print(
            f"Initial Capital:    {format_currency_simple(initial, currency)} (avg {format_currency_simple(avg_initial, currency)}/scenario)")
        print(f"Final Balance:      {format_currency_simple(final, currency)}")

        pnl_str = renderer.pnl(pnl, currency)
        print(f"Total P&L:          {pnl_str} ({pnl_pct:+.2f}%)")
        # Order execution stats
        exec_stats = agg_portfolio.execution_stats
        orders_sent = exec_stats.orders_sent if exec_stats else 0
        orders_executed = exec_stats.orders_executed if exec_stats else 0
        orders_rejected = exec_stats.orders_rejected if exec_stats else 0
        exec_rate = (orders_executed / orders_sent *
                     100) if orders_sent > 0 else 0

        print("")
        print(f"Total Trades:       {total_trades} ({winning}W / {losing}L)")
        print(f"Win Rate:           {win_rate * 100:.1f}%")
        print(
            f"Avg Win:            {format_currency_simple(avg_win, currency)}")
        print(
            f"Avg Loss:           {format_currency_simple(avg_loss, currency)}")
        print(f"Profit Factor:      {profit_factor:.2f}")

        if orders_rejected > 0:
            print(
                f"Orders:             {orders_executed}/{orders_sent} executed | "
                f"{renderer.yellow(f'{orders_rejected} rejected')} ({exec_rate:.1f}%)")
        else:
            print(
                f"Orders:             {orders_executed}/{orders_sent} executed ({exec_rate:.1f}%)")

        # Pending order latency (green)
        pending_stats = agg_portfolio.pending_stats
        if pending_stats and pending_stats.total_resolved > 0:
            latency_line = self._format_pending_latency(
                renderer, pending_stats)
            if latency_line:
                print(latency_line)

        print("")
        print(
            f"Max Drawdown:       {format_currency_simple(abs(portfolio_stats.max_drawdown), currency)} ({max_dd_pct:.1f}%)")
        print(
            f"Max Equity:         {format_currency_simple(portfolio_stats.max_equity, currency)}")
        print(f"Recovery Factor:    {recovery_factor:.2f}")
        print("")
        print(
            f"Spread Cost:        {format_currency_simple(portfolio_stats.total_spread_cost, currency)} (avg {format_currency_simple(avg_spread, currency)}/trade)")
        print(
            f"Commission:         {format_currency_simple(portfolio_stats.total_commission, currency)}")
        print(
            f"Swap:               {format_currency_simple(portfolio_stats.total_swap, currency)}")

    @staticmethod
    def _format_pending_latency(renderer: ConsoleRenderer, pending_stats) -> str:
        """
        Format pending latency line for executive summary.

        Args:
            renderer: Console renderer for color formatting
            pending_stats: PendingOrderStats with latency metrics

        Returns:
            Formatted latency line (green) or empty string
        """
        # Tick-based latency (simulation)
        if pending_stats.min_latency_ticks is not None:
            avg = pending_stats.avg_latency_ticks
            min_val = pending_stats.min_latency_ticks
            max_val = pending_stats.max_latency_ticks
            line = f"Avg Latency:        {avg:.1f} ticks (min: {min_val} | max: {max_val})"
            line += ExecutiveSummary._format_anomaly_suffix_full(
                renderer, pending_stats)
            return renderer.green(line)

        # Time-based latency (live)
        if pending_stats.min_latency_ms is not None:
            avg = pending_stats.avg_latency_ms
            min_val = pending_stats.min_latency_ms
            max_val = pending_stats.max_latency_ms
            line = f"Avg Latency:        {avg:.0f}ms (min: {min_val:.0f}ms | max: {max_val:.0f}ms)"
            line += ExecutiveSummary._format_anomaly_suffix_full(
                renderer, pending_stats)
            return renderer.green(line)

        return ""

    @staticmethod
    def _format_anomaly_suffix_full(
        renderer: ConsoleRenderer,
        pending_stats: PendingOrderStats
    ) -> str:
        """Format full anomaly + active order suffix for executive summary."""
        result = ""
        # Anomalies (yellow)
        anomaly_parts = []
        if pending_stats.total_force_closed > 0:
            anomaly_parts.append(
                f"{pending_stats.total_force_closed} force-closed")
        if pending_stats.total_timed_out > 0:
            anomaly_parts.append(f"{pending_stats.total_timed_out} timed out")
        if anomaly_parts:
            result += f" | {renderer.yellow(' | '.join(anomaly_parts))}"
        # Active orders at scenario end (cyan)
        active_parts = []
        if pending_stats.active_limit_orders:
            active_parts.append(
                f"{len(pending_stats.active_limit_orders)} limits")
        if pending_stats.active_stop_orders:
            active_parts.append(
                f"{len(pending_stats.active_stop_orders)} stops")
        if active_parts:
            result += f" | {renderer.cyan(' | '.join(active_parts))}"
        return result

    def _render_system_resources(self, renderer: ConsoleRenderer):
        """Render system resources section."""
        try:
            cpu_count = psutil.cpu_count()
            mem = psutil.virtual_memory()
            ram_total_gb = mem.total / (1024**3)
            ram_available_gb = mem.available / (1024**3)
        except:
            cpu_count = "N/A"
            ram_total_gb = 0
            ram_available_gb = 0

        renderer.print_bold("SYSTEM RESOURCES")
        renderer.print_separator(width=68)
        print(f"CPU Cores:          {cpu_count}")
        print(
            f"RAM:                {ram_available_gb:.1f} GB available / {ram_total_gb:.1f} GB total")

    def _calculate_in_time_stats(self) -> Dict[str, float]:
        """
        Calculate in-time statistics from scenarios.

        Returns:
            Dict with total_hours, avg_hours, total_days, ticks_per_hour
        """
        scenarios = self._batch_summary.single_scenario_list

        total_hours = 0.0
        for scenario in scenarios:
            if scenario.end_date and scenario.start_date:
                duration = scenario.end_date - scenario.start_date
                total_hours += duration.total_seconds() / 3600

        scenario_count = len(scenarios)
        avg_hours = total_hours / scenario_count if scenario_count > 0 else 0
        total_days = total_hours / 24

        # Calculate ticks per hour
        total_ticks = sum(
            r.tick_loop_results.coordination_statistics.ticks_processed
            for r in self._batch_summary.process_result_list
            if r.tick_loop_results and r.tick_loop_results.coordination_statistics
        )
        ticks_per_hour = total_ticks / total_hours if total_hours > 0 else 0

        return {
            'total_hours': total_hours,
            'avg_hours': avg_hours,
            'total_days': total_days,
            'ticks_per_hour': ticks_per_hour
        }
