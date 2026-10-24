"""
Run entry census helpers (#621) — whether a run directory holds what the run store declares.

Shared by the census suite and by the suites that produce a real run of their own, such as the
acceptance studies, so every run a test makes can be held to the same declaration.
"""

import json
from pathlib import Path
from typing import List, Set

from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.store.entry_members.run_footprint import (
    members_present,
    run_files,
    run_type_of,
    undeclared_files,
)
from python.framework.store.entry_members.sweep_entry_members import SWEEP_ENTRY_MEMBERS
from python.framework.types.store_member_types import EntryMemberShape, SweepEntryMemberId

# The run directory's own reads are the framework's (`run_footprint`); they are named here so a
# suite needs one import for the whole census.
__all__ = ['members_present', 'run_files', 'run_type_of', 'undeclared_files',
           'stream_version_mismatches', 'undeclared_sweep_files', 'sweep_members_present',
           'assert_run_dir_declared']


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
                  if path.is_file() and not any(member.matches(path.name) for member in members))


def sweep_members_present(sweep_dir: Path) -> Set[SweepEntryMemberId]:
    """
    Which members a sweep directory holds.

    Args:
        sweep_dir: The sweep's directory

    Returns:
        The member ids
    """
    return {member.member_id for path in sweep_dir.iterdir() if path.is_file()
            for member in SWEEP_ENTRY_MEMBERS if member.matches(path.name)}


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
