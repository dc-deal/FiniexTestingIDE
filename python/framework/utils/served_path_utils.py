"""
FiniexTestingIDE - Served Path Utils
A path recorded on this machine, in the form it may leave it.

A record names where things were as the machine saw them — `/app/user_algos/my_bot/decision.py`.
Served as written, that puts this installation's directory layout, and the name of an operator's
private workspace, on the wire. So a served path is named relative to a root the record itself
states, and a path that lies under none is reduced to its file name. The run header's route and
the activity list share this one rule.
"""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Optional, Tuple

# A path written on Windows: a drive letter or a UNC share. Recognised by its shape, because the
# record may have been written by another machine than the one serving it.
_WINDOWS_ABSOLUTE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)')

# A name written together with its value in one argument: `--config=/app/configs/x.json`, or
# `CONFIG=/app/configs/x.json`. The name holds no slash, so a path with `=` in it stays whole.
_OPTION_WITH_VALUE = re.compile(r'^((?:--?)?[A-Za-z][\w-]*)=(.+)$')


def normal_path(path: str) -> Tuple[PurePosixPath, bool]:
    """
    A recorded path in one comparable form, whichever system wrote it.

    Args:
        path: The path as recorded

    Returns:
        The path with forward slashes, and whether it is absolute
    """
    if _WINDOWS_ABSOLUTE.match(path):
        return PurePosixPath(PureWindowsPath(path).as_posix()), True
    posix = PurePosixPath(Path(path).as_posix())
    return posix, posix.is_absolute()


def relative_served_path(path: Optional[str], roots: List[PurePosixPath]) -> Optional[str]:
    """
    A recorded path as it may leave the machine.

    Args:
        path: The path as recorded; a relative one, a bare name or a type string such as
            `CORE/rsi` is kept as it is
        roots: The roots to name it relative to, in order — the first that holds it wins

    Returns:
        The path relative to the first root that holds it, `.` for a root itself, or the bare
        name when no root holds it
    """
    if not path:
        return path
    candidate, absolute = normal_path(path)
    if not absolute:
        return path
    for root in roots:
        if candidate == root:
            return '.'
        if candidate.is_relative_to(root):
            return candidate.relative_to(root).as_posix()
    return candidate.name


def served_command_argument(argument: str, roots: List[PurePosixPath]) -> str:
    """
    One command-line argument as it may leave the machine.

    A path is named the way `relative_served_path` names it, and so is the value written in one
    argument with its name: `--config=/app/configs/x.json` leaves as `--config=configs/x.json`.
    Any other argument is kept as written.

    Args:
        argument: The argument as the process received it
        roots: The roots to name a path relative to, in order

    Returns:
        The argument as served
    """
    option = _OPTION_WITH_VALUE.match(argument)
    if option is not None:
        return f'{option.group(1)}={served_command_argument(option.group(2), roots)}'
    # Only a bare root has no name to be reduced to, and it names nothing of the machine
    return relative_served_path(argument, roots) or argument
