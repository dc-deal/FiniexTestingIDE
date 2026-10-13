"""
FiniexTestingIDE - Operator Alert Messages

The texts the operator reads (#235), rendered from what the session already decided: the run's
grade and exit code from the result, the money figures from the run summary the report built.
Nothing is computed here — a P&L re-derived for a phone notification would be a second
definition of it, and the two would drift apart.
"""

from typing import Dict, Optional, Tuple

from python.framework.types.api.report_types import RunSummary
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlert,
    OperatorAlertLevel,
)
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome

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
        Mark and title, the detail when there is one, and the speaker with the UTC time the
        session raised it
    """
    lines = [f'{LEVEL_MARK[alert.level]} {alert.title}']
    if alert.detail:
        lines.append(alert.detail)
    lines.append(f'{session_label} · {alert.raised_at:%Y-%m-%d %H:%M:%S} UTC')
    return '\n'.join(lines)


def session_label(config: AutoTraderConfig) -> str:
    """
    The speaker line every message ends with.

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
    code = f' · commit {commit[:8]}' if commit else ''
    return 'Session started', f'{mode}\nrun {run_id}{code}'


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
        lines.append(f'refused at the first tick: {result.emergency_reason}')
    elif result.emergency_reason:
        lines.append(result.emergency_reason)
    elif end_reason:
        lines.append(end_reason)
    activity = [format_duration(result.session_duration_s), f'{result.ticks_processed:,} ticks']
    if run_summary is None:
        activity.append('figures unavailable — see the run summary')
    else:
        for row in run_summary.currencies:
            activity.append(f'{row.total_trades} trade(s)')
            activity.append(f'net P&L {row.net_pnl:+.2f} {row.currency}')
            # Realised and valued are kept apart (#492): a holding left open is not in net_pnl
            if row.unrealized_pnl:
                activity.append(f'open {row.unrealized_pnl:+.2f} {row.currency}')
    errors = result.count_logged(LogLevel.ERROR)
    if errors:
        activity.append(f'{errors} error(s) logged')
    lines.append(' · '.join(activity))
    return level, title, '\n'.join(lines)


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
