"""
FiniexTestingIDE - Deployment History (#497)

A live bot that restarts writes one ledger row per session, each under its own run id. Since
#497 those rows name a common deployment, so they can be read back as one history — the life
of one bot across its restarts.

Formatting only — the grouping, the marks and the roll-up are derived in
`builders/deployment_history_builder.py` (§12), because the API serves the same history and two
derivations of one deployment are two chances to disagree about what its drawdown is (#539).

What this does NOT do is judge. A deployment is a DECLARATION that these sessions belong
together; whether they are COMPARABLE is what the two fingerprints answer, and whether a gap
was acceptable is a judgement a person makes. All three are shown, none is collapsed into a
verdict (#527 owns the richer surface; this is the console floor beneath it).
"""

from typing import List, Optional

from python.framework.types.api.report_types import (
    DeploymentComparabilityAdvisory,
    DeploymentSessionRow,
    DeploymentSummary,
)


def render_deployment_list(summaries: List[DeploymentSummary]) -> None:
    """
    Print every deployment as one line — the overview half.

    Formatting only; every figure was derived in the builder (§12).

    Args:
        summaries: The roll-ups, already ordered
    """
    print(f'\n{len(summaries)} deployment(s)')
    print('─' * 136)
    print(f'{"deployment":<30} {"bot":<22} {"bot id":<20} {"sessions":>8} {"since":<17} '
          f'{"net P&L":>11} {"max DD":>11} {"idle max":>9}')
    print('─' * 136)
    # Grouped by BOT so a deliberate restart reads as what it is. `--new-deployment` mints a
    # fresh identity and stores no link back to the one it replaced, so two deployments of one
    # bot would otherwise sit among the others as strangers.
    for summary in sorted(summaries, key=lambda s: (s.bot, s.deployment_id), reverse=True):
        since = summary.first_started.strftime('%Y-%m-%d %H:%M') if summary.first_started else '?'
        gap = f'{summary.longest_gap_hours / 24:.1f} d' if summary.longest_gap_hours else '—'
        mark = ' ⚠' if summary.changed else ''
        # `bot_id` is the only identity that does not move, and the footer's claim about two
        # rows for one bot is only checkable against it — `bot` is the profile NAME, which an
        # operator improves. A profile that declares none shows a dash rather than a blank.
        print(f'{summary.deployment_id:<30} {summary.bot:<22} '
              f'{(summary.bot_id or "—"):<20} {summary.sessions:>8} '
              f'{since:<17} {summary.net_pnl:>11.2f} {-abs(summary.max_drawdown):>11.2f} '
              f'{gap:>9}{mark}')
    print('─' * 136)
    if any(s.changed for s in summaries):
        print('⚠ = the sessions were not all produced by the same configuration; the detail '
              'view draws the break.')
    print('Two rows sharing a BOT ID = one bot restarted with --new-deployment. The older '
          'history stays readable.')
    print('Open one with `run_index_cli.py deployments --id <deployment>`.')


def render_deployment_history(
    deployment: str, sessions: List[DeploymentSessionRow],
    advisory: Optional[DeploymentComparabilityAdvisory] = None,
    unfinished: int = 0) -> None:
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
            is built from the LEDGER, whose row is written last, so those runs are absent
            from it by construction — and a bare session count would then be a number the
            reader has no reason to doubt. Zero leaves the line exactly as it was
    """
    currency = sessions[0].currency if sessions else ''
    first = sessions[0].started.strftime('%Y-%m-%d %H:%M') if sessions and sessions[0].started else '?'
    recorded = (f'{len(sessions)} session(s) recorded + {unfinished} never completed'
                if unfinished else f'{len(sessions)} session(s)')
    bot_id = next((s.bot_id for s in sessions if s.bot_id), '')
    identity = f' · bot {bot_id}' if bot_id else ''
    print(f'\n{deployment} — {recorded} · since {first} UTC{identity}')
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
