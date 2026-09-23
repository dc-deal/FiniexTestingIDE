"""
FiniexTestingIDE - Run Config Store views (#538)

What the run-config store holds, and what one configuration has been.

The history view is the one that earns its place. A config file changes for four different
reasons and they mean different things — a parameter that changes what the algo decides, a
scenario added that changes which data runs, a rename that changes neither, a comment that
changes nothing at all. The three hashes on every row are what separates them, so the view says
WHICH KIND of change each version was instead of only that there was one.

Formatting only: every figure comes off the index (§12).
"""

from typing import List, Optional

from python.framework.types.run_config_types import RunConfigEntry, RunConfigHistory


def render_run_config_list(entries: List[RunConfigEntry], indent: str = '  ') -> None:
    """
    Every registered configuration version, newest first.

    Args:
        entries: The index rows
        indent: Left padding
    """
    print(f'\n{indent}📇 RUN CONFIGS — {len(entries)} registered version(s)')
    print(f'{indent}' + '─' * 104)
    if not entries:
        print(f'{indent}  nothing registered yet — run or list a scenario set and it appears here')
        return
    print(f'{indent}{"config_id":<14} {"kind":<19} {"source":<38} {"first seen":<17} {"runs":>5}')
    print(f'{indent}' + '─' * 104)
    for e in entries:
        print(f'{indent}{e.config_id[:12]:<14} {str(e.kind):<19} {e.source_name[:38]:<38} '
              f'{_stamp(e.first_seen):<17} {e.run_count:>5}')
    print(f'{indent}' + '─' * 104)


def render_run_config_history(history: RunConfigHistory, indent: str = '  ') -> None:
    """
    Every version one source file has had, oldest first, with what each change touched.

    Args:
        history: The versions of one file
        indent: Left padding
    """
    print(f'\n{indent}📜 {history.source_name} — {len(history.versions)} version(s)')
    print(f'{indent}' + '─' * 104)
    if not history.versions:
        print(f'{indent}  no version registered under that name')
        return
    print(f'{indent}{"#":>3}  {"config_id":<14} {"first seen":<17} {"runs":>5}  what changed')
    print(f'{indent}' + '─' * 104)
    previous: Optional[RunConfigEntry] = None
    for number, entry in enumerate(history.versions, start=1):
        print(f'{indent}{number:>3}  {entry.config_id[:12]:<14} {_stamp(entry.first_seen):<17} '
              f'{entry.run_count:>5}  {_what_changed(previous, entry)}')
        previous = entry
    print(f'{indent}' + '─' * 104)
    print(f'{indent}A version whose decisions and scope both held is a change of NAMING only —')
    print(f'{indent}the bytes differ, what the run would do does not.')


def _what_changed(previous: Optional[RunConfigEntry], entry: RunConfigEntry) -> str:
    """
    Which of the three identities moved between two consecutive versions.

    Args:
        previous: The version before this one, or None for the first
        entry: The version being described

    Returns:
        A phrase naming what the change touched
    """
    if previous is None:
        return 'first registered'
    moved = []
    if previous.param_hash != entry.param_hash:
        moved.append('what the algo DECIDES')
    if previous.scope_hash != entry.scope_hash:
        moved.append('WHICH DATA runs')
    if not moved:
        return 'naming only — no decision, no scope'
    return ' · '.join(moved)


def _stamp(value) -> str:
    """
    A timestamp as the tables show it.

    Args:
        value: A datetime, or anything a str() describes

    Returns:
        `YYYY-MM-DD HH:MM`, or the raw value when it cannot be read
    """
    try:
        return value.strftime('%Y-%m-%d %H:%M')
    except AttributeError:
        return str(value)[:16]
