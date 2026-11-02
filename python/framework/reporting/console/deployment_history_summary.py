"""
FiniexTestingIDE - Deployment History (#497)

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

Formatting only — the grouping, the marks and the roll-up are derived in
`builders/deployment_history_builder.py` (§12), because the API serves the same history and two
derivations of one deployment are two chances to disagree about what its drawdown is (#539).

What this does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints and the code stands answer,
and whether a gap was acceptable is a judgement a person makes. All three are shown, none is
collapsed into a verdict (#527 owns the richer surface; this is the console floor beneath it).
"""

from typing import List, Optional

from python.framework.reporting.builders.deployment_history_builder import deployment_caption
from python.framework.types.api.report_types import (
    CodeChangeDirection,
    DeploymentCodeChange,
    DeploymentCodeSummary,
    DeploymentComparabilityAdvisory,
    DeploymentSessionRow,
    DeploymentSummary,
)
from python.framework.types.run_origin_types import FRAMEWORK_REPOSITORY
from python.framework.utils.math_utils import negated_magnitude


def render_deployment_list(summaries: List[DeploymentSummary]) -> None:
    """
    Print every deployment as one line — the overview half.

    Formatting only; every figure was derived in the builder (§12).

    Args:
        summaries: The roll-ups, already ordered
    """
    print(f'\n{len(summaries)} deployment(s)')
    print('─' * 155)
    # 32 holds a deployment id with its random half (31 characters) and the gap after it
    print(f'{"deployment":<32} {"bot":<22} {"bot id":<20} {"orders to":<16} {"sessions":>8} '
          f'{"since":<17} {"net P&L":>11} {"max DD":>11} {"idle max":>9}')
    print('─' * 155)
    # Grouped by BOT so a deliberate restart reads as what it is. `--new-deployment` mints a
    # fresh identity and stores no link back to the one it replaced, so two deployments of one
    # bot would otherwise sit among the others as strangers.
    for summary in sorted(summaries, key=lambda s: (s.bot, s.deployment_id), reverse=True):
        since = summary.first_started.strftime('%Y-%m-%d %H:%M') if summary.first_started else '?'
        gap = f'{summary.longest_gap_hours / 24:.1f} d' if summary.longest_gap_hours else '—'
        mark = _list_mark(summary)
        # `bot_id` is the only identity that does not move, and the footer's claim about two
        # rows for one bot is only checkable against it — `bot` is the profile NAME, which an
        # operator improves. A profile that declares none shows a dash rather than a blank.
        # Where the orders went — `venue` is real money. Both values: a rehearsal and real money
        # in one history; a dash: not recorded.
        orders_to = ','.join(value.value for value in summary.orders_to) or '—'
        deepest = (f'{negated_magnitude(summary.max_drawdown):>11.2f}'
                   if summary.max_drawdown is not None
                   else f'{"—":>11}')
        print(f'{summary.deployment_id:<32} {summary.bot:<22} '
              f'{(summary.bot_id or "—"):<20} {orders_to:<16} {summary.sessions:>8} '
              f'{since:<17} {summary.net_pnl:>11.2f} {deepest} '
              f'{gap:>9}{mark}')
    print('─' * 155)
    if any(s.configuration_changed for s in summaries):
        print('⚠ = the sessions were not all produced by the same configuration; the detail '
              'view draws the break.')
    if any(s.code_summary is not None for s in summaries):
        print('⚠ code N d ago = the sessions started on different code, last N days ago; the '
              'detail view draws each change.')
    print('Two rows sharing a BOT ID = one bot restarted with --new-deployment. The older '
          'history stays readable.')
    print('Open one with `run_index_cli.py deployments --id <deployment>`.')


def render_deployment_history(
    deployment: str, sessions: List[DeploymentSessionRow],
    advisory: Optional[DeploymentComparabilityAdvisory] = None,
    unfinished: int = 0,
    code_summary: Optional[DeploymentCodeSummary] = None) -> None:
    """
    Print one deployment's sessions as a table — the detail half.

    Formatting only; every value it shows was derived in the builder (§12) — including
    `unfinished`, which is a set difference over two stores and is computed by the caller.

    Args:
        deployment: The deployment identity
        sessions: Its sessions, ordered
        advisory: What the comparability analyzer found, printed ABOVE the table when it
            found anything. Above, because the question it answers — may these rows be read
            as one series — has to reach the reader before the numbers do
        unfinished: How many of this deployment's runs never reached their close. The table
            is built from the LEDGER, which holds a session's sealed days (#476) but the row
            of its close only at the end — so such a run stands in the table without an end
            when it sealed a day, and is absent from it when it died before its first seal.
            The two counts overlap and are never added. Zero leaves the line exactly as it was
        code_summary: How often the code moved, printed as one dated line above the table and above
            the advisory's block. One line, because it never leaves the deployment's history: a
            block here would stand on every maintained deployment for good
    """
    caption = deployment_caption(sessions)
    first = caption.first_started.strftime('%Y-%m-%d %H:%M') if caption.first_started else '?'
    recorded = (f'{caption.session_count} session(s) in the ledger · {unfinished} never completed'
                if unfinished else f'{caption.session_count} session(s)')
    bot_id = next((s.bot_id for s in sessions if s.bot_id), '')
    identity = f' · bot {bot_id}' if bot_id else ''
    print(f'\n{deployment} — {recorded} · since {first} UTC{identity}')
    if code_summary is not None:
        print(_code_warning(code_summary))
    if advisory is not None:
        print('─' * 104)
        print('⚠️  THIS DEPLOYMENT SPANS MORE THAN ONE CONFIGURATION')
        print(f'    {advisory.strategy_stands} strategy stand(s) and '
              f'{advisory.operation_stands} operational stand(s) over '
              f'{advisory.sessions} sessions.')
        if len(advisory.orders_to) > 1:
            print('    Its orders went to a simulator in some sessions and to the venue in others '
                  '— its P&L')
            print('    column adds simulated fills to real money.')
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
    # directory name under runs/autotrader/<profile>/, so a reader can descend from here into the
    # logs of one session — the last step of deployments → sessions → this run.
    for session in reversed(sessions):
        started = session.started.strftime('%Y-%m-%d %H:%M') if session.started else '?'
        ran = f'{session.ran_hours:.1f} h' if session.ran_hours is not None else '—'
        notes = []
        # With several currencies the footer cannot say which one a row's amounts are in
        if len(caption.currencies) > 1 and session.currency:
            notes.append(session.currency)
        # Reported, never judged: no threshold here decides what is "too long" — that is a
        # judgement about the market and the bot, not about the data. Named "idle" because
        # the table now reads downwards into the past: it is the stretch before this session.
        if session.gap_hours is not None and session.gap_hours >= 1:
            measure = ' between starts' if session.gap_between_starts else ''
            notes.append(f'idle {session.gap_hours:.1f} h{measure}'
                         if session.gap_hours < 48
                         else f'idle {session.gap_hours / 24:.1f} d{measure}')
        decline = (f'{negated_magnitude(session.max_drawdown):>13.2f}'
                   if session.max_drawdown is not None
                   else f'{"—":>13}')
        share = (f'{session.max_drawdown_pct:>6.2f}%' if session.max_drawdown_pct is not None
                 else f'{"—":>7}')
        print(f'{session.run_id:<26} {started:<17} {ran:>7} {session.net_pnl:>11.2f} '
              f'{decline} {share}   '
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
        if session.orders_to_changed:
            moved.append('ORDERS TO')
        if moved:
            print(f'{"·" * 26} {" and ".join(moved)} CHANGED HERE — rows above and below '
                  f'answer different questions {"·" * 6}')
        if session.code_changes:
            _print_code_changes(session.code_changes)

    print('─' * 104)
    amounts = ', '.join(caption.currencies) or '—'
    print(f'Amounts in {amounts}. Newest session first. `ran` is measured from the start to '
          'the moment the')
    print('row was written, so it carries the reporting tail. The drawdown column is '
          'CUMULATIVE over the')
    print('deployment — each row holds the running figure against the inherited peak, so '
          'max() is its')
    print('reduction and a sum double-counts. `idle` is the stretch BEFORE that session; '
          'where a row')
    print('predates the end stamp it is measured start-to-start and labelled, which '
          'overstates it.')
    print('Descend into one session: runs/autotrader/<profile>/<run id>/ — its summary, session log and '
          'events.csv each answer a different question '
          '(docs/autotrader/autotrader_observability.md).')


def _list_mark(summary: DeploymentSummary) -> str:
    """
    What a list line says about change, behind its last column.

    Args:
        summary: The deployment's roll-up

    Returns:
        ` ⚠` for a moved configuration, ` ⚠ code 22 d ago` for moved code, ` ⚠ · code 22 d ago` for
        both, '' for neither
    """
    code = summary.code_summary
    if code is None:
        return ' ⚠' if summary.configuration_changed else ''
    age = f'code {code.age_days} d ago' if code.age_days is not None else 'code'
    return f' ⚠ · {age}' if summary.configuration_changed else f' ⚠ {age}'


def _code_warning(code: DeploymentCodeSummary) -> str:
    """
    The one dated line a deployment's code change prints above the table.

    Args:
        code: How often the code moved, and when it last did

    Returns:
        The line
    """
    when = (f'{code.age_days} d ago ({code.last_change_at:%Y-%m-%d})'
            if code.last_change_at is not None and code.age_days is not None
            else 'on an unknown date')
    return (f'⚠️  Code changed {when} — {code.code_stands} code stands over {code.sessions} '
            'sessions; net P&L adds them up')


def _print_code_changes(changes: List[DeploymentCodeChange]) -> None:
    """
    The line between two sessions whose code differs, with one row per repository under it.

    Args:
        changes: What moved, one entry per repository
    """
    heading = '··· code changed here '
    print(heading + '·' * (104 - len(heading)))
    label_width = max([len(FRAMEWORK_REPOSITORY)] + [len(c.repository_label) for c in changes])
    for change in changes:
        old = _commit_shown(
            change.old_commit,
            '(new)' if change.change_direction is CodeChangeDirection.ADDED else 'unknown')
        new = _commit_shown(
            change.new_commit,
            '(gone)' if change.change_direction is CodeChangeDirection.REMOVED else 'unknown')
        commits = f'{old} → {new}'
        subject = f'"{change.subject}"' if change.subject else ''
        print(f'    {change.repository_label:<{label_width}}  {commits:<19}  '
              f'{_how_far(change):<10}  {subject}'.rstrip())


def _commit_shown(commit: Optional[str], absent: str) -> str:
    """
    A commit as the console shows it. Cut here and nowhere else: the served figure keeps its length.

    Args:
        commit: The commit, None where there is none to show
        absent: What stands in its place then

    Returns:
        The first 8 characters of the commit, or the stand-in
    """
    return commit[:8] if commit else absent


def _how_far(change: DeploymentCodeChange) -> str:
    """
    How far the code moved, as a change row says it.

    Args:
        change: One repository's change

    Returns:
        The words; empty for a repository only one of the two sessions used, which has no distance
    """
    if change.change_direction is CodeChangeDirection.FORWARD:
        return f'+{change.commits_between} commit{"" if change.commits_between == 1 else "s"}'
    if change.change_direction is CodeChangeDirection.BACK:
        return f'back {change.commits_between}'
    if change.change_direction is CodeChangeDirection.UNKNOWN:
        return '+?'
    if change.change_direction is CodeChangeDirection.UNCOMMITTED:
        return 'uncommitted changes'
    return ''
