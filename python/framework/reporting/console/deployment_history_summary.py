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

from python.framework.reporting.store.ledger_aggregation import aggregate_ledger_rows
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
        bot: Which bot ran it — the AutoTrader profile's name, which the ledger stores in
            `scenario_set_name` for a live row. Carried so the OVERVIEW can show that two
            deployments belong to the same bot: `--new-deployment` mints a fresh identity and
            records no link back, so without the name a deliberate restart reads as two
            unrelated bots
        currency: The row's account currency
        ended: When its ledger row was written, i.e. when it stopped — None on a row written
            before that stamp existed
        ran_hours: How long it ran, None when its end is unknown. Derived from the two
            stamps rather than stored: the row is written as the session closes, so this is
            the session's length plus the seconds its reports took
        gap_hours: Hours the bot was NOT running before this session, None for the first
        gap_between_starts: True when the gap could only be measured from one START to the
            next, because the predecessor's row predates `recorded_at_utc`. That figure
            contains the predecessor's whole runtime and overstates the downtime, so it is
            marked rather than quietly shown as the same measure
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
    bot: str = ''
    ended: Optional[datetime] = None
    ran_hours: Optional[float] = None
    gap_hours: Optional[float] = None
    gap_between_starts: bool = False
    strategy_changed: bool = False
    operation_changed: bool = False


@dataclass
class DeploymentComparabilityAdvisory:
    """
    A deployment whose sessions were not all produced by the same configuration.

    The per-session marks say WHERE something changed; this says WHETHER the history can be
    read as one series at all, and it says it BEFORE the table rather than inside it. The
    difference matters on a thirty-day run: eleven sessions with three parameter changes show
    three marks somewhere in the middle, and the reader has already added up the P&L column by
    the time they reach them.

    The sibling of `mixed_logic_version_advisory` (#390), which answers the same question for a
    sweep's ranking. Like it, this is an ANALYZER and renders no verdict — whether the halves
    may be compared is a judgement a person makes with what it reports.

    Args:
        sessions: How many sessions the deployment holds
        strategy_stands: Distinct `param_hash` values across them — what the bot DECIDED
        operation_stands: Distinct `profile_hash` values — what a session DID without changing
            what it decided (a safety threshold, a guard, a timeout, the capital declaration)
        longest_gap_hours: The longest stretch the bot was not running, None when no gap could
            be measured
    """
    sessions: int
    strategy_stands: int
    operation_stands: int
    longest_gap_hours: Optional[float] = None


@dataclass
class DeploymentSummary:
    """
    One deployment's at-a-glance line, for the list view.

    The sibling of `SweepSummary` (#390) and deliberately the same shape of answer: a reader
    scanning a dozen deployments wants to know which one to open, not what happened inside it.

    Args:
        deployment_id: The identity its sessions name
        sessions: How many sessions it holds
        first_started: When the deployment began
        last_started: When its most recent session began
        net_pnl: Realised P&L summed over its sessions — this one DOES add up
        max_drawdown: The deepest decline, which is the LARGEST of the rows and never a sum:
            a live row carries the running figure against the inherited peak
        max_drawdown_pct: That decline as a share of the peak standing when it happened
        bot: The AutoTrader profile that ran it
        currency: The account currency the figures are in
        longest_gap_hours: The longest stretch the bot was not running
        changed: True when the sessions were not all produced by the same configuration
    """
    deployment_id: str
    sessions: int
    first_started: Optional[datetime]
    last_started: Optional[datetime]
    net_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    currency: str
    bot: str = ''
    longest_gap_hours: Optional[float] = None
    changed: bool = False


def summarize_deployments(
    histories: Dict[str, List[DeploymentSessionRow]],
    advisories: Dict[str, Optional[DeploymentComparabilityAdvisory]],
) -> List[DeploymentSummary]:
    """
    Reduce each deployment to one line, newest first.

    DERIVE, not PRESENT (§12): the console, a future API route and the viewer (#527) all want
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


def render_deployment_list(summaries: List[DeploymentSummary]) -> None:
    """
    Print every deployment as one line — the overview half.

    Formatting only; every figure was derived above (§12).

    Args:
        summaries: The roll-ups, already ordered
    """
    print(f'\n{len(summaries)} deployment(s)')
    print('─' * 118)
    print(f'{"deployment":<30} {"bot":<22} {"sessions":>8} {"since":<17} {"net P&L":>11} '
          f'{"max DD":>11} {"idle max":>9}')
    print('─' * 118)
    # Grouped by BOT so a deliberate restart reads as what it is. `--new-deployment` mints a
    # fresh identity and stores no link back to the one it replaced, so two deployments of one
    # bot would otherwise sit among the others as strangers.
    for summary in sorted(summaries, key=lambda s: (s.bot, s.deployment_id), reverse=True):
        since = summary.first_started.strftime('%Y-%m-%d %H:%M') if summary.first_started else '?'
        gap = f'{summary.longest_gap_hours / 24:.1f} d' if summary.longest_gap_hours else '—'
        mark = ' ⚠' if summary.changed else ''
        print(f'{summary.deployment_id:<30} {summary.bot:<22} {summary.sessions:>8} '
              f'{since:<17} {summary.net_pnl:>11.2f} {-abs(summary.max_drawdown):>11.2f} '
              f'{gap:>9}{mark}')
    print('─' * 118)
    if any(s.changed for s in summaries):
        print('⚠ = the sessions were not all produced by the same configuration; the detail '
              'view draws the break.')
    print('Two rows for one bot = it was restarted with --new-deployment. The older history '
          'stays readable.')
    print('Open one with `run_index_cli.py deployments --id <deployment>`.')


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


def render_deployment_history(
    deployment: str, sessions: List[DeploymentSessionRow],
    advisory: Optional[DeploymentComparabilityAdvisory] = None,
    unfinished: int = 0) -> None:
    """
    Print one deployment's sessions as a table — the detail half.

    Formatting only; every value it shows was derived above (§12) — including `unfinished`,
    which is a set difference over two stores and is computed by the caller.

    Args:
        deployment: The deployment identity
        sessions: Its sessions, ordered
        advisory: What the comparability analyzer found, printed ABOVE the table when it
            found anything. Above, because the question it answers — may these rows be read
            as one series — has to reach the reader before the numbers do
        unfinished: How many of this deployment's runs never reached their close. The table
            is built from the LEDGER, whose row is written last, so those runs are absent
            from it by construction — and a bare session count would then be a number the
            reader has no reason to doubt. Zero leaves the line exactly as it was
    """
    currency = sessions[0].currency if sessions else ''
    first = sessions[0].started.strftime('%Y-%m-%d %H:%M') if sessions and sessions[0].started else '?'
    recorded = (f'{len(sessions)} session(s) recorded + {unfinished} never completed'
                if unfinished else f'{len(sessions)} session(s)')
    print(f'\n{deployment} — {recorded} · since {first} UTC')
    if advisory is not None:
        print('─' * 104)
        print('⚠️  THIS DEPLOYMENT SPANS MORE THAN ONE CONFIGURATION')
        print(f'    {advisory.strategy_stands} strategy stand(s) and '
              f'{advisory.operation_stands} operational stand(s) over '
              f'{advisory.sessions} sessions.')
        print('    The sessions are one deployment because they were DECLARED one — that says '
              'they belong')
        print('    to one bot, not that their figures are comparable. A drawdown that deepens '
              'after a change')
        print('    is attributable; a P&L column summed across the change is a number about '
              'two bots.')
    print('─' * 104)
    print(f'{"run id":<26} {"started":<17} {"ran":>7} {"net P&L":>11} {"max DD (cum)":>13} '
          f'{"share":>7}   notes')

    # NEWEST FIRST, and the run id instead of a position. The order is the only one anybody
    # wants (what is this bot doing NOW, then how did it get here), so a number counting the
    # other way is a second thing to read. The id is what the next question needs: it is the
    # directory name under runs/live/<profile>/, so a reader can descend from here into the
    # logs of one session — the last step of deployments → sessions → this run.
    for session in reversed(sessions):
        started = session.started.strftime('%Y-%m-%d %H:%M') if session.started else '?'
        ran = f'{session.ran_hours:.1f} h' if session.ran_hours is not None else '—'
        notes = []
        # Reported, never judged: no threshold here decides what is "too long" — that is a
        # judgement about the market and the bot, not about the data. Named "idle" because
        # the table now reads downwards into the past: it is the stretch before this session.
        if session.gap_hours is not None and session.gap_hours >= 1:
            measure = ' between starts' if session.gap_between_starts else ''
            notes.append(f'idle {session.gap_hours:.1f} h{measure}'
                         if session.gap_hours < 48
                         else f'idle {session.gap_hours / 24:.1f} d{measure}')
        print(f'{session.run_id:<26} {started:<17} {ran:>7} {session.net_pnl:>11.2f} '
              f'{-abs(session.max_drawdown):>13.2f} {session.max_drawdown_pct:>6.2f}%   '
              f'{" · ".join(notes)}')

        # A SEPARATOR rather than a mark in the notes column. The change belongs BETWEEN two
        # rows, not on one of them, and that is also what it does to the numbers: everything
        # above the line was produced by a different configuration from everything below it.
        # A ⚠ at the end of a line is read as a property of that session; a line across the
        # table is read as what it is — the eye stops, which is the whole point on a history
        # long enough that the P&L column gets summed before anyone reaches row 3.
        moved = []
        if session.strategy_changed:
            moved.append('STRATEGY')
        if session.operation_changed:
            moved.append('OPERATION')
        if moved:
            print(f'{"·" * 26} {" and ".join(moved)} CHANGED HERE — rows above and below '
                  f'answer different questions {"·" * 6}')

    print('─' * 104)
    print(f'Amounts in {currency}. Newest session first. `ran` is measured from the start to '
          'the moment the')
    print('row was written, so it carries the reporting tail. The drawdown column is '
          'CUMULATIVE over the')
    print('deployment — each row holds the running figure against the inherited peak, so '
          'max() is its')
    print('reduction and a sum double-counts. `idle` is the stretch BEFORE that session; '
          'where a row')
    print('predates the end stamp it is measured start-to-start and labelled, which '
          'overstates it.')
    print('Descend into one session: runs/live/<profile>/<run id>/ — see §36 for which log '
          'answers what.')


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
