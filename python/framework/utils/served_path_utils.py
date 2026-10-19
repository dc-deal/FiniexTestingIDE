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
