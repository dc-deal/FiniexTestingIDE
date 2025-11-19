"""
FiniexTestingIDE - Console Renderer
Unified console rendering with proper box formatting

Provides:
- Symmetric box rendering (fixed alignment bug)
- Color utilities (ANSI codes)
- Grid layouts for scenarios and portfolio
- Formatted text output

CRITICAL: All box rendering accounts for ANSI color codes in string length.
FULLY TYPED: Works with typed dataclasses instead of dicts.
FIXED: _create_scenario_box() uses BatchPerformanceStats correctly.
"""

import re
from typing import List
from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.process_data_types import ProcessResult, TickRangeStats
from python.framework.utils.time_utils import format_duration


class ConsoleRenderer:
    """
    Unified console renderer for all summary outputs.

    Handles:
    - Box rendering with perfect symmetry
    - Color formatting
    - Grid layouts
    - Section separators
    """

    # ANSI Color Codes
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    def __init__(self):
        """Initialize console renderer."""
        pass

    # ============================================
    # Color Utilities
    # ============================================

    def red(self, text: str) -> str:
        """Color text red."""
        return f"{self.RED}{text}{self.RESET}"

    def yellow(self, text: str) -> str:
        """Color text yellow."""
        return f"{self.YELLOW}{text}{self.RESET}"

    def blue(self, text: str) -> str:
        """Color text blue."""
        return f"{self.BLUE}{text}{self.RESET}"

    def green(self, text: str) -> str:
        """Color text green."""
        return f"{self.GREEN}{text}{self.RESET}"

    def gray(self, text: str) -> str:
        """Color text gray."""
        return f"{self.GRAY}{text}{self.RESET}"

    def bold(self, text: str) -> str:
        """Make text bold."""
        return f"{self.BOLD}{text}{self.RESET}"

    # ============================================
    # Section Formatting
    # ============================================

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Print section header."""
        print(f"\n{self.BOLD}{char * width}{self.RESET}")
        print(f"{self.BOLD}{title.center(width)}{self.RESET}")
        print(f"{self.BOLD}{char * width}{self.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Print section separator."""
        print(f"{char * width}")

    def print_separator(self, width: int = 60, char: str = "-"):
        """Print simple separator line."""
        print(char * width)

    def print_bold(self, text: str):
        """Print bold text."""
        print(self.bold(text))

    # ============================================
    # String Utilities (ANSI-aware)
    # ============================================

    def strip_ansi(self, text: str) -> str:
        """
        Remove ANSI color codes from string.

        Args:
            text: String potentially containing ANSI codes

        Returns:
            String without ANSI codes
        """
        ansi_escape = re.compile(r'\033\[[0-9;]*m')
        return ansi_escape.sub('', text)

    def visual_length(self, text: str) -> int:
        """
        Get visual length of string (excluding ANSI codes).

        Args:
            text: String to measure

        Returns:
            Visual length (what user sees)
        """
        return len(self.strip_ansi(text))

    def pad_line(self, text: str, width: int) -> str:
        """
        Pad line to exact visual width.

        Args:
            text: String to pad (may contain ANSI codes)
            width: Target visual width

        Returns:
            Padded string with exact visual width
        """
        visual_len = self.visual_length(text)

        if visual_len > width:
            # Truncate if too long (preserve ANSI codes at start)
            stripped = self.strip_ansi(text)
            return text[:len(text) - len(stripped) + width]

        # Add padding
        padding = ' ' * (width - visual_len)
        return text + padding

    # ============================================
    # Box Rendering (Symmetric)
    # ============================================

    def render_box(self, lines: List[str], box_width: int = 38) -> List[str]:
        """
        Render symmetric box around lines.

        Args:
            lines: Lines of text (may contain ANSI codes)
            box_width: Total box width (including borders)

        Returns:
            List of box lines ready to print
        """
        content_width = box_width - 4  # Account for "│ " and " │"

        box_lines = []

        # Top border
        box_lines.append(f"┌{'─' * (box_width - 2)}┐")

        # Content lines
        for line in lines:
            padded = self.pad_line(line, content_width)
            box_lines.append(f"│ {padded} │")

        # Bottom border
        box_lines.append(f"└{'─' * (box_width - 2)}┘")

        return box_lines

    # ============================================
    # Time Formatting
    # ============================================

    def format_tick_timespan(self, stats: TickRangeStats) -> str:
        """
        Format tick time range in human-readable format.

        Args:
            first_tick_time: First tick timestamp
            last_tick_time: Last tick timestamp
            tick_timespan_seconds: Duration in seconds

        Returns:
            Formatted time range string
        """

        if not stats.first_tick_time or not stats.last_tick_time:
            return "N/A"

        # Check if same day
        same_day = stats.first_tick_time.date() == stats.last_tick_time.date()

        if same_day:
            # Same day: "20:00:00 → 20:30:28 (30m 28s)"
            start_time = stats.first_tick_time.strftime("%H:%M:%S")
            end_time = stats.last_tick_time.strftime("%H:%M:%S")
            duration = format_duration(stats.tick_timespan_seconds)
            return f"{start_time} → {end_time} ({duration})"
        else:
            # Different days: "Oct 09 20:00 → Oct 10 02:15 (6h 15m)"
            start_time = stats.first_tick_time.strftime("%b %d %H:%M")
            end_time = stats.last_tick_time.strftime("%b %d %H:%M")
            duration = format_duration(stats.tick_timespan_seconds)
            return f"{start_time} → {end_time} ({duration})"

    # ============================================
    # Grid Rendering
    # ============================================

    def render_scenario_grid(self, scenarios: List[ProcessResult], columns: int = 3, box_width: int = 38):
        """
        Render scenarios in grid layout.

        Args:
            scenarios: List of Scenario objects
            columns: Number of columns in grid
            box_width: Width of each box
        """
        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Create lines for each box
            all_boxes = []

            for scenario in row_scenarios:
                box_lines = self._create_scenario_box(scenario, box_width)
                all_boxes.append(box_lines)

            # Print boxes side by side
            max_lines = max(len(box) for box in all_boxes)

            for line_idx in range(max_lines):
                line_parts = []
                for box in all_boxes:
                    if line_idx < len(box):
                        line_parts.append(box[line_idx])
                    else:
                        line_parts.append(' ' * box_width)

                print("  ".join(line_parts))

            print()  # Empty line between rows

    def _create_scenario_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create box lines for single scenario.

        Args:
            scenario: ProcessResult object
            box_width: Width of box

        Returns:
            List of box lines
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
            tick_time_range = self.format_tick_timespan(stats)
        execution_time = format_duration(
            scenario.execution_time_ms, True)
        # Create content lines
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
        ]

        return self.render_box(lines, box_width)

    def render_portfolio_grid(self,  scenarios: List[ProcessResult], columns: int = 3, box_width: int = 38):
        """
        Render portfolio stats in grid layout.

        Args:
            scenarios: List of scenario result dicts with portfolio stats
            columns: Number of columns in grid
            box_width: Width of each box
        """
        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Create lines for each box
            all_boxes = []

            for scenario in row_scenarios:
                box_lines = self._create_portfolio_box(scenario, box_width)
                all_boxes.append(box_lines)

            # Print boxes side by side
            max_lines = max(len(box) for box in all_boxes)

            for line_idx in range(max_lines):
                line_parts = []
                for box in all_boxes:
                    if line_idx < len(box):
                        line_parts.append(box[line_idx])
                    else:
                        line_parts.append(' ' * box_width)

                print("  ".join(line_parts))

            print()  # Empty line between rows

    def _create_portfolio_box(self, scenario: ProcessResult, box_width: int) -> List[str]:
        """
        Create box lines for portfolio stats.

        Args:
            scenario: ProcessResult object
            box_width: Width of box

        Returns:
            List of box lines
        """
        scenario_name = scenario.scenario_name[:28]
        portfolio_stats = scenario.tick_loop_results.portfolio_stats
        execution_stats = scenario.tick_loop_results.execution_stats
        cost_breakdown = scenario.tick_loop_results.cost_breakdown

        # Handle case where stats might be None
        if not portfolio_stats or not execution_stats or not cost_breakdown:
            lines = [
                f"💰 {scenario_name}",
                "No statistics available",
                "",
                "",
                "",
                ""
            ]
            return self.render_box(lines, box_width)

        # Extract stats (direct attribute access for typed dataclasses)
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

        # Get currency from portfolio stats
        currency = portfolio_stats.currency

        # Format P&L with color and currency
        if total_pnl >= 0:
            pnl_str = self.green(
                f"+{format_currency_simple(total_pnl, currency)}")
        else:
            pnl_str = self.red(
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
            current_balance_str = self.green(current_balance_str)
        if current_balance < initial_balance:
            current_balance_str = self.red(f"{current_balance_str}")

        # Format currency display
        if configured_currency == "auto":
            currency_display = f"{currency} (auto)"
        else:
            currency_display = currency

        # Format conversion rate (only for Base == Account scenarios)
        if current_conversion_rate is not None:
            # Show conversion rate used
            rate_display = f" @ {current_conversion_rate:.4f}"
        else:
            rate_display = ""

        # Create content lines
        if total_trades > 0:
            lines = [
                f"{scenario_name}",  # MODIFIED - Add broker
                f"Broker: {broker_name}",
                f"Account: {currency_display}",
                f"Trades executed: {total_trades} ({winning}W/{losing}L)",
                f"Win Rate: {win_rate:.1%}",
                # MODIFIED - Add rate if applicable
                f"P&L: {pnl_str}{rate_display}",
                f"Balance: {current_balance_str}",
                f"Init: {initial_balance_str}",
                f"Spread: {format_currency_simple(spread_cost, currency)}",
                f"Orders Ex/Sent: {orders_executed}/{orders_sent}",
                f"Long/Short: {long_trades}/{short_trades}",
            ]
        else:
            lines = [
                f"💰 {scenario_name}",
                "No trades executed",
                f"Orders: {orders_sent} sent",
                "",
                "",
                ""
            ]

        return self.render_box(lines, box_width)
