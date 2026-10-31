"""
Deployment history builder (#497 / #539) — ledger rows read back as the life of one bot.

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

This is DERIVE (§12). It lived in the console module until #539 asked for the same history over
HTTP; two derivations of one deployment are two chances to disagree about what its drawdown is,
so the grouping, the marks and the roll-up moved here and the renderers kept only their columns.

What it does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints and the code stands answer,
and whether a gap was acceptable is a judgement a person makes. All three are reported, none is
collapsed into a verdict (#527 owns the richer surface; the console is the floor beneath it).
"""

from datetime import datetime
from typing import Dict, List, Optional

from python.framework.reporting.builders.code_stand_comparison import (
    code_changes_between,
    stands_agree,
)
from python.framework.reporting.store.ledger_aggregation import aggregate_ledger_rows
from python.framework.reporting.store.run_completion_audit import closes_its_run
from python.framework.types.api.report_types import (
    SESSION_KEY,
    DeploymentCodeSummary,
    DeploymentCaption,
    DeploymentComparabilityAdvisory,
    DeploymentSessionRow,
    DeploymentSummary,
    RunResultRow,
)
from python.framework.types.run_origin_types import CodeStand
from python.framework.utils.time_utils import ensure_utc_aware


def summarize_deployments(
    histories: Dict[str, List[DeploymentSessionRow]],
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
    code_summaries: Dict[str, Optional[DeploymentCodeSummary]],
) -> List[DeploymentSummary]:
    """
    Reduce each deployment to one line, newest first.

    DERIVE, not PRESENT (§12): the console, the API route and the viewer (#527) all want
    this roll-up, and three roll-ups are three chances to disagree about what a deployment's
    drawdown is.

    Args:
        histories: Deployment identity → its sessions, ordered
        advisories: Deployment identity → its comparability advisory, or None
        code_summaries: Deployment identity → its code summary, or None

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
            summaries.append(_summarize_one(deployment, sessions, advisories, code_summaries))
    return sorted(summaries, key=lambda s: (s.deployment_id, s.currency), reverse=True)


def _summarize_one(
    deployment: str,
    sessions: List[DeploymentSessionRow],
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
    code_summaries: Dict[str, Optional[DeploymentCodeSummary]],
) -> DeploymentSummary:
    """
    One deployment's sessions in ONE currency, as a single line.

    Args:
        deployment: The deployment identity
        sessions: Its sessions in one currency, ordered
        advisories: Deployment identity → its comparability advisory, or None
        code_summaries: Deployment identity → its code summary, or None

    Returns:
        The summary line
    """
    # max(), never sum(): each live row carries the RUNNING decline against the inherited
    # peak, so adding them counts one decline once per session that was still inside it. Over
    # the sessions that measured one: a session without a drawdown is unknown, not a zero.
    measured = [s for s in sessions if s.max_drawdown is not None]
    deepest = max(measured, key=lambda s: abs(s.max_drawdown)) if measured else None
    gaps = [s.gap_hours for s in sessions if s.gap_hours is not None]
    advisory = advisories.get(deployment)
    code_summary = code_summaries.get(deployment)
    return DeploymentSummary(
        deployment_id=deployment,
        sessions=len(sessions),
        first_started=sessions[0].started,
        last_started=sessions[-1].started,
        net_pnl=sum(s.net_pnl for s in sessions),
        max_drawdown=deepest.max_drawdown if deepest else None,
        max_drawdown_pct=deepest.max_drawdown_pct if deepest else None,
        currency=sessions[0].currency,
        bot=sessions[0].bot,
        bot_id=sessions[0].bot_id,
        longest_gap_hours=max(gaps) if gaps else None,
        changed=advisory is not None or code_summary is not None,
        configuration_changed=advisory is not None,
        code_summary=code_summary,
        orders_to=sorted({s.orders_to for s in sessions if s.orders_to is not None}),
    )


def deployment_caption(sessions: List[DeploymentSessionRow]) -> DeploymentCaption:
    """
    What the detail view's heading and footer say about a whole deployment.

    Read off no single row: a session with two currencies is two rows, and a start that failed has
    no currency and sorts first, so the first row says neither when the deployment began nor what
    its amounts are in.

    Args:
        sessions: The deployment's sessions

    Returns:
        The caption
    """
    starts = [s.started for s in sessions if s.started is not None]
    return DeploymentCaption(
        session_count=len({s.run_id for s in sessions}),
        first_started=min(starts, key=ensure_utc_aware) if starts else None,
        currencies=sorted({s.currency for s in sessions if s.currency}),
    )


def deployment_comparability_advisory(
    rows: List[RunResultRow],
) -> Optional[DeploymentComparabilityAdvisory]:
    """
    Report whether one deployment's sessions form a comparable series.

    Returns None when they do — one strategy stand, one operational stand and one place its
    orders went — because a warning that fires on the normal case is a warning that gets
    skipped. A rehearsal followed by real money is the sharpest of the three: its P&L column
    adds simulated fills to real ones.

    Args:
        rows: The ledger rows of ONE deployment

    Returns:
        The advisory, or None when nothing changed across the deployment
    """
    if len(rows) < 2:
        return None
    strategy = {r.param_hash for r in rows if r.param_hash}
    operation = {r.profile_hash for r in rows if r.profile_hash}
    orders_to = {r.orders_to for r in rows if r.orders_to is not None}
    if len(strategy) < 2 and len(operation) < 2 and len(orders_to) < 2:
        return None
    return DeploymentComparabilityAdvisory(
        # Distinct RUNS, not rows. A row is one (run × account currency), so counting rows
        # reported a two-currency session as two sessions — and the number is rendered as
        # "over N sessions", i.e. the denominator of the sentence that tells the operator
        # how much of their deployment the advisory is about.
        sessions=len({r.run_id for r in rows}),
        strategy_stands=max(1, len(strategy)),
        operation_stands=max(1, len(operation)),
        orders_to=sorted(orders_to),
    )


def deployment_code_summary(
    rows: List[RunResultRow], now: datetime,
) -> Optional[DeploymentCodeSummary]:
    """
    How many code stands one deployment's sessions started on, and when the code last moved.

    Returns None under two stands, for the reason the advisory does: a warning that fires on the
    normal case is a warning that gets skipped. A row that predates the column has no stand and
    takes no part, so the sessions on either side of it are compared with each other. It walks the
    series the history draws, one per currency, so it never reports a move no line shows; a start
    that failed has no currency and takes no part either.

    Args:
        rows: The ledger rows of ONE deployment
        now: The moment the age is measured to; a parameter, so a reader names its own clock

    Returns:
        The summary, or None under two stands
    """
    # One stand per RUN and currency: a run writes one row per booking period and account currency,
    # and all of them read one header.
    by_series: Dict[str, Dict[str, RunResultRow]] = {}
    for row in sorted(rows, key=lambda r: r.run_timestamp):
        if row.code_stand is not None and row.currency:
            by_series.setdefault(row.currency, {}).setdefault(row.run_id, row)

    distinct: List[CodeStand] = []
    last_change: Optional[RunResultRow] = None
    for by_session in by_series.values():
        previous: Optional[CodeStand] = None
        for row in by_session.values():
            stand = row.code_stand
            if not any(stands_agree(known, stand) for known in distinct):
                distinct.append(stand)
            if (previous is not None and not stands_agree(previous, stand)
                    and (last_change is None or row.run_timestamp > last_change.run_timestamp)):
                last_change = row
            previous = stand
    if len(distinct) < 2 or last_change is None:
        return None

    started = _parse(last_change.run_timestamp)
    last_change_at = ensure_utc_aware(started) if started is not None else None
    return DeploymentCodeSummary(
        code_stands=len(distinct),
        # Distinct RUNS, not rows, as on the advisory: it is the denominator of a sentence.
        sessions=len({r.run_id for r in rows}),
        last_change_at=last_change_at,
        # A start ahead of the reader's clock is a clock apart, not a change in the future.
        age_days=max(0, (now - last_change_at).days) if last_change_at is not None else None,
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
        by_session = aggregate_ledger_rows(members, by=SESSION_KEY)

        sessions: List[DeploymentSessionRow] = []
        previous: Optional[RunResultRow] = None
        previous_stand: Optional[CodeStand] = None
        position = 0
        for row in sorted(by_session, key=lambda r: (r.currency, r.run_timestamp)):
            # Each currency is its own series: the first session of the next one has no
            # predecessor, so no gap and no change mark is measured against the other currency.
            if previous is not None and previous.currency != row.currency:
                previous, previous_stand, position = None, None, 0
            position += 1
            started = _parse(row.run_timestamp)
            # The gap runs from when the PREDECESSOR'S ROW WAS WRITTEN — i.e. from the end of
            # that session — to this one's start. Measuring start-to-start instead counts the
            # predecessor's whole runtime as downtime: a bot that ran 06:00-18:00 and came
            # back at 19:00 would read as a 13-hour gap rather than a one-hour one. Rows
            # written before `recorded_at_utc` existed fall back to start-to-start and say so,
            # and so does a predecessor that never wrote its close (#476): its last row is
            # its last sealed day, not its end.
            ended = _session_end(previous) if previous else None
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
                bot_id=row.bot_id,
                ended=_session_end(row),
                ran_hours=_ran_hours(started, _session_end(row)),
                gap_hours=(
                    (started - ended).total_seconds() / 3600
                    if started and ended else None),
                gap_between_starts=fell_back,
                strategy_changed=bool(previous and previous.param_hash != row.param_hash),
                operation_changed=bool(previous and previous.profile_hash != row.profile_hash),
                orders_to=row.orders_to,
                # Only between two sessions that both recorded it — an older row says nothing,
                # which is not a change.
                orders_to_changed=bool(
                    previous and previous.orders_to is not None and row.orders_to is not None
                    and previous.orders_to != row.orders_to),
                # Only between two sessions that both recorded it: an older row has no stand, no
                # line and is never a change, and it does not hide what moved across it. A start
                # that failed has no currency, and its series adds nothing up a line could split.
                code_changes=(
                    code_changes_between(previous_stand, row.code_stand)
                    if previous_stand is not None and row.code_stand is not None and row.currency
                    else []),
            ))
            previous = row
            if row.code_stand is not None:
                previous_stand = row.code_stand
        histories[deployment] = sessions
    return histories


def _session_end(row: RunResultRow) -> Optional[datetime]:
    """
    When a session ended, if its ledger says it did (#476).

    A session writes its rows at each trading day's seal while it runs, and every write stamps
    `recorded_at_utc` — so the stamp is the session's end only once the row of its close is
    among them. Without it the session is still running or died, and its end is unknown: the run
    list's recording verdict says which. The stamp is the wall clock, as the start is, so a
    replayed session's length is not measured against the market time it replayed.

    Args:
        row: One session's rows, folded — the last period's close reason is the folded one

    Returns:
        The end, or None while the ledger holds no row of the session's close
    """
    if not closes_its_run(row.period_close_reason):
        return None
    return _parse(row.recorded_at_utc)


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
