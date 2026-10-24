"""
FiniexTestingIDE - Run Entry Reader (#621)
Reads one run's files by the run store's declaration: a caller asks for a run and a member, never
for a path it builds itself.

Where a run lives comes from the run index, by an exact match on the id — the guard a run id
arriving from a URL needs, since a pattern built from it would match whatever the pattern says.
Whether a file is there is asked of the disk, never of the index row: the row's lists are stamped
when the reports land, so between a section's write and that stamp — or for a run whose row a
concurrent write lost — the row would say no where the file is there.

Reading a stream's content stays with the stream readers; this hands them the path. Once a run
is found, its directory is read by the run directory reader.
"""

from pathlib import Path
from typing import Dict, Iterable, Optional, TypeVar

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.artifact_specs import ArtifactSpec
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_members import section_member_of
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

    def directory(self, run_id: str) -> Optional[RunDirReader]:
        """
        One run's directory, to read by member.

        Args:
            run_id: The run

        Returns:
            Its reader, or None when the index does not carry the run
        """
        run_dir = self.run_dir(run_id)
        return RunDirReader(run_dir) if run_dir is not None else None

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
        directory = self.directory(run_id)
        return directory.path_of(member_id, **names) if directory is not None else None

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
        directory = self.directory(run_id)
        return directory is not None and directory.has(member_id, **names)

    def read_header(self, run_id: str) -> Optional[RunHeader]:
        """
        One run's header.

        Args:
            run_id: The run

        Returns:
            The header, or None when the run is unknown or holds no header
        """
        directory = self.directory(run_id)
        if directory is None or not directory.has(RunEntryMemberId.HEADER):
            return None
        return directory.read_header()

    def read_section(self, run_id: str, spec: ArtifactSpec[T]) -> Optional[T]:
        """
        One report section of one run.

        Args:
            run_id: The run
            spec: The section

        Returns:
            The decoded section, or None when the run is unknown or did not write it
        """
        directory = self.directory(run_id)
        return directory.read_section(spec) if directory is not None else None

    def stream_path(self, run_id: str, member_id: RunEntryMemberId) -> Optional[Path]:
        """
        Where one stream of one run lies, for its reader to open.

        Args:
            run_id: The run
            member_id: The stream

        Returns:
            The path when the stream is there; None otherwise
        """
        directory = self.directory(run_id)
        return directory.stream_path(member_id) if directory is not None else None


def section_member(spec: ArtifactSpec) -> EntryMember:
    """
    The member a report section is.

    Args:
        spec: The section

    Returns:
        Its member
    """
    return section_member_of(spec.filename)
