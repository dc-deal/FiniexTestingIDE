"""
FiniexTestingIDE - Run Footprint (#621)
A run directory read against the run store's declaration: which files are declared, which members
are there — and, once the run has closed, what its own footprint says it wrote.

A run writes `footprint.json` as its very last step. Without one it never got to its close; with
one, a file the footprint names and the disk lacks has been lost since, and a member written only
sometimes is judged by what the run said it expected rather than guessed from its absence.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.constants.run_layout_constants import (
    FOOTPRINT_FILE,
    FOOTPRINT_SCHEMA_VERSION,
    RUN_HEADER_ARTIFACT,
    RUN_TYPE_SIMULATION,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.types.run_footprint_types import (
    FootprintFile,
    MemberExpectation,
    MemberFootprint,
    MemberFootprintState,
    RunCloseState,
    RunFootprintAssessment,
    RunFootprintRecord,
)
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberShape,
    RunEntryMemberId,
)
from python.framework.utils.atomic_file_utils import write_text_atomic


def is_run_directory(directory: Path) -> bool:
    """
    Whether a directory is a run's own: it holds a run header.

    Args:
        directory: The directory

    Returns:
        True when a header lies in it
    """
    return (directory / RUN_HEADER_ARTIFACT).is_file()


def _read_header(run_dir: Path) -> Dict:
    """
    The run header as raw JSON — the fields the footprint needs, without the report model.

    Args:
        run_dir: The run's directory

    Returns:
        The header's fields
    """
    return json.loads((run_dir / RUN_HEADER_ARTIFACT).read_text(encoding='utf-8'))


def run_type_of(run_dir: Path) -> str:
    """
    The pipeline that wrote a run, as its header names it.

    Args:
        run_dir: The run's directory

    Returns:
        The header's run type
    """
    return _read_header(run_dir)['run_type']


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


def pipeline_members(run_type: str) -> List[EntryMember]:
    """
    The members one pipeline may write.

    Args:
        run_type: The pipeline

    Returns:
        Its members, in declaration order
    """
    return [member for member in RUN_ENTRY_MEMBERS if member.is_written_by(run_type)]


def _claims(relative_path: str, members: List[EntryMember]) -> List[EntryMember]:
    """
    The members a file is.

    Args:
        relative_path: The file's path inside the run
        members: The members to ask

    Returns:
        Every member whose pattern fits the path
    """
    return [member for member in members if member.matches(relative_path)]


def undeclared_files(run_dir: Path) -> List[str]:
    """
    The files a run directory holds that no member of its pipeline declares.

    Args:
        run_dir: The run's directory

    Returns:
        Their relative paths, sorted; empty when everything is declared
    """
    members = pipeline_members(run_type_of(run_dir))
    return [path for path in run_files(run_dir) if not _claims(path, members)]


def members_present(run_dir: Path) -> Dict[RunEntryMemberId, List[str]]:
    """
    Which members a run directory holds, with the files that are each of them.

    Args:
        run_dir: The run's directory

    Returns:
        Member id → its files' relative paths
    """
    members = pipeline_members(run_type_of(run_dir))
    present: Dict[RunEntryMemberId, List[str]] = {}
    for path in run_files(run_dir):
        for member in _claims(path, members):
            present.setdefault(member.member_id, []).append(path)
    return present


def expectation(member_id: RunEntryMemberId, held: bool, when: str,
                otherwise: str) -> MemberExpectation:
    """
    Whether a run expected a member written only sometimes, with the condition as it read.

    Args:
        member_id: The member
        held: Whether its condition held for this run
        when: How the condition reads when it held
        otherwise: How it reads when it did not

    Returns:
        The expectation
    """
    return MemberExpectation(member_id=member_id, expected=held, reason=when if held else otherwise)


def expectation_after(member_id: RunEntryMemberId, held: bool, reached: bool, when: str,
                      otherwise: str, not_reached: str) -> MemberExpectation:
    """
    Whether a run expected a member that opens only at a later step: a run that stopped before
    that step did not fail to write it, it never got there.

    Args:
        member_id: The member
        held: Whether its condition held for this run
        reached: Whether the run got to the step that writes it
        when: How the condition reads when it held
        otherwise: How it reads when it did not
        not_reached: How it reads when it held but the run stopped before the step

    Returns:
        The expectation
    """
    if held and not reached:
        return MemberExpectation(member_id=member_id, expected=False, reason=not_reached)
    return expectation(member_id, held, when, otherwise)


def write_footprint(run_dir: Path, run_id: str, run_type: str,
                    expectations: List[MemberExpectation]) -> None:
    """
    Close a run: write what its directory holds now, and what it expected of the members written
    only sometimes. Called as the run's very last step, so nothing but a log line follows it.

    Args:
        run_dir: The run's directory
        run_id: The run
        run_type: Its pipeline
        expectations: For the members written only sometimes, whether this run expected them
    """
    members = pipeline_members(run_type)
    files = []
    for path in run_files(run_dir):
        if path == FOOTPRINT_FILE:
            continue
        for member in _claims(path, members):
            files.append(FootprintFile(path=path, member_id=member.member_id,
                                       size_bytes=(run_dir / path).stat().st_size))
    record = RunFootprintRecord(
        schema_version=FOOTPRINT_SCHEMA_VERSION, run_id=run_id, run_type=run_type,
        closed_at=datetime.now(timezone.utc), files=files, expectations=expectations)
    write_text_atomic(run_dir / FOOTPRINT_FILE, record.model_dump_json(indent=2))


def read_footprint(run_dir: Path) -> Optional[RunFootprintRecord]:
    """
    The footprint a run closed with.

    Args:
        run_dir: The run's directory

    Returns:
        The record, or None when the run has none
    """
    path = run_dir / FOOTPRINT_FILE
    if not path.is_file():
        return None
    return RunFootprintRecord.model_validate_json(path.read_text(encoding='utf-8'))


def assess(run_dir: Path, running: bool = False) -> RunFootprintAssessment:
    """
    Read a run directory against its declaration and its footprint.

    Args:
        run_dir: The run's directory
        running: Whether the run is still going, as the caller found out

    Returns:
        The assessment
    """
    header = _read_header(run_dir)
    run_type = header['run_type']
    try:
        record = read_footprint(run_dir)
        unreadable = ''
    except (OSError, ValueError) as error:
        record, unreadable = None, f'{type(error).__name__}: {str(error).splitlines()[0]}'
    commissioned = (run_type != RUN_TYPE_SIMULATION
                    or header.get('reporting', 'expected') == 'expected')
    if record is not None:
        close_state = RunCloseState.CLOSED
    elif running:
        close_state = RunCloseState.RUNNING
    elif not commissioned:
        close_state = RunCloseState.NOT_COMMISSIONED
    else:
        close_state = RunCloseState.NOT_CLOSED
    present = members_present(run_dir)
    members = [_assess_member(member, run_type, present.get(member.member_id, []), record,
                              unreadable)
               for member in pipeline_members(run_type)]
    return RunFootprintAssessment(
        run_id=header['run_id'], run_type=run_type, close_state=close_state,
        closed_at=record.closed_at if record is not None else None,
        members=members, undeclared=undeclared_files(run_dir))


def assess_indexed_run(run_id: str) -> Optional[RunFootprintAssessment]:
    """
    Assess a run the run index knows, asking the activity list whether it still runs.

    Args:
        run_id: The run

    Returns:
        The assessment, or None when the index knows no directory of that run, or that directory
        holds no header
    """
    index_path = AppConfigManager().get_file_logging_config_object().run_index
    run_dir = RunIndex(index_path).run_dir(run_id)
    if run_dir is None or not is_run_directory(run_dir):
        return None
    running = any(entry.run_id == run_id for entry in ActivityRegistryReader().read().entries)
    return assess(run_dir, running=running)


def _assess_member(member: EntryMember, run_type: str, on_disk: List[str],
                   record: Optional[RunFootprintRecord], unreadable: str) -> MemberFootprint:
    """
    One member of a run, read against the footprint.

    Args:
        member: The member
        run_type: The run's pipeline
        on_disk: Its files on disk
        record: The run's footprint, or None when it has none
        unreadable: Why the footprint could not be read; empty when it could, or when there is none

    Returns:
        What the run directory says about the member
    """
    if member.member_id is RunEntryMemberId.FOOTPRINT:
        if unreadable:
            return MemberFootprint(member.member_id, MemberFootprintState.FAILED, on_disk,
                                   f'unreadable — {unreadable}')
        state = MemberFootprintState.PRESENT if on_disk else MemberFootprintState.UNKNOWN
        return MemberFootprint(member.member_id, state, on_disk,
                               '' if on_disk else 'the run has not closed')
    if record is None:
        if on_disk:
            return MemberFootprint(member.member_id, MemberFootprintState.PRESENT, on_disk)
        return MemberFootprint(member.member_id, MemberFootprintState.UNKNOWN,
                               reason='the run has not closed')
    in_footprint = [entry.path for entry in record.files if entry.member_id is member.member_id]
    lost = [path for path in in_footprint if path not in on_disk]
    if lost:
        return MemberFootprint(member.member_id, MemberFootprintState.MISSING, lost,
                               'in the footprint, not on disk')
    if on_disk:
        # A log keeps growing after the close, and a new day's log may open after it.
        late = ([] if member.shape is EntryMemberShape.LOG
                else [path for path in on_disk if path not in in_footprint])
        if late:
            return MemberFootprint(member.member_id, MemberFootprintState.LATE, late,
                                   'written after the run closed')
        return MemberFootprint(member.member_id, MemberFootprintState.PRESENT, on_disk)
    write = next(write for write in member.written_by if write.run_type == run_type)
    if not write.condition:
        return MemberFootprint(member.member_id, MemberFootprintState.FAILED,
                               reason='always written by this pipeline, not there at the close')
    expectation = next((entry for entry in record.expectations
                        if entry.member_id is member.member_id), None)
    if expectation is None:
        return MemberFootprint(member.member_id, MemberFootprintState.UNKNOWN,
                               reason=f'written only when {write.condition}; the run did not say')
    if expectation.expected:
        return MemberFootprint(member.member_id, MemberFootprintState.FAILED,
                               reason=f'expected — {expectation.reason} — but not written; the '
                                      f"run's log says why")
    return MemberFootprint(member.member_id, MemberFootprintState.SKIPPED,
                           reason=expectation.reason)
