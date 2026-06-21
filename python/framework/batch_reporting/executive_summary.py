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
from typing import Dict, List, Optional
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import (
    AggregatedPortfolioCurrency, AggregatedPortfolioReport, AggregatedPortfolioRow,
    ProfilingReport, RunMetaReport, RunSummary, ScenarioDetailsReport, WarningsErrorsOutcome,
    WarningsErrorsReport)
from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.trading_env_types.currency_codes import format_currency_simple


class ExecutiveSummary(AbstractBatchSummarySection):
    """
    Generates executive summary for batch execution.

    Aggregates all key metrics into single-screen overview.
    Designed for quick performance assessment.
    """

    _section_title = '🎯 EXECUTIVE SUMMARY'

    def __init__(
        self,
        app_config: AppConfigManager,
        run_summary: RunSummary,
        run_meta_report: RunMetaReport,
        profiling_report: ProfilingReport,
        scenario_details_report: ScenarioDetailsReport,
        warnings_errors_report: WarningsErrorsReport,
        aggregated_report: AggregatedPortfolioReport,
        generator_profiles: Optional[List[GeneratorProfile]] = None
    ):
        """
        Initialize executive summary.

        Args:
            app_config: Application configuration
            run_summary: Cross-section run KPIs (the model-fed headline source)
            run_meta_report: Run-level meta (scenario identity + timing split) — the run-level
                values read from the model instead of the raw batch summary
            warnings_errors_report: Warnings/errors model — the failed-scenario headline reads its
                outcome (no inline re-scan of process results, #395)
            generator_profiles: Generator profiles for Profile Run source info (None for normal runs)
        """
        self._app_config = app_config
        self._run_summary = run_summary
        self._run_meta = run_meta_report
        self._profiling = profiling_report
        self._scenario_details = scenario_details_report
        self._outcome = warnings_errors_report.outcome
        self._aggregated_report = aggregated_report
        self._generator_profiles = generator_profiles

    def render(self, renderer: ConsoleRenderer):
        """
        Render executive summary sections.

        Args:
            renderer: Console renderer for formatting
        """
        self._render_section_header(renderer)

        self._render_run_summary(renderer)
        print()
        self._render_execution_results(renderer)
        print()
        self._render_data_sources(renderer)
        print()
        self._render_time_performance(renderer)
        print()
        self._render_portfolio_performance(renderer)
        print()
        self._render_system_resources(renderer)

    def _render_run_summary(self, renderer: ConsoleRenderer):
        """
        Render the cross-section run KPIs straight from the RunSummary model (#390/#393).

        The model-fed headline: per-currency P&L / win rate / profit factor / expectancy /
        fees + the global order counts — composed once in the pipeline, never re-derived here.
        """
        summary = self._run_summary

        renderer.print_bold("RUN SUMMARY")
        renderer.print_separator(width=68)
        print(f"Scenarios:          {summary.unit_count}")

        exec_rate = (summary.orders_executed / summary.orders_sent *
                     100) if summary.orders_sent > 0 else 0.0
        orders_line = (f"Orders:             {summary.orders_executed}/{summary.orders_sent} "
                       f"executed ({exec_rate:.1f}%)")
        if summary.orders_rejected > 0:
            orders_line += f" | {renderer.yellow(f'{summary.orders_rejected} rejected')}"
        if summary.sl_tp_triggered > 0:
            orders_line += f" | {summary.sl_tp_triggered} SL/TP"
        print(orders_line)

        if not summary.currencies:
            return

        print("")
        for c in summary.currencies:
            # Expectancy (mean R) is only meaningful with stop-loss trades.
            exp_str = f"{c.expectancy:+.2f}R" if c.r_trade_count > 0 else "n/a"
            print(
                f"{c.currency}:".ljust(20)
                + f"P&L {renderer.pnl(c.net_pnl, c.currency)} | "
                + f"Win {c.win_rate * 100:.1f}% ({c.winning_trades}W/{c.losing_trades}L) | "
                + f"PF {c.profit_factor:.2f} | "
                + f"Exp {exp_str} | "
                + f"Fees {format_currency_simple(c.total_fees, c.currency)}"
            )

    def _render_execution_results(self, renderer: ConsoleRenderer):
        """Render execution results section."""
        total_scenarios = self._run_meta.scenario_count
        # Failure truth from the warnings/errors model (no inline re-scan, #395)
        failed = self._outcome.failed_count
        successful = total_scenarios - failed
        success_rate = (successful / total_scenarios *
                        100) if total_scenarios > 0 else 0

        # Get timing breakdown (run-level, from the meta model)
        batch_time = self._run_meta.execution_time_s
        warmup_time = self._run_meta.warmup_time_s
        tickrun_time = self._run_meta.tickrun_time_s

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

        # Count disabled scenarios (run-level, from the meta model)
        disabled = self._run_meta.disabled_count

        # Render section
        renderer.print_bold("EXECUTION RESULTS")
        renderer.print_separator(width=68)
        print(f"Scenarios:          {total_scenarios} executed" +
              (f" ({disabled} disabled)" if disabled > 0 else ""))
        print(
            f"Success Rate:       {success_rate:.1f}% ({successful}/{total_scenarios} successful)")
        print(f"Status:             {status_str}")

        # Show first failed scenario details (from the model outcome)
        if failed > 0:
            self._render_first_failure(renderer)

        # Batch time with warmup/tickrun split
        if warmup_time > 0:
            print(
                f"Batch Time:         {batch_time:.1f}s (warmup: {warmup_time:.1f}s | tickrun: {tickrun_time:.1f}s)")
        else:
            # Mounted mode - no warmup
            print(f"Batch Time:         {batch_time:.1f}s")

        # Warmup hotspot one-liner (always shown when warmup phases available)
        warmup_hotspot = self._format_warmup_hotspot(renderer)
        if warmup_hotspot:
            print(f"Warmup Hotspot:     {warmup_hotspot}")

        # Mode line carries the run-quality tag (recorded at execution time):
        # PRODUCTION (real subprocesses, timings representative) vs DEBUG (serial
        # under a debugger / DEBUG_MODE, timings not representative).
        if self._run_meta.debug_execution:
            mode_str = 'Sequential ' + renderer.yellow(
                '🐞 DEBUG — timings not representative')
        else:
            mode_str = 'Parallel' if parallel else 'Sequential'
            if parallel:
                mode_str += f" (max {max_workers} workers)"
            mode_str += f" {renderer.green('— PRODUCTION')}"
        print(f"Mode:               {mode_str}")

        # Source line (Profile Run vs Scenario Set)
        if self._generator_profiles:
            modes = sorted(set(p.profile_meta.generator_mode for p in self._generator_profiles))
            mode_str = ', '.join(modes)
            print(f"Source:             Profile Run ({len(self._generator_profiles)} profile(s), {mode_str})")
        else:
            print(f"Source:             Scenario Set")

        # Performance tracking status (#137) — surface when at least one
        # tracking layer is off so the user knows why summary sections are missing
        tracking_line = self._format_tracking_status_line(renderer)
        if tracking_line:
            print(tracking_line)

    def _render_data_sources(self, renderer: ConsoleRenderer):
        """Render data sources summary section."""
        # Aggregate by data broker type — from the scenario-details model (one row per scenario)
        broker_type_stats = {}
        for unit in self._scenario_details.units:
            bt = unit.data_source
            if bt not in broker_type_stats:
                broker_type_stats[bt] = {
                    'count': 0,
                    'symbols': set()
                }
            broker_type_stats[bt]['count'] += 1
            broker_type_stats[bt]['symbols'].add(unit.symbol)

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

    def _render_first_failure(self, renderer: ConsoleRenderer):
        """
        Render first failed scenario details from the model outcome (#395).

        Args:
            renderer: Console renderer
        """
        scenario_name = self._outcome.first_failure_name
        total_failed = self._outcome.failed_count
        error_msg = self._outcome.first_failure_error or "No error message available"

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
        agg = self._profiling.aggregate
        # Algo ticks (non-clipped, coordination) — what the algo path actually processed
        algo_ticks = sum(u.ticks_processed for u in self._scenario_details.units)

        # Total ticks (including clipped) — what the loop actually iterated (0 = no clipping)
        ticks_total = agg.clipping_total_ticks
        # Fall back to algo_ticks when no clipping active (ticks_total=0)
        loop_ticks = ticks_total if ticks_total > 0 else algo_ticks
        has_clipping = ticks_total > 0 and ticks_total != algo_ticks

        # Use tick run time (excludes warmup)
        tickrun_time = self._run_meta.tickrun_time_s
        ticks_per_second = loop_ticks / tickrun_time if tickrun_time > 0 else 0
        speedup = (in_time_stats['total_hours'] * 3600) / \
            tickrun_time if tickrun_time > 0 else 0

        # Render IN-TIME section
        renderer.print_bold("IN-TIME PERFORMANCE (Simulated Market Time)")
        renderer.print_separator(width=68)
        print(
            f"Total Simulation:   {in_time_stats['total_hours']:.1f} hours ({in_time_stats['total_days']:.1f} days)")
        print(f"Avg per Scenario:   {in_time_stats['avg_hours']:.2f} hours")
        if has_clipping:
            print(f"Ticks Processed:    {loop_ticks:,} total ({algo_ticks:,} algo)")
        else:
            print(f"Ticks Processed:    {algo_ticks:,} total")

        # Tick budget one-liner (only when clipping was active)
        if agg.budget_active:
            total_clipped = agg.clipping_total_clipped
            total_original = agg.clipping_total_ticks
            budget_str = '/'.join(f'{b}ms' for b in agg.clipping_budgets)
            if total_clipped > 0:
                rate = total_clipped / total_original * 100 if total_original > 0 else 0
                print(
                    f"Tick Budget:        {budget_str} "
                    f"({total_clipped:,} clipped = {rate:.1f}%)")
            else:
                print(f"Tick Budget:        {budget_str} (no ticks clipped)")

        print(
            f"Ticks/Hour:         {in_time_stats['ticks_per_hour']:,.0f} (market density)")

        print()

        # Render REAL-TIME section (tick processing speed)
        renderer.print_bold("REAL-TIME PERFORMANCE (Tick Processing Speed)")
        renderer.print_separator(width=68)
        pickle_time = self._run_meta.pickle_time_s
        pickle_mb = self._run_meta.pickle_sample_mb
        if pickle_time > 0.0:
            execution_time = tickrun_time - pickle_time
            mb_str = f" ~{pickle_mb:.1f} MB/scenario" if pickle_mb > 0.0 else ''
            print(
                f"Tick Run Time:      {tickrun_time:.1f} seconds "
                f"(pickle: {pickle_time:.1f}s{mb_str} | execution: {execution_time:.1f}s)"
            )
        else:
            print(f"Tick Run Time:      {tickrun_time:.1f} seconds")
        print(
            f"Ticks/Second:       {ticks_per_second:,.0f} (processing rate)")
        print(
            f"Speedup:            {speedup:,.0f}x ({in_time_stats['total_hours']:.0f} hours → {tickrun_time:.0f} seconds)")

    def _render_portfolio_performance(self, renderer: ConsoleRenderer):
        """Render aggregated portfolio performance from the model (#397)."""
        currencies = self._aggregated_report.currencies

        if not currencies:
            renderer.print_bold("PORTFOLIO PERFORMANCE")
            renderer.print_separator(width=68)
            print("No portfolio data available")
            return

        # Render each currency — the model carries the margin/spot split for mixed batches
        for cur in currencies:
            if cur.is_mixed:
                self._render_currency_portfolio(renderer, cur.currency, cur.margin)
                self._render_spot_portfolio(renderer, cur.currency, cur.spot)
            elif cur.is_spot:
                self._render_spot_portfolio(renderer, cur.currency, cur.combined)
            else:
                self._render_currency_portfolio(renderer, cur.currency, cur.combined)

    def _render_currency_portfolio(
        self, renderer: ConsoleRenderer, currency: str, row: AggregatedPortfolioRow
    ):
        """
        Render portfolio metrics for one currency (margin mode), from the model (#397).

        Args:
            renderer: Console renderer
            currency: Currency code (EUR, USD, etc.)
            row: Aggregated portfolio row (the margin / combined group; row.label suffixes the title)
        """
        h = row.headline
        initial = row.initial_balance
        final = row.final_balance
        pnl = row.balance_pnl
        pnl_pct = row.balance_pnl_pct

        total_trades = h.total_trades
        winning = h.winning_trades
        losing = h.losing_trades
        win_rate = h.win_rate

        # Profit factor (executive formula: total_profit / abs(total_loss))
        profit_factor = h.total_profit / \
            abs(h.total_loss) if h.total_loss != 0 else 0

        scenario_count = h.unit_count

        # Render section
        section_title = f"PORTFOLIO PERFORMANCE ({currency})"
        if row.label:
            section_title = f"PORTFOLIO PERFORMANCE ({currency} — {row.label})"
        renderer.print_bold(section_title)
        renderer.print_separator(width=68)
        print(f"Scenarios:          {scenario_count}")
        print(
            f"Initial Capital:    {format_currency_simple(initial, currency)} (avg {format_currency_simple(row.avg_initial, currency)}/scenario)")
        print(f"Final Balance:      {format_currency_simple(final, currency)}")

        print(f"Total P&L:          {renderer.pnl(pnl, currency)} ({pnl_pct:+.2f}%)")
        # Order execution stats
        orders_sent = row.orders_sent
        orders_executed = row.orders_executed
        orders_rejected = row.orders_rejected
        exec_rate = (orders_executed / orders_sent *
                     100) if orders_sent > 0 else 0

        print("")
        print(f"Total Trades:       {total_trades} ({winning}W / {losing}L)")
        print(f"Win Rate:           {win_rate * 100:.1f}%")
        print(
            f"Avg Win:            {format_currency_simple(row.avg_win, currency)}")
        print(
            f"Avg Loss:           {format_currency_simple(row.avg_loss, currency)}")
        print(f"Profit Factor:      {profit_factor:.2f}")

        if orders_rejected > 0:
            print(
                f"Orders:             {orders_executed}/{orders_sent} executed | "
                f"{renderer.yellow(f'{orders_rejected} rejected')} ({exec_rate:.1f}%)")
        else:
            print(
                f"Orders:             {orders_executed}/{orders_sent} executed ({exec_rate:.1f}%)")

        # Pending order latency (green)
        if row.pending_total_resolved > 0:
            latency_line = self._format_pending_latency(renderer, row)
            if latency_line:
                print(latency_line)

        # Order pipeline status (always visible)
        self._render_order_pipeline(renderer, row)

        print("")
        print(
            f"Max Drawdown:       {format_currency_simple(abs(h.max_drawdown), currency)} ({row.max_dd_pct:.1f}%)")
        print(
            f"Max Equity:         {format_currency_simple(row.max_equity, currency)}")
        print(f"Recovery Factor:    {row.recovery_factor:.2f}")
        print("")
        print(
            f"Spread Cost:        {format_currency_simple(row.total_spread_cost, currency)} (avg {format_currency_simple(row.avg_spread, currency)}/trade)")
        print(
            f"Commission:         {format_currency_simple(row.total_commission, currency)}")
        print(
            f"Swap:               {format_currency_simple(row.total_swap, currency)}")
        print(
            f"Maker Fee:          {format_currency_simple(row.maker_fee, currency)}")
        print(
            f"Taker Fee:          {format_currency_simple(row.taker_fee, currency)}")

    def _render_spot_portfolio(
        self, renderer: ConsoleRenderer, currency: str, row: AggregatedPortfolioRow
    ):
        """
        Render portfolio metrics for spot scenarios from the model (#397).

        Shows dual balances per scenario, estimated portfolio value, and P&L.

        Args:
            renderer: Console renderer
            currency: Quote currency code (USD, etc.)
            row: Aggregated spot portfolio row (carries the per-scenario dual-balance sub-rows)
        """
        h = row.headline

        renderer.print_bold(f"PORTFOLIO PERFORMANCE ({currency} — Spot)")
        renderer.print_separator(width=68)
        print(f"Scenarios:          {h.unit_count}")

        print(
            f"Initial Capital:    {format_currency_simple(row.initial_balance, currency)} (avg {format_currency_simple(row.avg_initial, currency)}/scenario)")

        # Per-scenario spot balances
        for s in row.spot_scenarios:
            base_fmt = f'{s.base_balance:,.4f}' if s.base_balance < 100 else f'{s.base_balance:,.2f}'
            print(f"  {s.scenario_name}:")
            print(f"    Balances: {format_currency_simple(s.quote_balance, s.quote_currency)} | {s.base_currency} {base_fmt}")
            if s.has_base_holdings:
                print(f"    Est. Value: {format_currency_simple(s.est_current, s.quote_currency)} @ {s.base_currency} {format_currency_simple(s.last_price, s.quote_currency)}")

        # Totals
        print("")
        if row.spot_has_base_holdings and row.spot_total_est_initial > 0:
            total_pnl = row.spot_total_est_current - row.spot_total_est_initial
            total_pnl_pct = (total_pnl / row.spot_total_est_initial * 100)
            print(f"Est. Portfolio:     {format_currency_simple(row.spot_total_est_current, currency)}")
            print(f"Total P&L:          {renderer.pnl(total_pnl, currency)} ({total_pnl_pct:+.2f}%)")
        else:
            # Simple P&L when no base holdings (same as margin)
            pnl = row.final_balance - row.initial_balance
            pnl_pct = (pnl / row.initial_balance * 100) if row.initial_balance > 0 else 0
            print(f"Final Balance:      {format_currency_simple(row.final_balance, currency)}")
            print(f"Total P&L:          {renderer.pnl(pnl, currency)} ({pnl_pct:+.2f}%)")

        # Trade stats
        if h.total_trades > 0:
            print("")
            print(f"Total Trades:       {h.total_trades} ({h.winning_trades}W / {h.losing_trades}L)")
            print(f"Win Rate:           {h.win_rate * 100:.1f}%")

        # Order execution
        if row.orders_sent > 0:
            print(
                f"Orders:             {row.orders_executed}/{row.orders_sent} executed")

        # Costs (layout A — all five categories, zeros where n/a; spot fees are maker/taker)
        print("")
        print(
            f"Spread Cost:        {format_currency_simple(row.total_spread_cost, currency)}")
        print(
            f"Commission:         {format_currency_simple(row.total_commission, currency)}")
        print(
            f"Swap:               {format_currency_simple(row.total_swap, currency)}")
        print(
            f"Maker Fee:          {format_currency_simple(row.maker_fee, currency)}")
        print(
            f"Taker Fee:          {format_currency_simple(row.taker_fee, currency)}")

    @staticmethod
    def _format_pending_latency(renderer: ConsoleRenderer, row: AggregatedPortfolioRow) -> str:
        """
        Format the pending-latency line for the executive summary from the model row (#397).

        Args:
            renderer: Console renderer for color formatting
            row: Aggregated portfolio row carrying the pending latency fields

        Returns:
            Formatted latency line (green) or empty string
        """
        # Millisecond-based latency
        if row.pending_min_latency_ms is not None:
            line = (f"Avg Latency:        {row.pending_avg_latency_ms:.0f}ms "
                    f"(min: {row.pending_min_latency_ms:.0f}ms | max: {row.pending_max_latency_ms:.0f}ms)")
            # Anomaly suffix (force-closed, timed out)
            anomaly_parts = []
            if row.pending_total_force_closed > 0:
                anomaly_parts.append(f"{row.pending_total_force_closed} force-closed")
            if row.pending_total_timed_out > 0:
                anomaly_parts.append(f"{row.pending_total_timed_out} timed out")
            if anomaly_parts:
                line += f" | {renderer.yellow(' | '.join(anomaly_parts))}"
            return renderer.green(line)

        return ""

    @staticmethod
    def _render_order_pipeline(
        renderer: ConsoleRenderer,
        row: AggregatedPortfolioRow
    ) -> None:
        """
        Render the order-pipeline status line (always visible), from the model row (#397).

        The aggregated pending stats never carry the latency-queue count → `pending` is always
        0 here (matches the previous aggregator behaviour); active limits / stops come from the row.

        Args:
            renderer: Console renderer for color formatting
            row: Aggregated portfolio row carrying the active-order counts
        """
        line = (f"Order Pipeline:     0 pending | {row.pending_active_limit_count} active limits | "
                f"{row.pending_active_stop_count} active stops")
        print(renderer.cyan(line))

    def _format_tracking_status_line(self, renderer: ConsoleRenderer) -> str:
        """
        Build performance-tracking status line for executive summary (#137).

        Layer A = worker_statistics populated for any scenario.
        Layer B = profile_times populated for any scenario.

        Returns:
            Formatted line ('Tracking:           ⚠️ ...') when at least one
            layer is off, empty string when both layers are on (default case
            — no friction).
        """
        layer_a_on = self._run_meta.worker_tracking_on
        layer_b_on = self._run_meta.profiling_tracking_on

        if layer_a_on and layer_b_on:
            return ''

        if not layer_a_on and not layer_b_on:
            msg = ('⚠️  All performance tracking OFF '
                   '(no per-component or operation-level diagnostics)')
        elif not layer_a_on:
            msg = ('⚠️  Worker tracking OFF '
                   '(per-worker / decision breakdowns unavailable)')
        else:
            msg = ('⚠️  Tick-loop profiling OFF '
                   '(operation hotspot analysis unavailable)')

        return f"Tracking:           {renderer.yellow(msg)}"

    def _format_warmup_hotspot(self, renderer: ConsoleRenderer) -> str:
        """
        Build warmup hotspot one-liner for executive summary.

        Shows slowest warmup phase + optional slowest scenario deviation.

        Args:
            renderer: Console renderer for color formatting

        Returns:
            Formatted one-liner string, or empty string if no data
        """
        phases = self._profiling.warmup_phases
        if not phases:
            return ''

        total_warmup = sum(p.duration_s for p in phases)
        if total_warmup <= 0:
            return ''

        slowest = max(phases, key=lambda p: p.duration_s)
        pct = slowest.duration_s / total_warmup * 100
        phase_part = renderer.yellow(
            f"Phase [{slowest.name}]  {slowest.duration_s:.1f}s ({pct:.1f}%)"
        )

        # Slowest scenario deviation (only when >1 scenario and profiling data present)
        scenario_part = self._format_slowest_scenario_deviation(renderer)
        if scenario_part:
            return f"{phase_part}  |  {scenario_part}"
        return phase_part

    def _format_slowest_scenario_deviation(self, renderer: ConsoleRenderer) -> str:
        """
        Build slowest scenario deviation string vs. average.

        Uses sum of profiling profile_times per scenario as proxy for compute cost.
        Only shown when deviation > 15% and more than 1 scenario present.

        Args:
            renderer: Console renderer for color formatting

        Returns:
            Formatted deviation string, or empty string
        """
        # Per-scenario compute cost = the unit's summed operation time (profiling model).
        # The model only carries units where tick-loop profiling was on — same filter as before.
        units = self._profiling.units
        if len(units) < 2:
            return ''

        scenario_times = [(u.name, u.total_ms) for u in units]

        avg_ms = sum(t for _, t in scenario_times) / len(scenario_times)
        if avg_ms <= 0:
            return ''

        slowest_name, slowest_ms = max(scenario_times, key=lambda x: x[1])
        deviation_pct = (slowest_ms - avg_ms) / avg_ms * 100

        if deviation_pct < 15.0:
            return ''

        return renderer.yellow(f"Slowest Scenario: {slowest_name}  +{deviation_pct:.0f}% vs avg")

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
        Calculate in-time statistics from the run-meta + profiling models.

        Returns:
            Dict with total_hours, avg_hours, total_days, ticks_per_hour
        """
        total_hours = self._run_meta.total_hours

        # Ticks per hour (market density = all ticks incl. clipped; fall back to the
        # coordination ticks when no clipping was active)
        ticks_total = self._profiling.aggregate.clipping_total_ticks
        if ticks_total == 0:
            ticks_total = sum(u.ticks_processed for u in self._scenario_details.units)
        ticks_per_hour = ticks_total / total_hours if total_hours > 0 else 0

        return {
            'total_hours': total_hours,
            'avg_hours': self._run_meta.avg_hours,
            'total_days': self._run_meta.total_days,
            'ticks_per_hour': ticks_per_hour
        }
