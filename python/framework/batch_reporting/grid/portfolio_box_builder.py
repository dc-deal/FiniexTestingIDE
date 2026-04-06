"""
FiniexTestingIDE - Portfolio Box Builder
Builds portfolio statistics boxes

Handles:
- Success boxes (portfolio performance details)
- Error boxes (minimal display for failed scenarios)
- Hybrid boxes (partial execution with portfolio data)
- Conditional status line rendering

All box types maintain identical line count for grid alignment.
"""

from typing import List, Optional, Tuple
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.batch_reporting.grid.console_grid_renderer import render_box
from python.framework.types.trading_env_types.currency_codes import format_currency_simple
from python.framework.utils.math_utils import force_negative, force_positive


def create_portfolio_box(
    process_result: ProcessResult,
    scenario: SingleScenario,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Create portfolio box - dispatches to appropriate builder.

    Determines box type based on execution state:
    - Success: Normal portfolio statistics
    - Error only: Minimal error display
    - Hybrid: Portfolio data + CRITICAL warning

    Args:
        process_result: ProcessResult object
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines
    """
    # Check for errors first
    if process_result.error_type or process_result.error_message:
        # Hybrid case: execution started, portfolio data exists
        if process_result.tick_loop_results and process_result.tick_loop_results.portfolio_stats:
            return _build_hybrid_portfolio_box(process_result, scenario, box_width, show_status_line)
        else:
            # Pure error: no portfolio data
            return _build_error_portfolio_box(process_result, scenario, box_width, show_status_line)

    # Normal success case
    return _build_success_portfolio_box(process_result, scenario, box_width, show_status_line)


def _build_success_portfolio_box(
    process_result: ProcessResult,
    scenario: SingleScenario,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build portfolio box for successful execution.

    Args:
        process_result: ProcessResult with success=True
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (12 or 13 stats + optional status)
    """
    renderer = ConsoleRenderer()

    portfolio_stats = process_result.tick_loop_results.portfolio_stats
    execution_stats = process_result.tick_loop_results.execution_stats
    cost_breakdown = process_result.tick_loop_results.cost_breakdown

    scenario_name = process_result.scenario_name[:28]

    # Check for no trades process_result
    if portfolio_stats.total_trades == 0:
        pending_stats = process_result.tick_loop_results.pending_stats
        active_parts = []
        if pending_stats and pending_stats.active_limit_orders:
            active_parts.append(
                f"{len(pending_stats.active_limit_orders)} limits")
        if pending_stats and pending_stats.active_stop_orders:
            active_parts.append(
                f"{len(pending_stats.active_stop_orders)} stops")
        active_line = renderer.cyan(
            f"Active: {' | '.join(active_parts)}") if active_parts else ""

        lines = [
            f"💰 {scenario_name}",
            "No trades executed",
            f"Orders: {execution_stats.orders_sent} sent",
            active_line,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]

        # Add status line or empty line
        if show_status_line:
            lines.append(renderer.green("✅ Status: Success"))
        else:
            lines.append("")

        return render_box(lines, renderer, box_width)

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
    orders_rejected = execution_stats.orders_rejected

    # Currency
    currency = portfolio_stats.currency
    broker_name = portfolio_stats.broker_name
    current_conversion_rate = portfolio_stats.current_conversion_rate

    # Format balance lines (spot-aware)
    balance_line, init_line, spot_pnl_detail = _format_balance_lines(
        portfolio_stats, renderer)

    # Format currency display
    currency_display = currency
    if portfolio_stats.spot_mode:
        currency_display = f"{currency} [SPOT]"

    # Format conversion rate
    if current_conversion_rate is not None:
        rate_display = f" @ {current_conversion_rate:.4f}"
    else:
        rate_display = ""

    # Calculate max drawdown percentage
    max_dd_pct = 0.0
    if portfolio_stats.max_equity > 0:
        max_dd_pct = portfolio_stats.max_drawdown / portfolio_stats.max_equity * 100

    # Broker name truncation
    broker_display = broker_name[:30] if len(broker_name) > 30 else broker_name
    data_source = scenario.data_broker_type

    # Format order execution line
    if orders_rejected > 0:
        orders_line = f"Orders: {orders_executed}/{orders_sent} | {renderer.yellow(f'Rej: {orders_rejected}')}"
    else:
        orders_line = f"Orders Ex/Sent: {orders_executed}/{orders_sent}"

    # Format pending latency line (green)
    pending_stats = process_result.tick_loop_results.pending_stats
    pending_line = _format_pending_latency_line(renderer, pending_stats)

    # Create content lines (14 stats)
    lines = [
        f"{scenario_name}",
        f"Broker: {broker_display}",
        f"Account: {currency_display} | Data: {data_source}",
        f"Trades executed: {total_trades} ({winning}W/{losing}L)",
        f"Win Rate: {win_rate:.1%}",
        f"P&L: {renderer.pnl(total_pnl, currency)}{rate_display}",
        balance_line,
        init_line,
    ]
    if portfolio_stats.spot_mode:
        # Spot: est. P&L line, then risk metrics, compressed tail
        lines.append(spot_pnl_detail)
        lines.append(f"Max DD: {renderer.pnl(force_negative(portfolio_stats.max_drawdown), currency)} ({max_dd_pct:.1f}%)")
        lines.append(f"Max Equity: {renderer.pnl(force_positive(portfolio_stats.max_equity), currency)}")
        lines.append(f"Spread: {format_currency_simple(spread_cost, currency)} | {orders_line}")
        lines.append(pending_line)
        lines.append(f"Long/Short: {long_trades}/{short_trades}")
    else:
        # Margin: original layout
        lines.append(f"Max DD: {renderer.pnl(force_negative(portfolio_stats.max_drawdown), currency)} ({max_dd_pct:.1f}%)")
        lines.append(f"Max Equity: {renderer.pnl(force_positive(portfolio_stats.max_equity), currency)}")
        lines.append(f"Spread: {format_currency_simple(spread_cost, currency)}")
        lines.append(orders_line)
        lines.append(pending_line)
        lines.append(f"Long/Short: {long_trades}/{short_trades}")

    # Add status line or empty line
    if show_status_line:
        lines.append(renderer.green("Status: Success"))
    else:
        lines.append("")

    return render_box(lines, renderer, box_width)


def _build_hybrid_portfolio_box(
    process_result: ProcessResult,
    scenario: SingleScenario,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build portfolio box for partial execution with errors.

    Shows portfolio data from partial execution + CRITICAL warning.

    Args:
        process_result: ProcessResult with portfolio_stats + errors
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (13 stats + CRITICAL warning)
    """
    renderer = ConsoleRenderer()

    portfolio_stats = process_result.tick_loop_results.portfolio_stats
    execution_stats = process_result.tick_loop_results.execution_stats
    cost_breakdown = process_result.tick_loop_results.cost_breakdown

    scenario_name = process_result.scenario_name[:28]

    # Check for no trades process_result
    if portfolio_stats.total_trades == 0:
        pending_stats = process_result.tick_loop_results.pending_stats
        active_parts = []
        if pending_stats and pending_stats.active_limit_orders:
            active_parts.append(
                f"{len(pending_stats.active_limit_orders)} limits")
        if pending_stats and pending_stats.active_stop_orders:
            active_parts.append(
                f"{len(pending_stats.active_stop_orders)} stops")
        active_line = renderer.cyan(
            f"Active: {' | '.join(active_parts)}") if active_parts else ""

        lines = [
            f"💰 {scenario_name}",
            "No trades executed",
            f"Orders: {execution_stats.orders_sent} sent",
            active_line,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]

        return render_box(lines, renderer, box_width)

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

    # Currency
    currency = portfolio_stats.currency
    broker_name = portfolio_stats.broker_name
    current_conversion_rate = portfolio_stats.current_conversion_rate

    # Format balance lines (spot-aware)
    balance_line, init_line, spot_pnl_detail = _format_balance_lines(
        portfolio_stats, renderer)

    # Format currency display
    currency_display = currency
    if portfolio_stats.spot_mode:
        currency_display = f"{currency} [SPOT]"

    # Format conversion rate
    if current_conversion_rate is not None:
        rate_display = f" @ {current_conversion_rate:.4f}"
    else:
        rate_display = ""

    # Calculate max drawdown percentage
    max_dd_pct = 0.0
    if portfolio_stats.max_equity > 0:
        max_dd_pct = portfolio_stats.max_drawdown / portfolio_stats.max_equity * 100

    # Broker name truncation
    broker_display = broker_name[:30] if len(broker_name) > 30 else broker_name
    data_source = scenario.data_broker_type

    # Create content lines (13 stats)
    lines = [
        f"{scenario_name}",
        f"Broker: {broker_display}",
        f"Account: {currency_display} | Data: {data_source}",
        f"Trades executed: {total_trades} ({winning}W/{losing}L)",
        f"Win Rate: {win_rate:.1%}",
        f"P&L: {renderer.pnl(total_pnl, currency)}{rate_display}",
        balance_line,
        init_line,
    ]
    if portfolio_stats.spot_mode:
        lines.append(spot_pnl_detail)
        lines.append(f"Max DD: {renderer.pnl(force_negative(portfolio_stats.max_drawdown), currency)} ({max_dd_pct:.1f}%)")
        lines.append(f"Spread: {format_currency_simple(spread_cost, currency)}")
        lines.append(f"Orders Ex/Sent: {orders_executed}/{orders_sent}")
        lines.append(f"Long/Short: {long_trades}/{short_trades}")
    else:
        lines.append(f"Max DD: {renderer.pnl(force_negative(portfolio_stats.max_drawdown), currency)} ({max_dd_pct:.1f}%)")
        lines.append(f"Max Equity: {renderer.pnl(force_positive(portfolio_stats.max_equity), currency)}")
        lines.append(f"Spread: {format_currency_simple(spread_cost, currency)}")
        lines.append(f"Orders Ex/Sent: {orders_executed}/{orders_sent}")
        lines.append(f"Long/Short: {long_trades}/{short_trades}")

    # CRITICAL warning (always shown for hybrid)
    lines.append(renderer.red("⚠️ CRITICAL: Errors detected"))

    return render_box(lines, renderer, box_width)


def _build_error_portfolio_box(
    process_result: ProcessResult,
    scenario: SingleScenario,
    box_width: int,
    show_status_line: bool
) -> List[str]:
    """
    Build portfolio box for failed scenarios.

    Minimal display - error details shown in process_result box only.

    Args:
        process_result: ProcessResult with success=False
        box_width: Total box width
        show_status_line: Whether to show status line

    Returns:
        List of formatted box lines (14 total)
    """
    renderer = ConsoleRenderer()

    scenario_name = process_result.scenario_name[:28]
    symbol = scenario.symbol

    # Minimal error display
    lines = [
        renderer.red(f"❌ {scenario_name}"),
        f"Symbol: {scenario.data_broker_type}/{symbol}",
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
        "",
    ]

    # Add status line or empty line
    if show_status_line:
        lines.append(renderer.red("Status: Failed"))
    else:
        lines.append("")

    return render_box(lines, renderer, box_width)


# ============================================
# Spot Balance Formatting (shared helper)
# ============================================

def _format_balance_lines(
    portfolio_stats: PortfolioStats,
    renderer: ConsoleRenderer
) -> Tuple[str, str, str]:
    """
    Format balance + init + P&L lines — spot-aware.

    For margin mode: single balance line (unchanged behavior).
    For spot mode: dual-balance line with estimated portfolio value.

    Args:
        portfolio_stats: Portfolio statistics (with spot_mode, balances, last_price)
        renderer: Console renderer for color formatting

    Returns:
        Tuple of (balance_line, init_line, pnl_detail) strings
    """
    currency = portfolio_stats.currency
    initial_balance = portfolio_stats.initial_balance
    current_balance = portfolio_stats.current_balance

    if not portfolio_stats.spot_mode:
        # Margin mode — unchanged
        initial_str = format_currency_simple(initial_balance, currency)
        current_str = format_currency_simple(current_balance, currency)
        if current_balance > initial_balance:
            current_str = renderer.green(current_str)
        elif current_balance < initial_balance:
            current_str = renderer.red(current_str)
        return (
            f"Balance: {current_str}",
            f"Init: {initial_str}",
            ''
        )

    # Spot mode — dual balance display
    symbol = portfolio_stats.symbol
    quote = symbol[-3:] if len(symbol) >= 6 else currency
    base = symbol[:-3] if len(symbol) >= 6 else ''
    balances = portfolio_stats.balances
    initial_balances = portfolio_stats.initial_balances
    last_price = portfolio_stats.last_price

    # Format current balances
    quote_bal = balances.get(quote, 0.0)
    base_bal = balances.get(base, 0.0)
    quote_init = initial_balances.get(quote, 0.0)
    base_init = initial_balances.get(base, 0.0)

    # Determine decimal precision for base asset
    base_fmt = f'{base_bal:,.4f}' if base_bal < 100 else f'{base_bal:,.2f}'
    base_init_fmt = f'{base_init:,.4f}' if base_init < 100 else f'{base_init:,.2f}'

    balance_line = f"Bal: {format_currency_simple(quote_bal, quote)} | {base} {base_fmt}"
    init_line = f"Init: {format_currency_simple(quote_init, quote)} | {base} {base_init_fmt}"

    # Estimated portfolio value
    pnl_detail = ''
    if last_price > 0:
        est_current = quote_bal + (base_bal * last_price)
        est_initial = quote_init + (base_init * last_price)
        est_pnl = est_current - est_initial
        est_pnl_pct = (est_pnl / est_initial * 100) if est_initial > 0 else 0.0
        pnl_sign = '+' if est_pnl >= 0 else ''
        price_str = format_currency_simple(last_price, quote)
        pnl_detail = f"Est: {pnl_sign}{format_currency_simple(est_pnl, quote)} ({pnl_sign}{est_pnl_pct:.2f}%) @ {base} {price_str}"

    return (balance_line, init_line, pnl_detail)


# ============================================
# Pending Latency Formatting (shared helper)
# ============================================

def _format_pending_latency_line(
    renderer: ConsoleRenderer,
    pending_stats: Optional[PendingOrderStats]
) -> str:
    """
    Format pending order latency as a green summary line for box display.

    Adapts unit based on available data (ticks for simulation, ms for live).

    Args:
        renderer: Console renderer for color formatting
        pending_stats: Pending order statistics (may be None)

    Returns:
        Formatted latency line (green) or empty string if no data
    """
    if not pending_stats or pending_stats.total_resolved == 0:
        return ""

    # Millisecond-based latency
    if pending_stats.min_latency_ms is not None:
        avg = pending_stats.avg_latency_ms
        min_val = pending_stats.min_latency_ms
        max_val = pending_stats.max_latency_ms
        latency_str = f"Latency: avg {avg:.0f}ms ({min_val:.0f}-{max_val:.0f})"

        latency_str += _format_anomaly_suffix_compact(renderer, pending_stats)

        return renderer.green(latency_str)

    return ""


def _format_anomaly_suffix_compact(
    renderer: ConsoleRenderer,
    pending_stats: PendingOrderStats
) -> str:
    """Format compact anomaly suffix for box display (e.g. '| 1 forced')."""
    parts = []
    if pending_stats.total_force_closed > 0:
        parts.append(f"{pending_stats.total_force_closed} forced")
    if pending_stats.total_timed_out > 0:
        parts.append(f"{pending_stats.total_timed_out} timeout")
    # Active orders at scenario end (bot's pending plan)
    if pending_stats.active_limit_orders:
        parts.append(f"{len(pending_stats.active_limit_orders)} limits")
    if pending_stats.active_stop_orders:
        parts.append(f"{len(pending_stats.active_stop_orders)} stops")
    if not parts:
        return ""
    # Anomalies in yellow, active orders in cyan
    anomaly_parts = []
    if pending_stats.total_force_closed > 0:
        anomaly_parts.append(f"{pending_stats.total_force_closed} forced")
    if pending_stats.total_timed_out > 0:
        anomaly_parts.append(f"{pending_stats.total_timed_out} timeout")
    active_parts = []
    if pending_stats.active_limit_orders:
        active_parts.append(f"{len(pending_stats.active_limit_orders)} limits")
    if pending_stats.active_stop_orders:
        active_parts.append(f"{len(pending_stats.active_stop_orders)} stops")

    result = ""
    if anomaly_parts:
        result += f" | {renderer.yellow(' | '.join(anomaly_parts))}"
    if active_parts:
        result += f" | {renderer.cyan(' | '.join(active_parts))}"
    return result
