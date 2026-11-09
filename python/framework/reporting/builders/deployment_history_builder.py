"""
Deployment history builder (#497 / #539) — ledger rows read back as the life of one bot.

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

Which sessions a deployment holds is the run index's answer, and what they booked the ledger's
(#645). A session books its first row when it seals its first trading day, so a history read
from the ledger alone left a new deployment out of every list until then, while it ran.

This is DERIVE (§12). It lived in the console module until #539 asked for the same history over
HTTP; two derivations of one deployment are two chances to disagree about what its drawdown is,
so the grouping, the marks and the roll-up moved here and the renderers kept only their columns.

What it does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints and the code stands answer,
and whether a gap was acceptable is a judgement a person makes. All three are reported, none is
collapsed into a verdict (#527 owns the richer surface; the console is the floor beneath it).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.reporting.aggregation.aggregation_fold import (
    aggregate_ledger_rows,
    fold_rows,
)
from python.framework.reporting.aggregation.tables.deployment_figure_table import (
    DEPLOYMENT_SUMMARY_TABLE,
)
from python.framework.reporting.builders.booking_periods_report_builder import (
    booking_periods_from_ledger_rows,
)
from python.framework.reporting.builders.code_stand_comparison import (
    code_changes_between,
    stands_agree,
)
from python.framework.reporting.store.run_completion_audit import (
    closes_its_run,
    unfinished_by_group,
)
from python.framework.types.api.report_types import (
    SESSION_KEY,
    DeploymentBookingPeriodsResponse,
    DeploymentCaption,
    DeploymentCodeSummary,
    DeploymentComparabilityAdvisory,
    DeploymentDetailResponse,
    DeploymentListResponse,
    DeploymentSessionRow,
    DeploymentSummary,
    ParentKind,
    RunInfo,
    RunResultRow,
    SessionBookingState,
)
from python.framework.types.run_origin_types import CodeStand
from python.framework.utils.time_utils import ensure_utc_aware

# Where a session without a start sorts among those without booked figures: after every start.
_NO_START = datetime.min.replace(tzinfo=timezone.utc)


def compose_deployment_list(
    rows: List[RunResultRow],
    runs: List[RunInfo],
    now: datetime,
) -> DeploymentListResponse:
    """
    Every deployment, newest first — the deployment list's answer, from what the stores hold.

    Composed here rather than in the route, so the answer can be rebuilt from frozen rows and runs
    with a fixed instant: the golden samples of #557 do exactly that.

    Args:
        rows: Every ledger row
        runs: Every run of the run index
        now: The instant each code summary measures against

    Returns:
        The DeploymentListResponse — empty when no session has named a continuous deployment yet
    """
    histories = add_unbooked_sessions(build_deployment_histories(rows), rows, runs)
    advisories = {
        name: deployment_comparability_advisory(
            [r for r in rows if r.deployment_id == name])
        for name in histories
    }
    code_summaries = {
        name: deployment_code_summary([r for r in rows if r.deployment_id == name], now)
        for name in histories
    }
    summaries = summarize_deployments(histories, advisories, code_summaries)
    return DeploymentListResponse(deployments=summaries, count=len(summaries))


def compose_deployment_detail(
    deployment_id: str,
    rows: List[RunResultRow],
    runs: List[RunInfo],
    now: datetime,
) -> Optional[DeploymentDetailResponse]:
    """
    One deployment's sessions, oldest first — the deployment detail's answer.

    Args:
        deployment_id: The identity the sessions name
        rows: The ledger rows of this deployment
        runs: The run index's runs that name this deployment
        now: The instant the code summary measures against

    Returns:
        The sessions, the comparability advisory when the configuration moved, the code summary
        when the code did, and how many of this deployment's runs never reached their close; None
        when neither a session in the run index nor a ledger row names this identity
    """
    sessions = add_unbooked_sessions(
        build_deployment_histories(rows), rows, runs).get(deployment_id, [])
    if not sessions:
        return None

    # The ledger holds the row of a session's close only at the end: a run killed before it is
    # in the list without an end when it sealed a day, and with no figures when it sealed none.
    # The count says how many such runs there are — without it a session that died reads like
    # one still running. DEPLOYMENT explicitly: a sweep id has the same shape, so the kind is
    # what makes the filter exact (#386).
    missing = unfinished_by_group(
        runs, rows, parent_id=deployment_id, parent_kind=ParentKind.DEPLOYMENT)

    return DeploymentDetailResponse(
        deployment_id=deployment_id,
        sessions=sessions,
        count=len(sessions),
        unfinished=sum(len(group) for group in missing.values()),
        advisory=deployment_comparability_advisory(rows),
        code_summary=deployment_code_summary(rows, now),
    )


def compose_deployment_booking_periods(
    deployment_id: str,
    rows: List[RunResultRow],
    runs: List[RunInfo],
) -> Optional[DeploymentBookingPeriodsResponse]:
    """
    Every booking period one deployment booked, across all of its sessions.

    Args:
        deployment_id: The identity the sessions name
        rows: The ledger rows of this deployment
        runs: The run index's runs that name this deployment

    Returns:
        The periods, oldest first within each currency; None when neither a session in the run
        index nor a ledger row names this identity
    """
    not_booked = unbooked_runs(rows, runs).get(deployment_id, [])
    if not rows and not not_booked:
        return None

    periods = booking_periods_from_ledger_rows(rows)
    # Counted over distinct RUNS, not rows: a run writes one row per period, so counting rows
    # would report a thirty-day session as thirty sessions. The second count is what keeps a
    # short list honest — a session whose row predates the booking journal books nothing, and
    # neither has one that has not sealed its first day yet.
    booked = {row.run_id for row in rows if row.period_opened_at}
    return DeploymentBookingPeriodsResponse(
        deployment_id=deployment_id,
        periods=periods,
        count=len(periods),
        currencies=sorted({period.currency for period in periods}),
        sessions=len(booked),
        sessions_without_periods=len(
            ({row.run_id for row in rows} - booked) | {run.run_id for run in not_booked}),
    )


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
        row count would report a two-currency bot as having run twice as often. The sessions
        without booked figures have no currency: they are counted on every row of their
        deployment, and form a row of their own only where the deployment has booked nothing
    """
    summaries: List[DeploymentSummary] = []
    for deployment, all_sessions in histories.items():
        without = [s for s in all_sessions if not s.booking_state.has_booked_figures()]
        failed = sum(1 for s in without if s.booking_state is SessionBookingState.FAILED)
        by_currency: Dict[Optional[str], List[DeploymentSessionRow]] = {}
        for session in all_sessions:
            if session.booking_state.has_booked_figures():
                by_currency.setdefault(session.currency, []).append(session)
        # A row of their own only for a deployment that booked nothing: beside a booked row it
        # would read the same deployment twice, one of the two empty.
        for sessions in list(by_currency.values()) or [without]:
            if not sessions:
                continue
            summaries.append(_summarize_one(
                deployment, sessions, len(without), failed, advisories, code_summaries))
    # The currency's PRESENCE before its value: None does not compare with a string, and the row
    # that has booked nothing belongs after the deployment's booked rows.
    return sorted(summaries, key=lambda s: (s.deployment_id, s.currency is not None, s.currency or ''),
                  reverse=True)


def _summarize_one(
    deployment: str,
    sessions: List[DeploymentSessionRow],
    sessions_without_figures: int,
    sessions_failed: int,
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
    code_summaries: Dict[str, Optional[DeploymentCodeSummary]],
) -> DeploymentSummary:
    """
    One deployment's sessions in ONE currency, as a single line.

    Args:
        deployment: The deployment identity
        sessions: Its sessions in one currency, or those without booked figures, ordered
        sessions_without_figures: How many of the deployment's sessions have no booked figures
        sessions_failed: How many of those failed before they measured anything
        advisories: Deployment identity → its comparability advisory, or None
        code_summaries: Deployment identity → its code summary, or None

    Returns:
        The summary line
    """
    # How the sessions fold — the deepest decline, the complete sum, the earliest start — is
    # declared in the deployment summary's fold table.
    (figures,) = fold_rows(sessions, DEPLOYMENT_SUMMARY_TABLE)
    advisory = advisories.get(deployment)
    code_summary = code_summaries.get(deployment)
    return DeploymentSummary(
        deployment_id=deployment,
        sessions_without_figures=sessions_without_figures,
        sessions_failed=sessions_failed,
        changed=advisory is not None or code_summary is not None,
        configuration_changed=advisory is not None,
        code_summary=code_summary,
        **figures,
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
        Deployment identity → its sessions: each currency's series by run timestamp, then the
        sessions without booked figures — runs that failed before they measured anything — oldest
        first
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
            session = DeploymentSessionRow(
                index=position,
                run_id=row.run_id,
                started=started,
                net_pnl=row.net_pnl,
                max_drawdown=row.account_max_drawdown,
                max_drawdown_pct=row.account_max_drawdown_pct,
                currency=row.currency,
                booking_state=SessionBookingState.BOOKED,
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
            )
            # A run that failed before it measured anything has no currency to stand under: it
            # joins the sessions without booked figures, which mark no gap and no change (#646).
            sessions.append(_without_figures(session, SessionBookingState.FAILED)
                            if row.measured_nothing() else session)
            previous = row
            if row.code_stand is not None:
                previous_stand = row.code_stand
        histories[deployment] = _order_without_figures(sessions)
    return histories


def add_unbooked_sessions(
    histories: Dict[str, List[DeploymentSessionRow]],
    rows: List[RunResultRow],
    runs: List[RunInfo],
) -> Dict[str, List[DeploymentSessionRow]]:
    """
    Add the sessions the run index registered that no ledger row of their deployment names (#645).

    Every session registers in the run index at its start, naming its deployment, and books its
    first ledger row only when it seals its first trading day. Read from the ledger alone, a new
    deployment therefore stood in no list for up to a day, and its detail answered 404, while its
    session ran and recorded. #539 defines a deployment by the runs that name it; this is that
    definition.

    Such a session has booked nothing yet: it runs before its first seal, or it ended before
    reaching it, and the run list's recording verdict says which. It is added with its start and
    without figures, as a series of its own after the booked ones: no currency, no P&L, no
    drawdown — None, never a zero, because nothing was measured.

    Args:
        histories: Deployment identity → its booked sessions, as `build_deployment_histories`
            returns them
        rows: The ledger rows the histories were built from
        runs: The runs of the run index to look through

    Returns:
        Deployment identity → its sessions: the booked ones unchanged, then every session without
        booked figures — those that failed before they measured anything and those not booked yet
        — as one series, oldest first and numbered from 1; a deployment only the run index names is
        added
    """
    merged = {deployment: list(sessions) for deployment, sessions in histories.items()}
    for deployment, members in unbooked_runs(rows, runs).items():
        sessions = merged.setdefault(deployment, [])
        for position, run in enumerate(members, start=1):
            sessions.append(DeploymentSessionRow(
                index=position,
                run_id=run.run_id,
                started=_parse(run.start_time),
                net_pnl=None,
                max_drawdown=None,
                max_drawdown_pct=None,
                currency=None,
                booking_state=SessionBookingState.NOT_BOOKED_YET,
                bot=run.name,
                orders_to=run.orders_to,
            ))
        merged[deployment] = _order_without_figures(sessions)
    return merged


def unbooked_runs(rows: List[RunResultRow], runs: List[RunInfo]) -> Dict[str, List[RunInfo]]:
    """
    The runs a deployment holds that no ledger row of that deployment names yet (#645).

    Args:
        rows: Ledger rows
        runs: The runs of the run index to look through

    Returns:
        Deployment identity → its runs that have booked nothing yet, oldest first; a deployment
        with none is absent
    """
    # Booked per DEPLOYMENT and run: a session is booked for the deployment its rows name.
    booked = {(row.deployment_id, row.run_id) for row in rows if row.deployment_id}
    unbooked: Dict[str, List[RunInfo]] = {}
    for run in sorted(runs, key=lambda r: r.start_time):
        # DEPLOYMENT explicitly: a sweep id has the same shape, so the kind is what makes the
        # filter exact (#386). A run indexed before the kind existed is not matched: an unknown
        # kind is not a claim that it is this one.
        if run.parent_kind != ParentKind.DEPLOYMENT or not run.parent_id:
            continue
        if (run.parent_id, run.run_id) not in booked:
            unbooked.setdefault(run.parent_id, []).append(run)
    return unbooked


def _without_figures(
    session: DeploymentSessionRow,
    state: SessionBookingState,
) -> DeploymentSessionRow:
    """
    A session as one without booked figures: no currency, and no gap or change measured against
    a predecessor, as a session the run index names before its first seal carries none (#645).

    Args:
        session: The session as its series built it
        state: Why it has no figures

    Returns:
        The session without currency, gap or change marks
    """
    return session.model_copy(update={
        'currency': None,
        'booking_state': state,
        'gap_hours': None,
        'gap_between_starts': False,
        'strategy_changed': False,
        'operation_changed': False,
        'orders_to_changed': False,
        'code_changes': [],
    })


def _order_without_figures(sessions: List[DeploymentSessionRow]) -> List[DeploymentSessionRow]:
    """
    A deployment's sessions, those without booked figures as one series after the others.

    Their starts come from two sources, a ledger row's timestamp and the run index's start, so
    they are compared as UTC instants, and a session without a start sorts last.

    Args:
        sessions: The deployment's sessions

    Returns:
        The booked sessions as they came, then those without booked figures oldest first,
        numbered from 1
    """
    booked = [s for s in sessions if s.booking_state is SessionBookingState.BOOKED]
    without = sorted(
        (s for s in sessions if s.booking_state is not SessionBookingState.BOOKED),
        key=lambda s: (s.started is None,
                       ensure_utc_aware(s.started) if s.started is not None else _NO_START))
    return booked + [s.model_copy(update={'index': position})
                     for position, s in enumerate(without, start=1)]


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
