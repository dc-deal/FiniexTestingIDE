"""
FiniexTestingIDE - Deployment History (#497)

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

Until this unit there was no reader at all: the ledger's only consumer filters on `sweep_id`,
which a live session does not have, so its row was written and unreachable. That is what §44's
own report rule calls out — a store with no read path.

What it does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints answer, and whether a gap
was acceptable is a judgement a person makes. All three are shown, none is collapsed into a
verdict (#527 owns the richer surface; this is the console floor beneath it).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from python.framework.types.api.report_types import RunResultRow


@dataclass
class DeploymentSessionRow:
    """
    One session of a deployment, as the history reads it.

    Args:
        index: Position in the deployment, 1-based
        run_id: The session's own run identity
        started: Its start, parsed from the run timestamp
        net_pnl: Realised P&L of that session
        max_drawdown: The CUMULATIVE account drawdown as of that session — a live row carries
            the running figure against the inherited peak, never that session's own
        max_drawdown_pct: Its share of the peak standing at the time
        currency: The row's account currency
        gap_hours: Hours between the previous session's row and this one, None for the first
        strategy_changed: True when `param_hash` differs from the previous session's
        operation_changed: True when `profile_hash` differs from the previous session's
    """
    index: int
    run_id: str
    started: Optional[datetime]
    net_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    currency: str
    gap_hours: Optional[float] = None
    strategy_changed: bool = False
    operation_changed: bool = False


def build_deployment_histories(rows: List[RunResultRow]) -> Dict[str, List[DeploymentSessionRow]]:
    """
    Group ledger rows into deployments and mark what changed between their sessions.

    DERIVE, not PRESENT: the console, a future API route and the viewer all want this
    grouping, and three groupings are three chances to disagree (§12).

    Rows without a deployment are skipped rather than collected under a placeholder key — a
    one-off session belongs to no history, and inventing one for it would be the same untruth
    as a default that claims a continuity nobody declared.

    Args:
        rows: Ledger rows, in any order

    Returns:
        Deployment identity → its sessions, ordered by run timestamp
    """
    grouped: Dict[str, List[RunResultRow]] = {}
    for row in rows:
        if not row.deployment_id:
            continue
        grouped.setdefault(row.deployment_id, []).append(row)

    histories: Dict[str, List[DeploymentSessionRow]] = {}
    for deployment, members in grouped.items():
        # One row per (run × currency); a multi-currency session would appear twice, so the
        # first row per run wins and the rest are dropped rather than double-counted.
        by_run: Dict[str, RunResultRow] = {}
        for row in sorted(members, key=lambda r: r.run_timestamp):
            by_run.setdefault(row.run_id, row)

        sessions: List[DeploymentSessionRow] = []
        previous: Optional[RunResultRow] = None
        for position, row in enumerate(sorted(by_run.values(), key=lambda r: r.run_timestamp), 1):
            started = _parse(row.run_timestamp)
            previous_started = _parse(previous.run_timestamp) if previous else None
            sessions.append(DeploymentSessionRow(
                index=position,
                run_id=row.run_id,
                started=started,
                net_pnl=row.net_pnl,
                max_drawdown=row.account_max_drawdown,
                max_drawdown_pct=row.account_max_drawdown_pct,
                currency=row.currency,
                gap_hours=(
                    (started - previous_started).total_seconds() / 3600
                    if started and previous_started else None),
                strategy_changed=bool(previous and previous.param_hash != row.param_hash),
                operation_changed=bool(previous and previous.profile_hash != row.profile_hash),
            ))
            previous = row
        histories[deployment] = sessions
    return histories


def render_deployment_history(
    deployment: str, sessions: List[DeploymentSessionRow]) -> None:
    """
    Print one deployment's sessions as a table.

    Formatting only — every value it shows was derived above (§12).

    Args:
        deployment: The deployment identity
        sessions: Its sessions, ordered
    """
    currency = sessions[0].currency if sessions else ''
    first = sessions[0].started.strftime('%Y-%m-%d %H:%M') if sessions and sessions[0].started else '?'
    print(f'\n{deployment} — {len(sessions)} session(s) · since {first} UTC')
    print('─' * 96)
    print(f'{"#":>3}  {"started":<17} {"net P&L":>12} {"max DD (cum)":>14} '
          f'{"share":>8}   notes')

    for session in sessions:
        started = session.started.strftime('%Y-%m-%d %H:%M') if session.started else '?'
        notes = []
        # A gap is reported, never judged: no threshold here decides what is "too long" —
        # that is a judgement about the market and the bot, not about the data.
        if session.gap_hours is not None and session.gap_hours >= 24:
            notes.append(f'gap {session.gap_hours / 24:.1f} d')
        if session.strategy_changed:
            notes.append('⚠ strategy changed')
        if session.operation_changed:
            notes.append('⚠ operation changed')
        print(f'{session.index:>3}  {started:<17} {session.net_pnl:>12.2f} '
              f'{-abs(session.max_drawdown):>14.2f} {session.max_drawdown_pct:>7.2f}%   '
              f'{" · ".join(notes)}')

    print('─' * 96)
    print(f'Amounts in {currency}. The drawdown column is CUMULATIVE over the deployment — each row')
    print('carries the running figure against the inherited peak, so max() is its reduction and a')
    print('sum double-counts. A gap and a changed hash are REPORTED, never judged.')


def _parse(timestamp: str) -> Optional[datetime]:
    """
    Parse a stored ISO timestamp, tolerating a row that has none.

    Args:
        timestamp: The row's `run_timestamp`, stored verbatim

    Returns:
        The parsed datetime, or None when it cannot be read
    """
    try:
        return datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None
