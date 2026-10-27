"""
FiniexTestingIDE - Run footprint view (#621)

One run directory read against its declaration and its footprint: whether the run closed, and the
members grouped by what was found. Formatting only: the assessment comes from `run_footprint`.
"""

from typing import List, Optional

from python.framework.types.run_footprint_types import (
    MemberFootprint,
    MemberFootprintState,
    RunCloseState,
    RunFootprintAssessment,
)

# How each run state reads after the run's id.
CLOSE_LABELS = {
    RunCloseState.CLOSED: 'closed',
    RunCloseState.RUNNING: 'still running — nothing is judged until it closes',
    RunCloseState.NOT_CLOSED: ('NO FOOTPRINT — not running: it stopped before its close, or it ran '
                               'before runs wrote one'),
    RunCloseState.NOT_COMMISSIONED: 'never meant to report, so it has no footprint',
}

# The order the states are listed in: what needs a look first.
STATE_ORDER = (MemberFootprintState.MISSING, MemberFootprintState.FAILED,
                MemberFootprintState.LATE, MemberFootprintState.UNKNOWN,
                MemberFootprintState.SKIPPED, MemberFootprintState.PRESENT)


def render_run_footprint(assessment: Optional[RunFootprintAssessment], run_id: str) -> None:
    """
    Print what a run directory holds against its declaration and its footprint.

    Args:
        assessment: The assessment, or None when the run is not known
        run_id: The run asked about
    """
    if assessment is None:
        print(f'\n  ❌ run {run_id}: the run index knows no directory of it, or that directory '
              f'holds no header.\n')
        return
    closed = (f' {assessment.closed_at.strftime("%Y-%m-%d %H:%M:%S")} UTC'
              if assessment.closed_at else '')
    print(f'\n🧾  run {assessment.run_id} ({assessment.run_type}) — '
          f'{CLOSE_LABELS[assessment.close_state]}{closed}')
    for state in STATE_ORDER:
        members = [member for member in assessment.members if member.state is state]
        if not members:
            continue
        label = state.value.upper() if state.needs_a_look() else state.value
        if state is MemberFootprintState.PRESENT:
            print(f'  {label:<9} {_present_line(members)}')
            continue
        for member in members:
            where = ', '.join(member.paths) if member.paths else member.member_id.value
            print(f'  {label:<9} {where} — {member.reason}')
    if assessment.undeclared:
        print(f'  {"UNDECLARED":<9} {", ".join(assessment.undeclared)} — no member of this '
              f'pipeline declares it')
    print()


def _present_line(members: List[MemberFootprint]) -> str:
    """
    The present members as one line: the first few files and how many there are.

    Args:
        members: The present members

    Returns:
        The line
    """
    paths = [path for member in members for path in member.paths]
    shown = ' · '.join(paths[:3])
    return f'{shown} · … ({len(paths)} files)' if len(paths) > 3 else shown
