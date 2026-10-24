"""
FiniexTestingIDE - Run Dir Reader (#621)
Reads one run directory's files by the run store's declaration, for a caller that already holds
the directory: the run itself while it reports, a walk that found runs by their header, the
pruner, a certificate. A caller asks for a member, never for a path it builds itself.

What a file holds is read the way its own reader reads it, and the errors are that reader's: a
section that no longer fits its model raises the validation error, a header that is gone raises
like any missing file. Whether a file is there is asked of the disk.
"""

from pathlib import Path
from typing import List, Optional, TypeVar

from python.framework.reporting.io.artifact_specs import ArtifactSpec
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.io.run_header_io import read_run_header
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS_BY_ID,
    run_member_folder,
    run_member_path,
    section_member_of,
)
from python.framework.types.api.report_types import RunHeader
from python.framework.types.store_member_types import RunEntryMemberId

T = TypeVar('T')


class RunDirReader:
    """One run directory's files, by member."""

    def __init__(self, run_dir: Path):
        """
        Args:
            run_dir: The run's directory
        """
        self._run_dir = Path(run_dir)

    def get_run_dir(self) -> Path:
        """
        The directory this reader reads.

        Returns:
            The run's directory
        """
        return self._run_dir

    def path_of(self, member_id: RunEntryMemberId, **names: str) -> Path:
        """
        Where one member lies, whether or not it is there.

        Args:
            member_id: The member
            names: One value per variable part of the member's path

        Returns:
            The path
        """
        return self._run_dir / run_member_path(member_id, **names)

    def has(self, member_id: RunEntryMemberId, **names: str) -> bool:
        """
        Whether one member is there — asked of the disk.

        Args:
            member_id: The member
            names: One value per variable part of the member's path

        Returns:
            True when the file exists
        """
        return self.path_of(member_id, **names).is_file()

    def read_header(self) -> RunHeader:
        """
        The run's header. A run directory always holds one, so a missing header raises.

        Returns:
            The header
        """
        return read_run_header(self.path_of(RunEntryMemberId.HEADER))

    def section_path(self, spec: ArtifactSpec) -> Path:
        """
        Where one report section lies, whether or not it is there.

        Args:
            spec: The section

        Returns:
            The path
        """
        return self.path_of(section_member_of(spec.filename).member_id)

    def read_section(self, spec: ArtifactSpec[T]) -> Optional[T]:
        """
        One report section.

        Args:
            spec: The section

        Returns:
            The decoded section, or None when the run did not write it
        """
        path = self.section_path(spec)
        return read_artifact(path, spec) if path.is_file() else None

    def stream_path(self, member_id: RunEntryMemberId) -> Optional[Path]:
        """
        Where one stream lies, for its reader to open.

        Args:
            member_id: The stream

        Returns:
            The path when the stream is there; None otherwise
        """
        path = self.path_of(member_id)
        return path if path.is_file() else None

    def files_of(self, member_id: RunEntryMemberId) -> List[Path]:
        """
        Every file of one member whose path has variable parts — one per scenario, per day, per
        sink.

        Args:
            member_id: The member

        Returns:
            The files, sorted by path
        """
        member = RUN_ENTRY_MEMBERS_BY_ID[member_id]
        folder = self._run_dir / run_member_folder(member_id)
        if not folder.is_dir():
            return []
        return sorted(path for path in folder.rglob('*') if path.is_file()
                      and member.matches(path.relative_to(self._run_dir).as_posix()))
