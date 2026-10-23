"""
Run entry census helpers (#621) — whether a run directory holds what the run store declares.

Shared by the census suite and by the suites that produce a real run of their own, such as the
acceptance studies, so every run a test makes can be held to the same declaration.
"""

import json
from pathlib import Path
from typing import Dict, List, Set

from python.framework.constants.run_layout_constants import RUN_HEADER_ARTIFACT
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.store.entry_members.sweep_entry_members import SWEEP_ENTRY_MEMBERS
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberShape,
    RunEntryMemberId,
    SweepEntryMemberId,
)


def run_type_of(run_dir: Path) -> str:
    """
    The pipeline that wrote a run, as its header names it.

    Args:
        run_dir: The run's directory

    Returns:
        The header's run type
    """
    header = json.loads((run_dir / RUN_HEADER_ARTIFACT).read_text(encoding='utf-8'))
    return header['run_type']


def run_files(run_dir: Path) -> List[str]:
    """
    Every file below a run directory, by its path inside the run.

    Args:
        run_dir: The run's directory

    Returns:
        Sorted relative paths with forward slashes
    """
    return sorted(path.relative_to(run_dir).as_posix()
                  for path in run_dir.rglob('*') if path.is_file())


def _matching_member(relative_path: str, members: List[EntryMember]) -> List[EntryMember]:
    """
    The members a file is claimed by.

    Args:
        relative_path: The file's path inside the entry
        members: The members to ask

    Returns:
        Every member whose pattern fits the path
    """
    return [member for member in members if member.matches(relative_path)]


def _pipeline_members(run_dir: Path) -> List[EntryMember]:
    """
    The members the run's own pipeline may write.

    Args:
        run_dir: The run's directory

    Returns:
        The members declared for its run type
    """
    run_type = run_type_of(run_dir)
    return [member for member in RUN_ENTRY_MEMBERS if member.is_written_by(run_type)]


def undeclared_files(run_dir: Path) -> List[str]:
    """
    The files a run directory holds that no member of its pipeline declares.

    Args:
        run_dir: The run's directory

    Returns:
        Their relative paths, sorted; empty when everything is declared
    """
    members = _pipeline_members(run_dir)
    return [path for path in run_files(run_dir) if not _matching_member(path, members)]


def members_present(run_dir: Path) -> Dict[RunEntryMemberId, List[str]]:
    """
    Which members a run directory holds, with the files that are each of them.

    Args:
        run_dir: The run's directory

    Returns:
        Member id → its files' relative paths
    """
    members = _pipeline_members(run_dir)
    present: Dict[RunEntryMemberId, List[str]] = {}
    for path in run_files(run_dir):
        for member in _matching_member(path, members):
            present.setdefault(member.member_id, []).append(path)
    return present


def stream_version_mismatches(run_dir: Path) -> List[str]:
    """
    The streams whose header line names another schema version than the declaration.

    Args:
        run_dir: The run's directory

    Returns:
        One line per mismatching file; empty when every stream agrees
    """
    by_id = {member.member_id: member for member in RUN_ENTRY_MEMBERS}
    mismatches = []
    for member_id, paths in members_present(run_dir).items():
        member = by_id[member_id]
        if member.shape is not EntryMemberShape.STREAM:
            continue
        for path in paths:
            with (run_dir / path).open(encoding='utf-8') as handle:
                header = json.loads(handle.readline())
            if header.get('schema_version') != member.schema_version:
                mismatches.append(f'{path}: schema_version {header.get("schema_version")!r}, '
                                  f'declared {member.schema_version!r}')
    return mismatches


def undeclared_sweep_files(sweep_dir: Path) -> List[str]:
    """
    The files directly in a sweep directory that no sweep member declares. Its subdirectories are
    its combinations, which are runs and are counted as runs.

    Args:
        sweep_dir: The sweep's directory

    Returns:
        Their names, sorted; empty when everything is declared
    """
    members = list(SWEEP_ENTRY_MEMBERS)
    return sorted(path.name for path in sweep_dir.iterdir()
                  if path.is_file() and not _matching_member(path.name, members))


def sweep_members_present(sweep_dir: Path) -> Set[SweepEntryMemberId]:
    """
    Which members a sweep directory holds.

    Args:
        sweep_dir: The sweep's directory

    Returns:
        The member ids
    """
    return {member.member_id for path in sweep_dir.iterdir() if path.is_file()
            for member in _matching_member(path.name, list(SWEEP_ENTRY_MEMBERS))}


def assert_run_dir_declared(run_dir: Path) -> None:
    """
    Fail when a run directory holds a file the run store does not declare, or a stream whose
    header disagrees with its declared version.

    Args:
        run_dir: The run's directory
    """
    undeclared = undeclared_files(run_dir)
    assert not undeclared, (
        f'{run_dir} holds files the run store does not declare: {undeclared} — declare each one '
        f'in run_entry_members.py, or stop writing it')
    mismatches = stream_version_mismatches(run_dir)
    assert not mismatches, f'{run_dir}: {mismatches}'
