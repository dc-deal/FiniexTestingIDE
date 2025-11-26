"""
FiniexTestingIDE - Scenario Box Builder
Builds scenario statistics boxes

Handles:
- Success boxes (scenario execution details)
- Error boxes (validation/execution failures)
- Hybrid boxes (partial execution with errors)
- Conditional status line rendering

All box types maintain identical line count for grid alignment.
"""

from typing import List
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.reporting.grid.console_grid_renderer import render_box
from python.framework.types.process_data_types import PostProcessResult, ProcessResult
from python.framework.utils.time_utils import format_duration, format_tick_timespan


def create_scenario_box(
    scenario: PostProcessResult,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Create scenario box - dispatches to appropriate builder.

    Determines box type based on execution state:
    - Success: Normal statistics
    - Error only: Validation/preparation failure
    - Hybrid: Runtime error during execution

    Args:
        scenario: ProcessResult object
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines
    """
    # Check for errors first
    if scenario.error_type or scenario.error_message:
        # Hybrid case: execution started but errors occurred
        if scenario.tick_loop_results:
            return _build_hybrid_scenario_box(scenario, box_width, show_status_line)
        else:
            # Pure error: preparation/validation failed
            return _build_error_scenario_box(scenario, box_width, show_status_line)

    # Normal success case
    return _build_success_scenario_box(scenario, box_width, show_status_line)


def _build_success_scenario_box(
    scenario: PostProcessResult,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build scenario box for successful execution.

    Args:
        scenario: ProcessResult with success=True
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (9 stats + optional status)
    """
    renderer = ConsoleRenderer()

    performance_stats = scenario.tick_loop_results.performance_stats
    decision_statistics = scenario.tick_loop_results.decision_statistics
    tick_range_stats = scenario.tick_loop_results.tick_range_stats

    scenario_name = scenario.scenario_name[:28]
    symbol = scenario.single_scenario.symbol
    ticks = performance_stats.ticks_processed
    nfSig = decision_statistics.buy_signals + decision_statistics.sell_signals
    buys = decision_statistics.buy_signals
    sells = decision_statistics.sell_signals
    flats = decision_statistics.flat_signals
    total_workers = performance_stats.total_workers

    # Format duration
    exec_time = scenario.execution_time_ms
    duration_str = format_duration(exec_time)

    # FIXED: Extract fields from tick_range_stats and pass individually
    tick_timespan_str = format_tick_timespan(
        tick_range_stats.first_tick_time,
        tick_range_stats.last_tick_time,
        tick_range_stats.tick_timespan_seconds
    )

    # Non-flat signal percentage
    nf_pct = (nfSig / ticks * 100) if ticks > 0 else 0

    # Trades requested (decision logic signals)
    trades_requested = decision_statistics.trades_requested
    trades_pct = (trades_requested / ticks * 100) if ticks > 0 else 0

    # Create content lines (9 stats)
    lines = [
        f"{scenario_name}",
        f"Symbol: {symbol}",
        f"Duration: {duration_str}",
        f"Ticks: {ticks:,}",
        f"{tick_timespan_str}",
        f"Non-Flat Sign.: {nfSig} ({nf_pct:.1f}%)",
        f"B/S/F: {buys}/{sells}/{flats}",
        f"Trades requested: {trades_requested} ({trades_pct:.1f}%)",
        f"Worker: {total_workers}",
    ]

    # Add status line or empty line (for grid alignment)
    if show_status_line:
        lines.append(renderer.green("✅ Status: Success"))
    else:
        lines.append("")  # Empty line maintains box height

    return render_box(lines, renderer, box_width)


def _build_hybrid_scenario_box(
    scenario: ProcessResult,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build scenario box for partial execution with errors.

    Shows statistics from partial execution + CRITICAL warning.

    Args:
        scenario: ProcessResult with tick_loop_results + errors
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (9 stats + CRITICAL warning)
    """
    renderer = ConsoleRenderer()

    performance_stats = scenario.tick_loop_results.performance_stats
    decision_statistics = scenario.tick_loop_results.decision_statistics
    tick_range_stats = scenario.tick_loop_results.tick_range_stats

    scenario_name = renderer.red(f"❌ {scenario.scenario_name[:28]}")
    symbol = scenario.symbol
    ticks = performance_stats.ticks_processed
    nfSig = decision_statistics.buy_signals + decision_statistics.sell_signals
    buys = decision_statistics.buy_signals
    sells = decision_statistics.sell_signals
    flats = decision_statistics.flat_signals
    total_workers = performance_stats.total_workers

    # Format duration
    exec_time = scenario.execution_time_ms
    duration_str = format_duration(exec_time)

    # FIXED: Extract fields from tick_range_stats and pass individually
    tick_timespan_str = format_tick_timespan(
        tick_range_stats.first_tick_time,
        tick_range_stats.last_tick_time,
        tick_range_stats.tick_timespan_seconds
    )

    # Non-flat signal percentage
    nf_pct = (nfSig / ticks * 100) if ticks > 0 else 0

    # Trades requested
    trades_requested = decision_statistics.trades_requested
    trades_pct = (trades_requested / ticks * 100) if ticks > 0 else 0

    # Create content lines (9 stats)
    lines = [
        f"{scenario_name}",
        f"Symbol: {symbol}",
        f"Duration: {duration_str}",
        f"Ticks: {ticks:,}",
        f"{tick_timespan_str}",
        f"Non-Flat Sign.: {nfSig} ({nf_pct:.1f}%)",
        f"B/S/F: {buys}/{sells}/{flats}",
        f"Trades requested: {trades_requested} ({trades_pct:.1f}%)",
        f"Worker: {total_workers}",
    ]

    # CRITICAL warning (always shown for hybrid)
    lines.append(renderer.red("⚠️ CRITICAL: Errors detected"))

    return render_box(lines, renderer, box_width)


def _build_error_scenario_box(
    scenario: ProcessResult,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build scenario box for failed scenarios.

    Displays error information with wrapped message.
    Status line shows failed state when enabled.

    Args:
        scenario: ProcessResult with success=False
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (10 total)
    """
    renderer = ConsoleRenderer()

    content_width = box_width - 4
    scenario_name = scenario.scenario_name[:28]
    symbol = scenario.symbol

    # Build content lines
    lines = [
        renderer.red(f"❌ {scenario_name}"),
        f"Symbol: {symbol}",
        "",  # Separator
        renderer.red(f"Error: {scenario.error_type or 'Unknown'}"),
    ]

    # Add wrapped error message (5 or 6 lines available)
    if scenario.error_message:
        remaining_lines = 5 if show_status_line else 6
        wrapped_msg = _wrap_error_message(
            scenario.error_message,
            content_width,
            remaining_lines
        )
        lines.extend(wrapped_msg)

    # Add status line or pad to exact line count
    target_lines = 10
    while len(lines) < target_lines - (1 if show_status_line else 0):
        lines.append("")

    # Add status line if enabled
    if show_status_line:
        lines.append(renderer.red("❌ Status: Failed"))
    else:
        lines.append("")  # Empty line maintains box height

    # Ensure exact line count
    lines = lines[:target_lines]

    return render_box(lines, renderer, box_width)


def _wrap_error_message(message: str, width: int, max_lines: int) -> List[str]:
    """
    Wrap error message to fit box width with word breaking.

    Preserves word boundaries when possible.
    Truncates with marker if message exceeds available lines.

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
                if len(lines) >= max_lines - 1:  # Reserve last line for truncation
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
