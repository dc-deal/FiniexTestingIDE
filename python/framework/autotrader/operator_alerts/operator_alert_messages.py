"""
FiniexTestingIDE - Operator Alert Messages

The texts the operator reads (#235), rendered from what the session already decided: the run's
grade and exit code from the result, the money figures from the run summary the report built.
Nothing is computed here — a P&L re-derived for a phone notification would be a second
definition of it, and the two would drift apart.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlert,
    OperatorAlertLevel,
)
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.run_results_types import BookingPeriod

# The mark opening every message — the one thing a notification list shows besides the title.
LEVEL_MARK: Dict[OperatorAlertLevel, str] = {
    OperatorAlertLevel.CRITICAL: '⛔',
    OperatorAlertLevel.WARNING: '⚠️',
    OperatorAlertLevel.INFO: '✅',
}

# How urgent a session's end is, by its grade — the grade is the run's own (#372).
END_LEVEL_BY_OUTCOME: Dict[RunOutcome, OperatorAlertLevel] = {
    RunOutcome.SUCCESS: OperatorAlertLevel.INFO,
    RunOutcome.FINISHED_WITH_ERRORS: OperatorAlertLevel.WARNING,
    RunOutcome.FAILED: OperatorAlertLevel.CRITICAL,
    RunOutcome.CRASHED: OperatorAlertLevel.CRITICAL,
}


def render_alert_text(alert: OperatorAlert, session_label: str) -> str:
    """
    The message as the channel sends it.

    Args:
        alert: The alert
        session_label: Who is speaking — several bots may share one chat

    Returns:
        Mark and title, the detail when there is one, the speaker, and on a line of its own the
        UTC time the session raised it — a phone breaks a long last line wherever it likes
    """
    lines = [f'{LEVEL_MARK[alert.level]} {alert.title}']
    if alert.detail:
        lines.append(alert.detail)
    lines.append(session_label)
    lines.append(f'{alert.raised_at:%Y-%m-%d %H:%M:%S} UTC')
    return '\n'.join(lines)


def session_label(config: AutoTraderConfig) -> str:
    """
    The speaker line every message carries, right above the time it was raised.

    Args:
        config: The session's resolved profile

    Returns:
        Unit name, broker and symbol
    """
    return f'{config.get_unit_name()} · {config.broker_type} {config.symbol}'


def session_started_text(config: AutoTraderConfig, dry_run: bool, run_id: str,
                         commit: str) -> Tuple[str, str]:
    """
    The message a session sends when its tick loop starts.

    Args:
        config: The session's resolved profile
        dry_run: Whether orders are only simulated
        run_id: The run's id
        commit: The commit the session runs, '' when unknown

    Returns:
        Title and detail
    """
    if config.adapter_type == 'mock':
        mode = 'mock replay — no venue'
    elif dry_run:
        mode = 'dry run — nothing is placed at the venue'
    else:
        mode = 'LIVE — real orders'
    code = f'\nCommit: {commit[:8]}' if commit else ''
    return 'Session started', f'{mode}\nRun: {run_id}{code}'


def session_ended_text(result: AutoTraderResult, run_summary: Optional[RunSummary],
                       end_reason: str = '') -> Tuple[OperatorAlertLevel, str, str]:
    """
    The message a session sends when it ends: its grade, why, and what it did.

    Args:
        result: The session's result
        run_summary: The run's figures as the report built them — None when the report could
            not be built, and then the message says so instead of showing numbers
        end_reason: Why the session was asked to end — a hard stop, a strategy's request —
            as the executor recorded it; '' when nothing asked it to

    Returns:
        Level, title and detail
    """
    outcome = result.get_outcome()
    level = END_LEVEL_BY_OUTCOME[outcome]
    if result.operator_interrupted:
        title = f'Session stopped by the operator — {outcome.value} (exit {result.get_exit_code()})'
    else:
        title = f'Session ended — {outcome.value.upper()} (exit {result.get_exit_code()})'

    lines = []
    if result.refused_at_first_tick:
        lines.append(f'Refused at the first tick: {result.emergency_reason}')
    elif result.emergency_reason:
        lines.append(f'Reason: {result.emergency_reason}')
    elif end_reason:
        lines.append(f'Reason: {end_reason}')
    lines.append(f'Duration: {format_duration(result.session_duration_s)}')
    lines.append(f'Ticks: {result.ticks_processed:,}')
    if run_summary is None:
        lines.append('Figures: unavailable — see the run summary')
    else:
        for row in run_summary.currencies:
            lines.extend(_figures_lines(row))
    errors = result.count_logged(LogLevel.ERROR)
    if errors:
        lines.append(f'Errors logged: {errors}')
    return level, title, '\n'.join(lines)


# How late a period may open after its trading day did and still count as the whole day. A day
# that ran through its boundary opens the next period within a pass of it; a session started
# later opens its first period at its start, and that day's message covers only the rest.
_WHOLE_DAY_TOLERANCE = timedelta(minutes=1)


def day_closed_text(period: BookingPeriod, trading_day: date,
                    day_opened_at: datetime) -> Tuple[str, str]:
    """
    The message a session sends when a trading day closes: the booking period that day sealed.

    A second distribution of the period, beside its line in the session log and its ledger row
    — it formats the period's own figures and computes none.

    Args:
        period: The booking period the day's boundary sealed
        trading_day: The trading day it covered, in the market's own calendar
        day_opened_at: When that trading day opened (UTC)

    Returns:
        Title and detail, one figure per line — first, when the session started during the day,
        the line that says the figures cover only the rest of it
    """
    figures = period.figures
    # rounded first, so a dip below a cent reads 0.00 rather than -0.00
    dip = round(abs(period.period_max_drawdown), 2)
    lines = []
    if period.opened_at - day_opened_at > _WHOLE_DAY_TOLERANCE:
        lines.append(f'Covers: from {period.opened_at:%H:%M} UTC — the session started '
                     f'during the day')
    lines += _figures_lines(figures) + [
        f'Fees: {figures.total_fees:,.2f} {figures.currency}',
        _equity_line(period),
        # a day that never fell reads 0.00, not -0.00
        f'Deepest dip: {-dip if dip else 0.0:,.2f} {figures.currency}',
    ]
    return f'Day closed — {trading_day.isoformat()}', '\n'.join(lines)


def _figures_lines(row: RunSummaryCurrency) -> List[str]:
    """
    One account currency's trades and P&L, as both the day and the session end read them.

    Args:
        row: The figures of one account currency

    Returns:
        One line each, realised and valued kept apart (#492): a holding left open is not in
        net_pnl
    """
    lines = [f'Trades: {row.total_trades}', f'Net P&L: {row.net_pnl:+,.2f} {row.currency}']
    if row.unrealized_pnl:
        lines.append(f'Open P&L: {row.unrealized_pnl:+,.2f} {row.currency}')
    return lines


def _equity_line(period: BookingPeriod) -> str:
    """
    The account's value across the day: what it opened with and what it closed at.

    Args:
        period: The sealed booking period

    Returns:
        'Equity: 933.49 → 907.57 USD', or which end of it was not observed
    """
    currency = period.figures.currency
    opening = period.period_opening_equity
    closing = period.figures.final_equity
    if opening is not None and closing is not None:
        return f'Equity: {opening:,.2f} → {closing:,.2f} {currency}'
    if closing is not None:
        return f'Equity: {closing:,.2f} {currency} at the close, the open was not observed'
    if opening is not None:
        return f'Equity: {opening:,.2f} {currency} at the open, the close was not observed'
    return 'Equity: not observed'


def format_duration(seconds: float) -> str:
    """
    A session's length the way a person reads it.

    Args:
        seconds: The length in seconds

    Returns:
        '2d 4h 12m', '4h 12m', '12m 5s' or '5s'
    """
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f'{days}d {hours}h {minutes}m'
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {secs}s'
    return f'{secs}s'
