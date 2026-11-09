"""
FiniexTestingIDE - Sim Executive Summary
One-screen overview of batch execution results — the sim pipeline's closing block of the unified
end-of-run console (the counterpart to AutotraderSessionSummary). The user-facing heading stays
"🎯 EXECUTIVE SUMMARY".

Provides comprehensive summary:
- Execution results (success rate, timing with warmup/tickrun split)
- In-time performance (simulated market time)
- Real-time performance (tick processing speed)
- Portfolio aggregation (P&L, win rate, metrics)
- System resources
"""

from python.framework.constants.currency_constants import (
    format_currency_cost,
    format_currency_signed,
    format_currency_simple,
)
from python.framework.reporting.console.abstract_batch_summary_section import (
    AbstractBatchSummarySection,
)
from python.framework.reporting.console.feed_stability_summary import format_disturbance_line
from python.framework.reporting.console.order_counts_line import order_endings_text
from python.framework.reporting.console.portfolio_summary import SPREAD_NOT_MEASURED
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    AggregatedPortfolioRow,
    PerformanceTracking,
    ProfilingReport,
    RunMetaReport,
    RunSummary,
    ScenarioDetailsReport,
    WarningsErrorsReport,
)
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer


# The line each tracking class prints — nothing when both layers carried data
_TRACKING_LINE = {
    PerformanceTracking.BOTH_ON: '',
    PerformanceTracking.ALL_OFF: ('⚠️  All performance tracking OFF '
                                  '(no per-component or operation-level diagnostics)'),
    PerformanceTracking.WORKER_OFF: ('⚠️  Worker tracking OFF '
                                     '(per-worker / decision breakdowns unavailable)'),
    PerformanceTracking.PROFILING_OFF: ('⚠️  Tick-loop profiling OFF '
                                        '(operation hotspot analysis unavailable)'),
}


class SimExecutiveSummary(AbstractBatchSummarySection):
    """
    Generates executive summary for batch execution.

    Aggregates all key metrics into single-screen overview.
    Designed for quick performance assessment.
    """

    _section_title = '🎯 EXECUTIVE SUMMARY'

    def __init__(
        self,
        run_summary: RunSummary,
        run_meta_report: RunMetaReport,
        profiling_report: ProfilingReport,
        scenario_details_report: ScenarioDetailsReport,
        warnings_errors_report: WarningsErrorsReport,
        aggregated_report: AggregatedPortfolioReport,
    ):
        """
        Initialize executive summary.

        Args:
            run_summary: Cross-section run KPIs (the model-fed headline source)
            run_meta_report: Run-level meta — timing, parallelism, the machine, the throughput
            profiling_report: Profiling — the warmup, the clipping roll-up, the slowest scenario
            scenario_details_report: Scenario details — the data-broker roll-up
            warnings_errors_report: Warnings/errors model — the failed-scenario headline reads its
                outcome (no inline re-scan of process results, #395)
            aggregated_report: The per-currency aggregated portfolio
        """
        self._run_summary = run_summary
        self._run_meta = run_meta_report
        self._profiling = profiling_report
        self._scenario_details = scenario_details_report
        self._outcome = warnings_errors_report.outcome
        self._aggregated_report = aggregated_report

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
        self._render_data_brokers(renderer)
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

        renderer.print_bold('RUN SUMMARY')
        renderer.print_separator(width=68)
        print(f'Scenarios:          {summary.unit_count}')

        orders_line = (f'Orders:             {summary.orders_executed}/{summary.orders_submitted} '
                       f'executed ({summary.execution_rate_pct:.1f}%)')
        endings = order_endings_text(summary, renderer)
        if endings:
            orders_line += f' | {endings}'
        if summary.sl_tp_triggered > 0:
            orders_line += f' | {summary.sl_tp_triggered} SL/TP'
        print(orders_line)

        # Feed disturbance (#451) — a disturbed run must not read as a clean one.
        disturbance = format_disturbance_line(summary)
        if disturbance:
            print(renderer.yellow(disturbance))

        if not summary.currencies:
            return

        print('')
        for c in summary.currencies:
            # Expectancy (mean R) is only meaningful with stop-loss trades.
            exp_str = f'{c.expectancy:+.2f}R' if c.r_trade_count > 0 else 'n/a'
            pf_str = f'{c.profit_factor:.2f}' if c.profit_factor is not None else 'n/a'
            print(
                f'{c.currency}:'.ljust(20)
                + f'P&L {renderer.pnl(c.net_pnl, c.currency)} | '
                + f'Win {c.win_rate * 100:.1f}% ({c.winning_trades}W/{c.losing_trades}L) | '
                + f'PF {pf_str} | '
                + f'Exp {exp_str} | '
                + f'Fees {format_currency_simple(c.total_fees, c.currency)}'
            )

    def _render_execution_results(self, renderer: ConsoleRenderer):
        """Render execution results section."""
        total_scenarios = self._run_meta.scenario_count
        # Failure truth from the warnings/errors model (no inline re-scan, #395)
        outcome = self._outcome
        failed = outcome.failed_count

        # Get timing breakdown (run-level, from the meta model)
        batch_time = self._run_meta.execution_time_s
        warmup_time = self._run_meta.warmup_time_s
        tickrun_time = self._run_meta.tickrun_time_s

        # The parallelism the batch ran with, as the run meta recorded it
        parallel = self._run_meta.parallel_scenarios
        max_workers = self._run_meta.max_parallel_scenarios

        # The status the outcome's counts describe
        if outcome.batch_status is BatchStatus.SUCCESS:
            status_str = renderer.green('✅ Complete Success')
        elif outcome.batch_status is BatchStatus.FAILED:
            status_str = renderer.red('❌ Complete Failure')
        else:
            status_str = renderer.yellow(
                f'⚠️ Partial ({outcome.succeeded_count}/{outcome.total_units})')

        # Count disabled scenarios (run-level, from the meta model)
        disabled = self._run_meta.disabled_count

        # Render section
        renderer.print_bold('EXECUTION RESULTS')
        renderer.print_separator(width=68)
        print(f'Scenarios:          {total_scenarios} executed' +
              (f' ({disabled} disabled)' if disabled > 0 else ''))
        print(
            f'Success Rate:       {outcome.success_rate_pct:.1f}% '
            f'({outcome.succeeded_count}/{outcome.total_units} successful)')
        print(f'Status:             {status_str}')

        # Show first failed scenario details (from the model outcome)
        if failed > 0:
            self._render_first_failure(renderer)

        # Batch time with warmup/tickrun split
        if warmup_time > 0:
            print(
                f'Batch Time:         {batch_time:.1f}s (warmup: {warmup_time:.1f}s | tickrun: {tickrun_time:.1f}s)')
        else:
            # Mounted mode - no warmup
            print(f'Batch Time:         {batch_time:.1f}s')

        # Warmup hotspot one-liner (always shown when warmup phases available)
        warmup_hotspot = self._format_warmup_hotspot(renderer)
        if warmup_hotspot:
            print(f'Warmup Hotspot:     {warmup_hotspot}')

        # Mode line carries the run-quality tag (recorded at execution time):
        # PRODUCTION (real subprocesses, timings representative) vs DEBUG (serial
        # under a debugger / DEBUG_MODE, timings not representative).
        if self._run_meta.debug_execution:
            mode_str = 'Sequential ' + renderer.yellow(
                '🐞 DEBUG — timings not representative')
        else:
            mode_str = 'Parallel' if parallel else 'Sequential'
            if parallel:
                mode_str += f' (max {max_workers} workers)'
            mode_str += f" {renderer.green('— PRODUCTION')}"
        print(f'Mode:               {mode_str}')

        # Source line (Profile Run vs Scenario Set)
        if self._run_meta.generator_profile_count:
            mode_str = ', '.join(self._run_meta.generator_profile_modes)
            print(f'Source:             Profile Run ({self._run_meta.generator_profile_count} '
                  f'profile(s), {mode_str})')
        else:
            print('Source:             Scenario Set')

        # Performance tracking status (#137) — surface when at least one
        # tracking layer is off so the user knows why summary sections are missing
        tracking_line = self._format_tracking_status_line(renderer)
        if tracking_line:
            print(tracking_line)

    def _render_data_brokers(self, renderer: ConsoleRenderer):
        """
        Render the data-broker roll-up — formatting only.

        The grouping and the market-type resolution both live in the builder now. This used to
        instantiate a config manager here and group the scenario rows itself, which the
        reporting-pipeline rule forbids for a reason that is not tidiness: config answers what
        a broker is TODAY, so the console could print one thing while the artifact beside it
        held another, and no other surface got the roll-up at all.

        Args:
            renderer: Console renderer
        """
        renderer.print_bold('DATA BROKERS')
        renderer.print_separator(width=68)

        for broker in self._scenario_details.data_brokers:
            symbols_str = ', '.join(broker.symbols)
            # Truncate if too long
            if len(symbols_str) > 40:
                symbols_str = symbols_str[:37] + '...'

            print(
                f'{broker.data_broker_type} [{broker.market_type}]'.ljust(24) +
                f'{broker.scenario_count} scenario(s) ({symbols_str})')
            # The price basis the bars were RENDERED from (§31c), not what config declares
            # today. Printed only where the archive knows — a run over tick data alone has
            # no basis to report, and an empty line would read as an answer.
            if broker.price_bases:
                print(f'{"":24}price basis: {broker.price_bases}')

    def _render_first_failure(self, renderer: ConsoleRenderer):
        """
        Render first failed scenario details from the model outcome (#395).

        Args:
            renderer: Console renderer
        """
        outcome = self._outcome
        scenario_name = outcome.first_failure_name

        # The first message line that says something, as the outcome splits it
        messages = outcome.first_failure_messages
        if messages:
            first_error = messages[0]
        elif outcome.first_failure_error:
            first_error = 'Unknown error'
        else:
            first_error = 'No error message available'
        remaining_errors = outcome.first_failure_more_messages
        remaining_scenarios = outcome.other_failed_count

        # Truncate error if too long
        max_len = 80
        if len(first_error) > max_len:
            first_error = first_error[:max_len - 3] + '...'

        # Add suffixes
        error_suffix = f' (+ {remaining_errors} more)' if remaining_errors > 0 else ''
        scenario_suffix = f' (+ {remaining_scenarios} more)' if remaining_scenarios > 0 else ''

        # Color highlights for errors
        print(
            f"{renderer.red('Failed Scenario:    ')}{scenario_name}{scenario_suffix}")
        print(f"{renderer.red('Error:              ')}{first_error}{error_suffix}")

    def _render_time_performance(self, renderer: ConsoleRenderer):
        """Render in-time and real-time performance sections — every figure from the run meta."""
        meta = self._run_meta
        agg = self._profiling.aggregate
        # The market time in hours: covered once, and processed summed over the scenarios
        covered = (self._run_summary.tick_timespan_seconds or 0.0) / 3600
        processed_hours = (self._run_summary.tick_timespan_total_seconds or 0.0) / 3600
        # The algo path's ticks, and what the loop iterated (the clipping total where a budget
        # clipped, the algo ticks otherwise)
        algo_ticks = meta.total_ticks_processed
        loop_ticks = meta.loop_ticks
        tickrun_time = meta.tickrun_time_s

        # Render IN-TIME section — the MEASURED tick timespan, never the declared windows: a
        # stretch two scenarios share is covered once and processed twice, and both are true.
        renderer.print_bold('IN-TIME PERFORMANCE (tick timespan)')
        renderer.print_separator(width=68)
        print(f'Covered:            {covered:.1f} hours ({covered / 24:.1f} days, overlap once)')
        print(
            f"Processed:          {processed_hours:.1f} hours "
            f"({self._run_summary.unit_count} scenarios summed, "
            f"avg {meta.avg_hours_per_unit:.2f} h)")
        if meta.has_clipping:
            print(f'Ticks Processed:    {loop_ticks:,} total ({algo_ticks:,} algo)')
        else:
            print(f'Ticks Processed:    {algo_ticks:,} total')

        # Tick budget one-liner (only when clipping was active)
        if agg.budget_active:
            total_clipped = agg.clipping_total_clipped
            budget_str = '/'.join(f'{b}ms' for b in agg.clipping_budgets)
            if total_clipped > 0:
                print(
                    f'Tick Budget:        {budget_str} '
                    f'({total_clipped:,} clipped = {agg.clipping_rate_pct:.1f}%)')
            else:
                print(f'Tick Budget:        {budget_str} (no ticks clipped)')

        print(
            f'Ticks/Hour:         {meta.ticks_per_hour:,.0f} (market density)')

        print()

        # Render REAL-TIME section (tick processing speed)
        renderer.print_bold('REAL-TIME PERFORMANCE (Tick Processing Speed)')
        renderer.print_separator(width=68)
        pickle_time = meta.pickle_time_s
        pickle_mb = meta.pickle_sample_mb
        if pickle_time > 0.0:
            mb_str = f' ~{pickle_mb:.1f} MB/scenario' if pickle_mb > 0.0 else ''
            print(
                f'Tick Run Time:      {tickrun_time:.1f} seconds '
                f'(pickle: {pickle_time:.1f}s{mb_str} | '
                f'execution: {meta.tickrun_execution_time_s:.1f}s)'
            )
        else:
            print(f'Tick Run Time:      {tickrun_time:.1f} seconds')
        print(
            f'Ticks/Second:       {meta.ticks_per_second:,.0f} (processing rate)')
        print(
            f'Speedup:            {meta.speedup:,.0f}x ({processed_hours:.0f} hours → {tickrun_time:.0f} seconds)')

    def _render_portfolio_performance(self, renderer: ConsoleRenderer):
        """Render aggregated portfolio performance from the model (#397)."""
        currencies = self._aggregated_report.currencies

        if not currencies:
            renderer.print_bold('PORTFOLIO PERFORMANCE')
            renderer.print_separator(width=68)
            print('No portfolio data available')
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

        # Profit factor comes from the model — the same figure every surface renders
        pf_str = f'{h.profit_factor:.2f}' if h.profit_factor is not None else 'n/a'

        scenario_count = h.unit_count

        # Render section
        section_title = f'PORTFOLIO PERFORMANCE ({currency})'
        if row.label:
            section_title = f'PORTFOLIO PERFORMANCE ({currency} — {row.label})'
        renderer.print_bold(section_title)
        renderer.print_separator(width=68)
        print(f'Scenarios:          {scenario_count}')
        # A figure that can go below zero keeps its sign, in front of the symbol: a balance, an
        # equity, the unrealized mark, the spread and the swap — both are signed costs. Fees, the
        # starting capital, a peak and the drawdown's magnitude are printed as magnitudes.
        print(
            f'Initial Capital:    {format_currency_simple(initial, currency)} (avg {format_currency_simple(row.avg_initial, currency)}/scenario)')
        print(f'Final Balance:      {format_currency_signed(final, currency)}')

        print(f'Total P&L:          {renderer.pnl(pnl, currency)} ({pnl_pct:+.2f}%)')
        # Order execution stats
        orders_submitted = row.orders_submitted
        orders_executed = row.orders_executed
        endings = order_endings_text(row, renderer)
        exec_rate = row.execution_rate_pct

        print('')
        print(f'Total Trades:       {total_trades} ({winning}W / {losing}L)')
        print(f'Win Rate:           {win_rate * 100:.1f}%')
        print(
            f'Avg Win:            {format_currency_simple(row.avg_win, currency)}')
        print(
            f'Avg Loss:           {format_currency_simple(row.avg_loss, currency)}')
        print(f'Profit Factor:      {pf_str}')

        if endings:
            print(
                f'Orders:             {orders_executed}/{orders_submitted} executed | '
                f'{endings} ({exec_rate:.1f}%)')
        else:
            print(
                f'Orders:             {orders_executed}/{orders_submitted} executed ({exec_rate:.1f}%)')

        # How long the venue took to answer (green)
        if row.pending_total_submitted > 0:
            latency_line = self._format_pending_latency(renderer, row)
            if latency_line:
                print(latency_line)

        # Order pipeline status (always visible)
        self._render_order_pipeline(renderer, row)

        # #492: what the run still HELD when its data ran out. Realised P&L above, the
        # wealth view here — never folded together.
        if h.open_position_count:
            print('')
            print(
                f'Open at end:        {h.open_position_count} position(s) '
                f'({format_currency_signed(h.unrealized_pnl, currency)} unrealized)')
            if h.final_equity is not None:
                print(
                    f'Final Equity:       {format_currency_signed(h.final_equity, currency)}')
            else:
                # Several accounts: their sum, said as one, beside the capital it began from.
                print(
                    f'Total Final Equity: {format_currency_signed(h.total_final_equity, currency)}'
                    f'  ({h.unit_count} accounts, initial '
                    f'{format_currency_simple(h.total_initial_balance, currency)})')

        print('')
        # The drawdown trio is ONE account's; over several it names which one.
        of_unit = f' — {h.account_max_drawdown_unit}' if h.unit_count > 1 else ''
        # "curve" names the measure (#497) — see portfolio_summary for why the word alone
        # is not enough once a second honest reading exists.
        print(
            f'Max Drawdown (account, curve): '
            f'{format_currency_simple(abs(h.account_max_drawdown), currency)} '
            f'({row.account_max_dd_pct:.1f}%){of_unit}')
        # The peak THAT account fell from — the highest peak of any account is a different
        # figure and stands in the aggregated details with its own scenario.
        equity_label = (f'Max Equity ({h.account_max_drawdown_unit}):' if h.unit_count > 1
                        else 'Max Equity:')
        print(f'{equity_label:<20}{format_currency_simple(h.max_equity, currency)}')
        if row.recovery_factor is None:
            print(f'Recovery Factor:    — ({h.unit_count} separate accounts)')
        else:
            print(f'Recovery Factor:    {row.recovery_factor:.2f}')
        print('')
        if row.total_spread_cost is None:
            print(f'Spread Cost:        {SPREAD_NOT_MEASURED}')
        else:
            print(
                f'Spread Cost:        {format_currency_cost(row.total_spread_cost, currency)} (avg {format_currency_cost(row.avg_spread, currency)}/trade)')
        if row.total_modelled_spread_cost:
            print(f'Modelled Spread:    '
                  f'{format_currency_cost(row.total_modelled_spread_cost, currency)}')
        print(
            f'Commission:         {format_currency_cost(row.total_commission, currency)}')
        print(
            f'Swap:               {format_currency_cost(row.total_swap, currency)}')
        print(
            f'Maker Fee:          {format_currency_cost(row.maker_fee, currency)}')
        print(
            f'Taker Fee:          {format_currency_cost(row.taker_fee, currency)}')

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

        renderer.print_bold(f'PORTFOLIO PERFORMANCE ({currency} — Spot)')
        renderer.print_separator(width=68)
        print(f'Scenarios:          {h.unit_count}')

        print(
            f'Initial Capital:    {format_currency_simple(row.initial_balance, currency)} (avg {format_currency_simple(row.avg_initial, currency)}/scenario)')

        # Per-scenario spot balances
        for s in row.spot_scenarios:
            base_fmt = f'{s.base_balance:,.4f}' if s.base_balance < 100 else f'{s.base_balance:,.2f}'
            print(f'  {s.scenario_name}:')
            print(f'    Balances: {format_currency_signed(s.quote_balance, s.quote_currency)} | {s.base_currency} {base_fmt}')
            if s.has_base_holdings:
                print(f'    Est. Value: {format_currency_signed(s.est_current, s.quote_currency)} @ {s.base_currency} {format_currency_simple(s.last_price, s.quote_currency)}')

        # Totals
        print('')
        if row.spot_total_est_pnl is not None:
            print(f'Est. Portfolio:     {format_currency_signed(row.spot_total_est_current, currency)}')
            print(f'Total P&L:          {renderer.pnl(row.spot_total_est_pnl, currency)} '
                  f'({row.spot_total_est_pnl_pct:+.2f}%)')
        else:
            # The balance P&L when nothing at spot could be valued (same as margin)
            print(f'Final Balance:      {format_currency_signed(row.final_balance, currency)}')
            print(f'Total P&L:          {renderer.pnl(row.balance_pnl, currency)} '
                  f'({row.balance_pnl_pct:+.2f}%)')

        # Trade stats
        if h.total_trades > 0:
            print('')
            print(f'Total Trades:       {h.total_trades} ({h.winning_trades}W / {h.losing_trades}L)')
            print(f'Win Rate:           {h.win_rate * 100:.1f}%')

        # Order execution
        if row.orders_submitted > 0:
            print(
                f'Orders:             {row.orders_executed}/{row.orders_submitted} executed')

        # Costs (layout A — all five categories, zeros where n/a; spot fees are maker/taker)
        print('')
        spread = (SPREAD_NOT_MEASURED if row.total_spread_cost is None
                  else format_currency_cost(row.total_spread_cost, currency))
        print(f'Spread Cost:        {spread}')
        if row.total_modelled_spread_cost:
            print(f'Modelled Spread:    '
                  f'{format_currency_cost(row.total_modelled_spread_cost, currency)}')
        print(
            f'Commission:         {format_currency_cost(row.total_commission, currency)}')
        print(
            f'Swap:               {format_currency_cost(row.total_swap, currency)}')
        print(
            f'Maker Fee:          {format_currency_cost(row.maker_fee, currency)}')
        print(
            f'Taker Fee:          {format_currency_cost(row.taker_fee, currency)}')

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
        if row.pending_min_in_flight_ms is not None:
            line = (f'In Flight:          avg {row.pending_avg_in_flight_ms:.0f}ms '
                    f'(min: {row.pending_min_in_flight_ms:.0f}ms | '
                    f'max: {row.pending_max_in_flight_ms:.0f}ms)')
            # Anomaly suffix (never confirmed, expired on the way)
            anomaly_parts = []
            if row.pending_total_never_confirmed > 0:
                anomaly_parts.append(f'{row.pending_total_never_confirmed} never confirmed')
            if row.pending_total_expired > 0:
                anomaly_parts.append(f'{row.pending_total_expired} expired on the way')
            if anomaly_parts:
                line += f" | {renderer.yellow(' | '.join(anomaly_parts))}"
            return renderer.green(line)

        return ''

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
        line = (f'Order Pipeline:     0 pending | {row.pending_active_limit_count} active limits | '
                f'{row.pending_active_stop_count} active stops')
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
        msg = _TRACKING_LINE[self._run_meta.performance_tracking]
        if not msg:
            return ''
        return f'Tracking:           {renderer.yellow(msg)}'

    def _format_warmup_hotspot(self, renderer: ConsoleRenderer) -> str:
        """
        Build warmup hotspot one-liner for executive summary.

        Shows slowest warmup phase + optional slowest scenario deviation.

        Args:
            renderer: Console renderer for color formatting

        Returns:
            Formatted one-liner string, or empty string if no data
        """
        profiling = self._profiling
        if not profiling.warmup_phases or profiling.warmup_total_s <= 0:
            return ''

        phase_part = renderer.yellow(
            f'Phase [{profiling.warmup_slowest_phase}]  {profiling.warmup_slowest_s:.1f}s '
            f'({profiling.warmup_slowest_pct:.1f}%)'
        )

        # Slowest scenario deviation (only when >1 scenario and profiling data present)
        scenario_part = self._format_slowest_scenario_deviation()
        if scenario_part:
            return f'{phase_part}  |  {scenario_part}'
        return phase_part

    def _format_slowest_scenario_deviation(self) -> str:
        """
        Build slowest scenario deviation string vs. average.

        Uses sum of profiling profile_times per scenario as proxy for compute cost. Shown whenever
        more than one scenario was profiled and the mean is above zero — a measurement; there is
        deliberately no "too slow" threshold (validators/shared_advisory_checks.py).

        Returns:
            Formatted deviation string, or empty string
        """
        # Per-scenario compute cost = the unit's summed operation time (profiling model).
        # The model only carries units where tick-loop profiling was on — same filter as before.
        if len(self._profiling.units) < 2:
            return ''

        agg = self._profiling.aggregate
        deviation_pct = agg.slowest_profiled_unit_deviation_pct
        if deviation_pct is None:
            return ''

        # Plain, not yellow: a measurement, which no threshold turns into a warning
        return f'Slowest Scenario: {agg.slowest_profiled_unit}  +{deviation_pct:.0f}% vs avg'

    def _render_system_resources(self, renderer: ConsoleRenderer):
        """Render system resources section."""
        # The processors and the memory that bind, as the run meta read them when it was derived
        meta = self._run_meta
        renderer.print_bold('SYSTEM RESOURCES')
        renderer.print_separator(width=68)
        if meta.machine_cpu_count is None or meta.machine_memory_limit_bytes is None:
            print('Machine:            not recorded')
            return
        ram_total_gb = meta.machine_memory_limit_bytes / (1024**3)
        ram_available_gb = meta.machine_memory_available_bytes / (1024**3)
        print(f'CPU Cores:          {meta.machine_cpu_count:g}')
        print(
            f'RAM:                {ram_available_gb:.1f} GB available / {ram_total_gb:.1f} GB total')
