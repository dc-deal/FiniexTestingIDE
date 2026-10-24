"""
FiniexTestingIDE - Study capture reader (#621, for #332 and #358)
How a release certificate finds the run it certifies and reads that run's capture.

The run is found by its identity — the header it wrote at its start — never by the newest file on
disk: a rehearsal that aborted after the real run is newer and is not the run. The headers are read
from the tree rather than from the run index, because this is evidence behind a release gate and
the index is a derived file that a concurrent write can leave a row short.
"""

import json
from pathlib import Path
from typing import List, Optional

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.exceptions.acceptance_study_errors import AcceptanceCaptureUnreadableError
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_members import run_member_path
from python.framework.types.acceptance_study_types.study_capture_types import (
    StudyCaptureContent,
    StudyEvidenceRun,
    StudyRunCandidate,
)
from python.framework.types.api.report_types import RunHeader
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.time_utils import parse_datetime


def read_study_capture(capture_path: Path) -> StudyCaptureContent:
    """
    Read a capture: its header and its lines. A last line cut off by a stopped session is left out
    and said; anything else that cannot be read is refused.

    Args:
        capture_path: The capture file

    Returns:
        Its header, its lines and whether the last one was cut off
    """
    try:
        lines = [line for line in Path(capture_path).read_text(encoding='utf-8').splitlines()
                 if line.strip()]
    except OSError as error:
        raise AcceptanceCaptureUnreadableError(
            capture_path, f'it cannot be opened ({error})') from error
    if not lines:
        raise AcceptanceCaptureUnreadableError(capture_path, 'it is empty — not even a header')
    try:
        header = json.loads(lines[0])
    except ValueError as error:
        raise AcceptanceCaptureUnreadableError(
            capture_path, 'its header line is not JSON') from error
    events = []
    cut_last_line = False
    for number, line in enumerate(lines[1:], start=2):
        try:
            events.append(json.loads(line))
        except ValueError as error:
            if number == len(lines):
                cut_last_line = True
                continue
            raise AcceptanceCaptureUnreadableError(
                capture_path, f'line {number} of {len(lines)} is not JSON') from error
    return StudyCaptureContent(header=header, events=events, cut_last_line=cut_last_line)


def find_study_runs(root: Path, capture_member: RunEntryMemberId, run_name: Optional[str] = None,
                    run_purpose: Optional[str] = None) -> List[StudyRunCandidate]:
    """
    The AutoTrader runs below a root that wrote a study's capture, newest first by their start.

    Args:
        root: Where to look
        capture_member: The capture a run directory must hold
        run_name: Only runs of this profile; None for any
        run_purpose: Only runs whose header declares this purpose; None for any

    Returns:
        The candidates, newest start first
    """
    if not root.exists():
        return []
    candidates = []
    for header_path in root.rglob(run_member_path(RunEntryMemberId.HEADER)):
        run_dir = header_path.parent
        directory = RunDirReader(run_dir)
        capture_path = directory.path_of(capture_member)
        if not capture_path.is_file():
            continue
        try:
            header = json.loads(header_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if header.get('run_type') != RUN_TYPE_AUTOTRADER:
            continue
        if run_name is not None and header.get('run_name') != run_name:
            continue
        if run_purpose is not None and header.get('run_purpose') != run_purpose:
            continue
        candidates.append(StudyRunCandidate(
            run_dir=run_dir, run_id=header.get('run_id', run_dir.name),
            run_name=header.get('run_name', ''), start_time=parse_datetime(header['start_time']),
            capture_path=capture_path, closed=directory.has(RunEntryMemberId.FOOTPRINT)))
    return sorted(candidates, key=lambda candidate: candidate.start_time, reverse=True)


def read_study_run_header(run_dir: Path) -> Optional[RunHeader]:
    """
    The header of the run a capture lies in.

    Args:
        run_dir: The capture's directory

    Returns:
        The header, or None when the directory holds none that can be read
    """
    try:
        return RunDirReader(run_dir).read_header()
    except (OSError, ValueError):
        return None


def study_evidence_run(run_dir: Path) -> StudyEvidenceRun:
    """
    One run a study certificate rests on: which run, which commit its code came from, when it
    started.

    Args:
        run_dir: The capture's directory

    Returns:
        The run as its header names it; without a readable header, its directory's name and
        nothing else
    """
    header = read_study_run_header(run_dir)
    if header is None:
        return StudyEvidenceRun(run_id=run_dir.name, commit=None, started=None)
    return StudyEvidenceRun(run_id=header.run_id, commit=header.git_commit or None,
                            started=header.start_time)
