"""
Deployment history builder (#497 / #539) — ledger rows read back as the life of one bot.

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

This is DERIVE (§12). It lived in the console module until #539 asked for the same history over
HTTP; two derivations of one deployment are two chances to disagree about what its drawdown is,
so the grouping, the marks and the roll-up moved here and the renderers kept only their columns.

What it does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints answer, and whether a gap
was acceptable is a judgement a person makes. All three are reported, none is collapsed into a
verdict (#527 owns the richer surface; the console is the floor beneath it).
"""

from datetime import datetime
from typing import Dict, List, Optional

from python.framework.reporting.store.ledger_aggregation import aggregate_ledger_rows
from python.framework.types.api.report_types import (
    DeploymentComparabilityAdvisory,
    DeploymentSessionRow,
    DeploymentSummary,
    RunResultRow,
)


def summarize_deployments(
    histories: Dict[str, List[DeploymentSessionRow]],
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
) -> List[DeploymentSummary]:
    """
    Reduce each deployment to one line, newest first.

    DERIVE, not PRESENT (§12): the console, the API route and the viewer (#527) all want
    this roll-up, and three roll-ups are three chances to disagree about what a deployment's
    drawdown is.

    Args:
        histories: Deployment identity → its sessions, ordered
        advisories: Deployment identity → its comparability advisory, or None

    Returns:
        One summary per (deployment × account currency), most recent first. Per currency and
        not per deployment: a P&L column added up over two currencies is not a number, and the
        row count would report a two-currency bot as having run twice as often
    """
    summaries: List[DeploymentSummary] = []
    for deployment, all_sessions in histories.items():
        by_currency: Dict[str, List[DeploymentSessionRow]] = {}
        for session in all_sessions:
            by_currency.setdefault(session.currency, []).append(session)
        for sessions in by_currency.values():
            if not sessions:
                continue
            summaries.append(_summarize_one(deployment, sessions, advisories))
    return sorted(summaries, key=lambda s: (s.deployment_id, s.currency), reverse=True)


def _summarize_one(
    deployment: str,
    sessions: List[DeploymentSessionRow],
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
) -> DeploymentSummary:
    """
    One deployment's sessions in ONE currency, as a single line.

    Args:
        deployment: The deployment identity
        sessions: Its sessions in one currency, ordered
        advisories: Deployment identity → its comparability advisory, or None

    Returns:
        The summary line
    """
    # max(), never sum(): each live row carries the RUNNING decline against the inherited
    # peak, so adding them counts one decline once per session that was still inside it.
    deepest = max(sessions, key=lambda s: abs(s.max_drawdown))
    gaps = [s.gap_hours for s in sessions if s.gap_hours is not None]
    return DeploymentSummary(
        deployment_id=deployment,
        sessions=len(sessions),
        first_started=sessions[0].started,
        last_started=sessions[-1].started,
        net_pnl=sum(s.net_pnl for s in sessions),
        max_drawdown=deepest.max_drawdown,
        max_drawdown_pct=deepest.max_drawdown_pct,
        currency=sessions[0].currency,
        bot=sessions[0].bot,
        longest_gap_hours=max(gaps) if gaps else None,
        changed=advisories.get(deployment) is not None,
    )


def deployment_comparability_advisory(
    rows: List[RunResultRow],
) -> Optional[DeploymentComparabilityAdvisory]:
    """
    Report whether one deployment's sessions form a comparable series.

    Returns None when they do — one strategy stand and one operational stand — because a
    warning that fires on the normal case is a warning that gets skipped.

    Args:
        rows: The ledger rows of ONE deployment

    Returns:
        The advisory, or None when nothing changed across the deployment
    """
    if len(rows) < 2:
        return None
    strategy = {r.param_hash for r in rows if r.param_hash}
    operation = {r.profile_hash for r in rows if r.profile_hash}
    if len(strategy) < 2 and len(operation) < 2:
        return None
    return DeploymentComparabilityAdvisory(
        # Distinct RUNS, not rows. A row is one (run × account currency), so counting rows
        # reported a two-currency session as two sessions — and the number is rendered as
        # "over N sessions", i.e. the denominator of the sentence that tells the operator
        # how much of their deployment the advisory is about.
        sessions=len({r.run_id for r in rows}),
        strategy_stands=max(1, len(strategy)),
        operation_stands=max(1, len(operation)),
    )


def build_deployment_histories(rows: List[RunResultRow]) -> Dict[str, List[DeploymentSessionRow]]:
    """
    Group ledger rows into deployments and mark what changed between their sessions.

    DERIVE, not PRESENT: the console, the API route and the viewer all want this grouping,
    and three groupings are three chances to disagree (§12).

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
        # One row per (run × currency), COMBINED rather than picked. Since #537 a session writes
        # one row per booking period, so a run has many rows and the first-one-wins rule that
        # used to guard against multi-currency double counting would now discard twenty-nine
        # days of a thirty-day session — silently, because the survivor looks like a session.
        #
        # `aggregate_ledger_rows` folds them by their DECLARED reductions, so the session row is
        # recomputed from its periods instead of being stored beside them. The currency stays in
        # the key: a P&L column over two currencies is not a number.
        by_session = aggregate_ledger_rows(members, by=('run_id', 'currency'))

        sessions: List[DeploymentSessionRow] = []
        previous: Optional[RunResultRow] = None
        for position, row in enumerate(
                sorted(by_session, key=lambda r: (r.currency, r.run_timestamp)), 1):
            started = _parse(row.run_timestamp)
            # The gap runs from when the PREDECESSOR'S ROW WAS WRITTEN — i.e. from the end of
            # that session — to this one's start. Measuring start-to-start instead counts the
            # predecessor's whole runtime as downtime: a bot that ran 06:00-18:00 and came
            # back at 19:00 would read as a 13-hour gap rather than a one-hour one. Rows
            # written before `recorded_at_utc` existed fall back to start-to-start and say so.
            ended = _parse(previous.recorded_at_utc) if previous else None
            fell_back = previous is not None and ended is None
            if fell_back:
                ended = _parse(previous.run_timestamp)
            sessions.append(DeploymentSessionRow(
                index=position,
                run_id=row.run_id,
                started=started,
                net_pnl=row.net_pnl,
                max_drawdown=row.account_max_drawdown,
                max_drawdown_pct=row.account_max_drawdown_pct,
                currency=row.currency,
                bot=row.scenario_set_name,
                ended=_parse(row.recorded_at_utc),
                ran_hours=_ran_hours(started, _parse(row.recorded_at_utc)),
                gap_hours=(
                    (started - ended).total_seconds() / 3600
                    if started and ended else None),
                gap_between_starts=fell_back,
                strategy_changed=bool(previous and previous.param_hash != row.param_hash),
                operation_changed=bool(previous and previous.profile_hash != row.profile_hash),
            ))
            previous = row
        histories[deployment] = sessions
    return histories


def _ran_hours(started: Optional[datetime], ended: Optional[datetime]) -> Optional[float]:
    """
    How long a session ran, when both ends are known.

    Args:
        started: Its start
        ended: When its ledger row was written, which is its close

    Returns:
        Hours, or None when either end is missing — an unmeasurable duration reports as
        unmeasured rather than as a number nobody can check
    """
    if started is None or ended is None:
        return None
    return (ended - started).total_seconds() / 3600


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
