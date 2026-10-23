"""
FiniexTestingIDE - Run Entry Reader (#621)
Reads one run's files by the run store's declaration: a caller asks for a run and a member, never
for a path it builds itself.

Where a run lives comes from the run index, by an exact match on the id — the guard a run id
arriving from a URL needs, since a pattern built from it would match whatever the pattern says.
Whether a file is there is asked of the disk, never of the index row: the row's lists are stamped
when the reports land, so between a section's write and that stamp — or for a run whose row a
concurrent write lost — the row would say no where the file is there.

Reading a stream's content stays with the stream readers; this hands them the path.
"""

from pathlib import Path
from typing import Dict, Iterable, Optional, TypeVar

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.run_layout_constants import IO_SUBDIR
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.reporting.io.artifact_specs import ArtifactSpec
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.io.run_header_io import read_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS,
    RUN_ENTRY_MEMBERS_BY_ID,
)
from python.framework.types.api.report_types import RunHeader
from python.framework.types.store_member_types import EntryMember, RunEntryMemberId

T = TypeVar('T')


class RunEntryReader:
    """One run's files, by member."""

    def __init__(self, run_index_path: Optional[Path] = None):
        """
        Args:
            run_index_path: The run index; the configured one when omitted
        """
        if run_index_path is None:
            run_index_path = AppConfigManager().get_file_logging_config_object().run_index
        self._index = RunIndex(Path(run_index_path))
        self._run_dirs: Dict[str, Path] = {}

    def remember_run_dirs(self, run_ids: Iterable[str]) -> None:
        """
        Look up where several runs live in ONE read of the index, for a caller about to ask about
        all of them. Only a run the index carries is remembered: one it does not carry yet is
        looked up again when it is asked about, since it may register later.

        Args:
            run_ids: The runs
        """
        ids = [run_id for run_id in run_ids if run_id not in self._run_dirs]
        for run_id, run_dir in zip(ids, self._index.run_dirs_of(ids)):
            if run_dir:
                self._run_dirs[run_id] = Path(run_dir)

    def run_dir(self, run_id: str) -> Optional[Path]:
        """
        Where one run lives.

        Args:
            run_id: The run

        Returns:
            Its directory, or None when the index does not carry it
        """
        if run_id not in self._run_dirs:
            self.remember_run_dirs([run_id])
        return self._run_dirs.get(run_id)

    def path_of(self, run_id: str, member_id: RunEntryMemberId, **names: str) -> Optional[Path]:
        """
        Where one member of one run lies, whether or not it is there.

        Args:
            run_id: The run
            member_id: The member
            names: One value per variable part of the member's path

        Returns:
            The path, or None when the run is unknown
        """
        run_dir = self.run_dir(run_id)
        if run_dir is None:
            return None
        return run_dir / RUN_ENTRY_MEMBERS_BY_ID[member_id].path_for(**names)

    def has(self, run_id: str, member_id: RunEntryMemberId, **names: str) -> bool:
        """
        Whether one member of one run is there — asked of the disk.

        Args:
            run_id: The run
            member_id: The member
            names: One value per variable part of the member's path

        Returns:
            True when the file exists
        """
        path = self.path_of(run_id, member_id, **names)
        return path is not None and path.is_file()

    def read_header(self, run_id: str) -> Optional[RunHeader]:
        """
        One run's header.

        Args:
            run_id: The run

        Returns:
            The header, or None when the run is unknown or holds no header
        """
        path = self.path_of(run_id, RunEntryMemberId.HEADER)
        return read_run_header(path) if path is not None and path.is_file() else None

    def read_section(self, run_id: str, spec: ArtifactSpec[T]) -> Optional[T]:
        """
        One report section of one run.

        Args:
            run_id: The run
            spec: The section

        Returns:
            The decoded section, or None when the run is unknown or did not write it
        """
        member = section_member(spec)
        path = self.path_of(run_id, member.member_id)
        return read_artifact(path, spec) if path is not None and path.is_file() else None

    def stream_path(self, run_id: str, member_id: RunEntryMemberId) -> Optional[Path]:
        """
        Where one stream of one run lies, for its reader to open.

        Args:
            run_id: The run
            member_id: The stream

        Returns:
            The path when the stream is there; None otherwise
        """
        path = self.path_of(run_id, member_id)
        return path if path is not None and path.is_file() else None


def section_member(spec: ArtifactSpec) -> EntryMember:
    """
    The member a report section is.

    Args:
        spec: The section

    Returns:
        Its member
    """
    path = f'{IO_SUBDIR}/{spec.filename}'
    for member in RUN_ENTRY_MEMBERS:
        if member.path_pattern == path:
            return member
    raise EntryMemberRefusedError(f'no member of a run is the report section {spec.filename}')
