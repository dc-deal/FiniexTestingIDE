"""
A run's header as the API serves it (#582): the record itself, with every path relative.

The header's code identity names where code was loaded from as the machine saw it —
`/app/user_algos/my_bot/decision.py`. Served as written, that would put this installation's
directory layout, and the name of an operator's private workspace, on the wire. What a reader needs
is WHICH repository and WHERE inside it, so every path is served relative to a repository the run
recorded, and a path that lies in none is reduced to its file name. The record on disk is never
changed.

Every field that can hold a path is covered: a repository's `root` and `patch_ref`, and a
component's `name` (a decision is recorded under its type, which may be a path), `type`,
`source_path` and `repository`. The porcelain lines in `changes` and `patch_excluded` are relative
to their repository by construction.
"""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Optional, Tuple

from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    RepositoryState,
)

# A path written on Windows: a drive letter or a UNC share. Recognised by its shape, because the
# header may have been written by another machine than the one serving it.
_WINDOWS_ABSOLUTE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)')


def served_run_header(header: RunHeader) -> RunHeader:
    """
    The header with every path in its code identity relative to a repository it recorded.

    Args:
        header: The run's header as recorded

    Returns:
        A copy whose repository roots, patch references and component paths are relative
    """
    identity = header.code_identity
    if identity is None:
        return header
    framework_root = _root_of(identity.framework)
    roots = [root for root in [framework_root] + [_root_of(r) for r in identity.repositories]
             if root is not None]
    served = CodeIdentity(
        framework=_served_repository(identity.framework, framework_root),
        repositories=[_served_repository(repository, framework_root)
                      for repository in identity.repositories],
        components=[_served_component(component, roots, framework_root)
                    for component in identity.components],
    )
    return header.model_copy(update={'code_identity': served})


def _normal(path: str) -> Tuple[PurePosixPath, bool]:
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


def _root_of(repository: Optional[RepositoryState]) -> Optional[PurePosixPath]:
    """
    A repository's root as a path, when it states one.

    Args:
        repository: The repository's recorded state

    Returns:
        Its root, or None
    """
    if repository is None or not repository.root:
        return None
    return _normal(repository.root)[0]


def _served_repository(repository: Optional[RepositoryState],
                       framework_root: Optional[PurePosixPath]) -> Optional[RepositoryState]:
    """
    A repository's state with its root and its patch reference relative to this repository —
    `.` for this one, its path inside this one (`user_algos`, `user_algos/vendor/lib`) for one
    within it, its directory's name alone for one outside.

    Args:
        repository: The repository's recorded state
        framework_root: This repository's root as the run recorded it

    Returns:
        The served copy
    """
    if repository is None:
        return None
    own = _root_of(repository)
    base = [framework_root] if framework_root else []
    return repository.model_copy(update={
        'root': _relative(repository.root, base),
        'patch_ref': _relative(repository.patch_ref, ([own] if own else []) + base),
    })


def _served_component(component: ComponentIdentity, roots: List[PurePosixPath],
                      framework_root: Optional[PurePosixPath]) -> ComponentIdentity:
    """
    A component's identity with every path in it relative: its `source_path` to its own
    repository, its `name` and `type` — a decision's name IS its type — to this repository
    first, and its `repository` named the way a repository's root is.

    Args:
        component: The component as recorded
        roots: Every repository root the run recorded, this repository's first
        framework_root: This repository's root as the run recorded it

    Returns:
        The served copy
    """
    own = [_normal(component.repository)[0]] if component.repository else []
    return component.model_copy(update={
        'name': _relative(component.name, roots),
        'type': _relative(component.type, roots),
        'source_path': _relative(component.source_path, own + roots),
        'repository': _relative(component.repository,
                                [framework_root] if framework_root else []),
    })


def _relative(path: Optional[str], roots: List[PurePosixPath]) -> Optional[str]:
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
    candidate, absolute = _normal(path)
    if not absolute:
        return path
    for root in roots:
        if candidate == root:
            return '.'
        if candidate.is_relative_to(root):
            return candidate.relative_to(root).as_posix()
    return candidate.name
