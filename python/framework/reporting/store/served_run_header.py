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

from pathlib import PurePosixPath
from typing import List, Optional

from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    RepositoryState,
)
from python.framework.utils.served_path_utils import normal_path, relative_served_path


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
    return normal_path(repository.root)[0]


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
        'root': relative_served_path(repository.root, base),
        'patch_ref': relative_served_path(repository.patch_ref, ([own] if own else []) + base),
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
    own = [normal_path(component.repository)[0]] if component.repository else []
    return component.model_copy(update={
        'name': relative_served_path(component.name, roots),
        'type': relative_served_path(component.type, roots),
        'source_path': relative_served_path(component.source_path, own + roots),
        'repository': relative_served_path(component.repository,
                                           [framework_root] if framework_root else []),
    })
