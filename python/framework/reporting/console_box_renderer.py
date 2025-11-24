"""
FiniexTestingIDE - Console Box Renderer
Specialized box rendering for scenario and portfolio statistics

Handles:
- Success boxes (normal statistics display with status line)
- Error boxes (validation/execution failures - NO status line, self-explanatory via ❌)
- Hybrid cases (partial execution with errors - CRITICAL state)
- Intelligent message wrapping and truncation

Box alignment is critical for grid rendering - all boxes in same row
must have identical line counts.
"""

from typing import List
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.rendering_types import BoxRenderConfig
from python.framework.utils.time_utils import format_duration, format_tick_timespan
from python.framework.types.currency_codes import format_currency_simple


class ConsoleBoxRenderer:
    """
    Renders scenario and portfolio statistics as formatted console boxes.

    Supports both success and error states with proper line alignment
    for grid rendering.
    """

    def __init__(self, renderer, config: BoxRenderConfig = None):
        """
        Initialize box renderer.

        Args:
            renderer: ConsoleRenderer instance (for colors, padding, box borders)
            config: Box render configuration (uses defaults if None)
        """
        self._renderer = renderer
        self._config = config or BoxRenderConfig()

    def create_scenario_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create box lines for scenario statistics.

        Dispatches to appropriate handler based on execution state:
        - Success: Normal statistics with status line
        - Error only: Error display (NO status line - self-explanatory)
        - Hybrid (success + errors): CRITICAL warning

        Args:
            scenario: ProcessResult object
            box_width: Total box width

        Returns:
            List of formatted box lines
        """
        # Check for errors first
        if scenario.error_type or scenario.error_message:
            # Check hybrid case (tick_loop_results exists BUT errors present)
            if scenario.tick_loop_results:
                return self._create_hybrid_scenario_box(scenario, box_width)
            else:
                # Pure error case
                return self._create_scenario_error_box(scenario, box_width)

        # Normal success case
        return self._create_success_scenario_box(scenario, box_width)

    def create_portfolio_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create box lines for portfolio statistics.

        Dispatches to appropriate handler based on execution state:
        - Success: Normal portfolio stats with status line
        - Error only: Minimal error display (NO redundant error message)
        - Hybrid (success + errors): CRITICAL warning

        Args:
            scenario: ProcessResult object
            box_width: Total box width

        Returns:
            List of formatted box lines
        """
        # Check for errors first
        if scenario.error_type or scenario.error_message:
            # Check hybrid case
            if scenario.tick_loop_results and scenario.tick_loop_results.portfolio_stats:
                return self._create_hybrid_portfolio_box(scenario, box_width)
            else:
                # Pure error case - minimal display, NO redundant error message
                return self._create_portfolio_error_box(scenario, box_width)

        # Normal success case
        return self._create_success_portfolio_box(scenario, box_width)

    def _create_success_scenario_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create scenario box for successful execution.

        Args:
            scenario: ProcessResult with success=True
            box_width: Total box width

        Returns:
            List of formatted box lines (10 content lines: 9 stats + 1 status)
        """
        performance_stats = scenario.tick_loop_results.performance_stats
        portfolio_stats = scenario.tick_loop_results.portfolio_stats
        decision_statistics = scenario.tick_loop_results.decision_statistics
        scenario_name = scenario.scenario_name[:28]
        symbol = scenario.symbol
        ticks = performance_stats.ticks_processed
        nfSig = decision_statistics.buy_signals + decision_statistics.sell_signals
        buys = decision_statistics.buy_signals
        sells = decision_statistics.sell_signals
        flats = decision_statistics.flat_signals

        rate = 100
        if ticks > 0:
            rate = nfSig / ticks

        action_trades = portfolio_stats.total_trades
        rate_trades = 100
        if ticks > 0:
            rate_trades = action_trades / ticks

        # Format tick time range
        tick_time_range = "N/A"
        stats = scenario.tick_loop_results.tick_range_stats
        first_tick_time = stats.first_tick_time
        last_tick_time = stats.last_tick_time
        if first_tick_time and last_tick_time:
            tick_time_range = format_tick_timespan(
                stats.first_tick_time,
                stats.last_tick_time,
                stats.tick_timespan_seconds
            )
        execution_time = format_duration(scenario.execution_time_ms, True)

        # Create content lines (9 stats + 1 status)
        lines = [
            f"{scenario_name}",
            f"Symbol: {symbol}",
            f"Duration: {execution_time}",
            f"Ticks: {ticks:,}",
            f"{tick_time_range}",
            f"Non-Flat Sign.: {nfSig} ({rate:.1%})",
            f"B/S/F: {buys}/{sells}/{flats}",
            f"Trades requested: {action_trades} ({rate_trades:.1%})",
            f"Worker: {scenario.tick_loop_results.performance_stats.total_workers}",
            self._renderer.green("✅ Status: Success"),
        ]

        return self._renderer.render_box(lines, box_width)

    def _create_success_portfolio_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create portfolio box for successful execution.

        Args:
            scenario: ProcessResult with success=True
            box_width: Total box width

        Returns:
            List of formatted box lines (12 content lines: 11 stats + 1 status)
        """
        scenario_name = scenario.scenario_name[:28]
        portfolio_stats = scenario.tick_loop_results.portfolio_stats
        execution_stats = scenario.tick_loop_results.execution_stats
        cost_breakdown = scenario.tick_loop_results.cost_breakdown

        # Handle case where stats might be None (but tick_loop_results exists)
        if not portfolio_stats or not execution_stats or not cost_breakdown:
            lines = [
                self._renderer.red(f"❌ {scenario_name}"),
                f"Symbol: {scenario.symbol}",
                "",
                "No portfolio data available",
                "(Execution error)",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
            return self._renderer.render_box(lines, box_width)

        # Extract stats
        total_trades = portfolio_stats.total_trades
        winning = portfolio_stats.winning_trades
        losing = portfolio_stats.losing_trades
        win_rate = portfolio_stats.win_rate
        long_trades = portfolio_stats.total_long_trades
        short_trades = portfolio_stats.total_short_trades

        # Calculate P&L
        total_profit = portfolio_stats.total_profit
        total_loss = portfolio_stats.total_loss
        total_pnl = total_profit - total_loss

        # Costs
        spread_cost = cost_breakdown.total_spread_cost
        orders_sent = execution_stats.orders_sent
        orders_executed = execution_stats.orders_executed

        # Get currency
        currency = portfolio_stats.currency

        # Format P&L with color and currency
        if total_pnl >= 0:
            pnl_str = self._renderer.green(
                f"+{format_currency_simple(total_pnl, currency)}")
        else:
            pnl_str = self._renderer.red(
                f"{format_currency_simple(total_pnl, currency)}")

        broker_name = portfolio_stats.broker_name
        configured_currency = portfolio_stats.configured_account_currency
        current_conversion_rate = portfolio_stats.current_conversion_rate
        initial_balance = portfolio_stats.initial_balance
        current_balance = portfolio_stats.current_balance

        initial_balance_str = format_currency_simple(
            portfolio_stats.initial_balance, currency)
        current_balance_str = format_currency_simple(
            portfolio_stats.current_balance, currency)

        if current_balance > initial_balance:
            current_balance_str = self._renderer.green(current_balance_str)
        if current_balance < initial_balance:
            current_balance_str = self._renderer.red(f"{current_balance_str}")

        # Format currency display
        if configured_currency == "auto":
            currency_display = f"{currency} (auto)"
        else:
            currency_display = currency

        # Format conversion rate
        if current_conversion_rate is not None:
            rate_display = f" @ {current_conversion_rate:.4f}"
        else:
            rate_display = ""

        # Create content lines (11 stats + 1 status)
        if total_trades > 0:
            lines = [
                f"{scenario_name}",
                f"Broker: {broker_name}",
                f"Account: {currency_display}",
                f"Trades executed: {total_trades} ({winning}W/{losing}L)",
                f"Win Rate: {win_rate:.1%}",
                f"P&L: {pnl_str}{rate_display}",
                f"Balance: {current_balance_str}",
                f"Init: {initial_balance_str}",
                f"Spread: {format_currency_simple(spread_cost, currency)}",
                f"Orders Ex/Sent: {orders_executed}/{orders_sent}",
                f"Long/Short: {long_trades}/{short_trades}",
                self._renderer.green("✅ Status: Success"),
            ]
        else:
            lines = [
                f"💰 {scenario_name}",
                "No trades executed",
                f"Orders: {orders_sent} sent",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                self._renderer.green("✅ Status: Success"),
            ]

        return self._renderer.render_box(lines, box_width)

    def _create_hybrid_scenario_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create scenario box for hybrid case (execution completed BUT errors present).

        This is a CRITICAL state that should never occur in production.
        Indicates process recovered from error but errors were logged.

        Args:
            scenario: ProcessResult with tick_loop_results AND errors
            box_width: Total box width

        Returns:
            List of formatted box lines with CRITICAL warning (replaces success status)
        """
        performance_stats = scenario.tick_loop_results.performance_stats
        portfolio_stats = scenario.tick_loop_results.portfolio_stats
        decision_statistics = scenario.tick_loop_results.decision_statistics
        scenario_name = scenario.scenario_name[:28]
        symbol = scenario.symbol
        ticks = performance_stats.ticks_processed
        nfSig = decision_statistics.buy_signals + decision_statistics.sell_signals
        buys = decision_statistics.buy_signals
        sells = decision_statistics.sell_signals
        flats = decision_statistics.flat_signals

        rate = 100
        if ticks > 0:
            rate = nfSig / ticks

        action_trades = portfolio_stats.total_trades
        rate_trades = 100
        if ticks > 0:
            rate_trades = action_trades / ticks

        # Format tick time range
        tick_time_range = "N/A"
        stats = scenario.tick_loop_results.tick_range_stats
        first_tick_time = stats.first_tick_time
        last_tick_time = stats.last_tick_time
        if first_tick_time and last_tick_time:
            tick_time_range = format_tick_timespan(
                stats.first_tick_time,
                stats.last_tick_time,
                stats.tick_timespan_seconds
            )
        execution_time = format_duration(scenario.execution_time_ms, True)

        # Create content lines (9 stats + 1 CRITICAL warning)
        lines = [
            f"{scenario_name}",
            f"Symbol: {symbol}",
            f"Duration: {execution_time}",
            f"Ticks: {ticks:,}",
            f"{tick_time_range}",
            f"Non-Flat Sign.: {nfSig} ({rate:.1%})",
            f"B/S/F: {buys}/{sells}/{flats}",
            f"Trades requested: {action_trades} ({rate_trades:.1%})",
            f"Worker: {scenario.tick_loop_results.performance_stats.total_workers}",
            self._renderer.red("⚠️ CRITICAL: Errors detected"),
        ]

        return self._renderer.render_box(lines, box_width)

    def _create_hybrid_portfolio_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create portfolio box for hybrid case (execution completed BUT errors present).

        This is a CRITICAL state that should never occur in production.
        Indicates process recovered from error but errors were logged.

        Args:
            scenario: ProcessResult with portfolio_stats AND errors
            box_width: Total box width

        Returns:
            List of formatted box lines with CRITICAL warning (replaces success status)
        """
        scenario_name = scenario.scenario_name[:28]
        portfolio_stats = scenario.tick_loop_results.portfolio_stats
        execution_stats = scenario.tick_loop_results.execution_stats
        cost_breakdown = scenario.tick_loop_results.cost_breakdown

        # Handle case where stats might be None (shouldn't happen in hybrid, but defensive)
        if not portfolio_stats or not execution_stats or not cost_breakdown:
            lines = [
                self._renderer.red(f"❌ {scenario_name}"),
                f"Symbol: {scenario.symbol}",
                "",
                "No portfolio data available",
                "(Execution error)",
                "",
                "",
                "",
                "",
                "",
                "",
                self._renderer.red("⚠️ CRITICAL: Errors detected"),
            ]
            return self._renderer.render_box(lines, box_width)

        # Extract stats
        total_trades = portfolio_stats.total_trades
        winning = portfolio_stats.winning_trades
        losing = portfolio_stats.losing_trades
        win_rate = portfolio_stats.win_rate
        long_trades = portfolio_stats.total_long_trades
        short_trades = portfolio_stats.total_short_trades

        # Calculate P&L
        total_profit = portfolio_stats.total_profit
        total_loss = portfolio_stats.total_loss
        total_pnl = total_profit - total_loss

        # Costs
        spread_cost = cost_breakdown.total_spread_cost
        orders_sent = execution_stats.orders_sent
        orders_executed = execution_stats.orders_executed

        # Get currency
        currency = portfolio_stats.currency

        # Format P&L with color and currency
        if total_pnl >= 0:
            pnl_str = self._renderer.green(
                f"+{format_currency_simple(total_pnl, currency)}")
        else:
            pnl_str = self._renderer.red(
                f"{format_currency_simple(total_pnl, currency)}")

        broker_name = portfolio_stats.broker_name
        configured_currency = portfolio_stats.configured_account_currency
        current_conversion_rate = portfolio_stats.current_conversion_rate
        initial_balance = portfolio_stats.initial_balance
        current_balance = portfolio_stats.current_balance

        initial_balance_str = format_currency_simple(
            portfolio_stats.initial_balance, currency)
        current_balance_str = format_currency_simple(
            portfolio_stats.current_balance, currency)

        if current_balance > initial_balance:
            current_balance_str = self._renderer.green(current_balance_str)
        if current_balance < initial_balance:
            current_balance_str = self._renderer.red(f"{current_balance_str}")

        # Format currency display
        if configured_currency == "auto":
            currency_display = f"{currency} (auto)"
        else:
            currency_display = currency

        # Format conversion rate
        if current_conversion_rate is not None:
            rate_display = f" @ {current_conversion_rate:.4f}"
        else:
            rate_display = ""

        # Create content lines (11 stats + 1 CRITICAL warning)
        if total_trades > 0:
            lines = [
                f"{scenario_name}",
                f"Broker: {broker_name}",
                f"Account: {currency_display}",
                f"Trades executed: {total_trades} ({winning}W/{losing}L)",
                f"Win Rate: {win_rate:.1%}",
                f"P&L: {pnl_str}{rate_display}",
                f"Balance: {current_balance_str}",
                f"Init: {initial_balance_str}",
                f"Spread: {format_currency_simple(spread_cost, currency)}",
                f"Orders Ex/Sent: {orders_executed}/{orders_sent}",
                f"Long/Short: {long_trades}/{short_trades}",
                self._renderer.red("⚠️ CRITICAL: Errors detected"),
            ]
        else:
            lines = [
                f"💰 {scenario_name}",
                "No trades executed",
                f"Orders: {orders_sent} sent",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                self._renderer.red("⚠️ CRITICAL: Errors detected"),
            ]

        return self._renderer.render_box(lines, box_width)

    def _create_scenario_error_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create scenario error box for failed scenarios.

        Displays scenario name, symbol, error type, and wrapped error message.
        NO status line (self-explanatory via ❌ and red text).
        More lines available for error message display.

        Args:
            scenario: ProcessResult with success=False
            box_width: Total box width

        Returns:
            List of formatted box lines (10 content lines)
        """
        content_width = box_width - 4
        scenario_name = scenario.scenario_name[:28]
        symbol = scenario.symbol

        # Build content lines
        lines = [
            self._renderer.red(f"❌ {scenario_name}"),
            f"Symbol: {symbol}",
            "",  # Separator
            self._renderer.red(f"Error: {scenario.error_type or 'Unknown'}"),
        ]

        # Add wrapped error message (6 lines available)
        if scenario.error_message:
            remaining_lines = 6  # 10 total - 4 used above
            wrapped_msg = self._wrap_error_message(
                scenario.error_message,
                content_width,
                remaining_lines
            )
            lines.extend(wrapped_msg)

        # Pad to exact 10 lines
        while len(lines) < 10:
            lines.append("")

        # Truncate if too long
        lines = lines[:10]

        return self._renderer.render_box(lines, box_width)

    def _create_portfolio_error_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create portfolio error box for failed scenarios.

        Minimal display - NO redundant error message.
        Error details are shown in scenario box only.

        Args:
            scenario: ProcessResult with success=False
            box_width: Total box width

        Returns:
            List of formatted box lines (12 content lines)
        """
        scenario_name = scenario.scenario_name[:28]
        symbol = scenario.symbol

        # Minimal error display - NO redundant error message
        lines = [
            self._renderer.red(f"❌ {scenario_name}"),
            f"Symbol: {symbol}",
            "",
            "No portfolio data available",
            "(Validation failed)",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]

        return self._renderer.render_box(lines, box_width)

    def _wrap_error_message(self, message: str, width: int, max_lines: int) -> List[str]:
        """
        Wrap error message to fit box width with intelligent word breaking.

        Preserves word boundaries when possible. Truncates with "... (see log)"
        marker if message exceeds available lines.

        Args:
            message: Error message to wrap
            width: Maximum line width (characters)
            max_lines: Maximum number of lines

        Returns:
            List of wrapped message lines
        """
        words = message.split()
        lines = []
        current_line = ""

        for word in words:
            # Check if adding word would exceed width
            test_line = f"{current_line} {word}".strip()

            if len(test_line) <= width:
                current_line = test_line
            else:
                # Line would be too long
                if current_line:
                    lines.append(current_line)
                    if len(lines) >= max_lines - 1:  # Reserve last line for truncation marker
                        break

                # Start new line with current word
                if len(word) > width:
                    # Word itself is too long - truncate it
                    current_line = word[:width-3] + "..."
                else:
                    current_line = word

        # Add remaining line
        if current_line and len(lines) < max_lines:
            lines.append(current_line)

        # Add truncation marker if needed
        if len(words) > 0 and len(lines) == max_lines - 1:
            lines.append("... (see log)")

        return lines
