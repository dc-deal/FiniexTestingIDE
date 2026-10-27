"""
FiniexTestingIDE - Run Dir Reader (#621)
Reads one run directory's files by the run store's declaration, for a caller that already holds
the directory: the run itself while it reports, a walk that found runs by their header, the
pruner, a certificate. A caller asks for a member, never for a path it builds itself.

What a file holds is read the way its own reader reads it, and the errors are that reader's: a
section that no longer fits its model raises the validation error, a header that is gone raises
like any missing file. Whether a file is there is asked of the disk.

A stream is read as one across the trading days a session sealed out of it and its own file
(#476) — the caller asks for the stream, never for its files.
"""

from pathlib import Path
from typing import List, Optional, TypeVar

from python.framework.reporting.io.artifact_specs import ArtifactSpec
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.io.run_header_io import read_run_header
from python.framework.reporting.io.run_stream_union_reader import (
    read_stream_page,
    read_stream_union,
    read_stream_union_ends,
    read_stream_union_overview,
)
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS_BY_ID,
    run_member_path,
    section_member_of,
)
from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_stream_types import (
    RunStreamEnds,
    RunStreamLines,
    RunStreamOverview,
    RunStreamPage,
    StreamTimeWindow,
)
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

    def read_stream(self, member_id: RunEntryMemberId, window: Optional[StreamTimeWindow] = None,
                    **names: str) -> Optional[RunStreamLines]:
        """
        Read one stream across the trading days sealed out of it and its own file — within a time
        window when one is given.

        Args:
            member_id: The stream
            window: The stretch to read, or None for the whole stream
            names: One value per variable part of the stream's path

        Returns:
            Its records, or None when the run holds no such stream
        """
        return read_stream_union(
            self.path_of(member_id, **names), lambda: self.day_record_paths(member_id, **names),
            RUN_ENTRY_MEMBERS_BY_ID[member_id].time_fields or (), window)

    def read_stream_page(self, member_id: RunEntryMemberId, window: StreamTimeWindow, limit: int,
                         **names: str) -> Optional[RunStreamPage]:
        """
        One page of a stream within a time window — at most `limit` records unless one
        millisecond holds more, oldest first, each numbered by its position in the whole stream
        (#580).

        Args:
            member_id: The stream
            window: The stretch to read
            limit: The most records the page holds, at least one
            names: One value per variable part of the stream's path

        Returns:
            The page, or None when the run holds no such stream
        """
        return read_stream_page(
            self.path_of(member_id, **names), lambda: self.day_record_paths(member_id, **names),
            RUN_ENTRY_MEMBERS_BY_ID[member_id].time_fields or (), window, limit)

    def read_stream_ends(self, member_id: RunEntryMemberId,
                         **names: str) -> Optional[RunStreamEnds]:
        """
        One stream's header, its first record and its latest one, without the records between.

        Args:
            member_id: The stream
            names: One value per variable part of the stream's path

        Returns:
            The two ends, or None when the run holds no such stream
        """
        return read_stream_union_ends(
            self.path_of(member_id, **names), lambda: self.day_record_paths(member_id, **names))

    def read_stream_overview(self, member_id: RunEntryMemberId,
                             **names: str) -> Optional[RunStreamOverview]:
        """
        One stream's two ends and its trading days, from one read of its file (#580).

        Args:
            member_id: The stream
            names: One value per variable part of the stream's path

        Returns:
            The overview, or None when the run holds no such stream
        """
        return read_stream_union_overview(
            self.path_of(member_id, **names), lambda: self.day_record_paths(member_id, **names),
            RUN_ENTRY_MEMBERS_BY_ID[member_id].time_fields or ())

    def day_record_paths(self, member_id: RunEntryMemberId, **names: str) -> List[Path]:
        """
        The trading days a session sealed out of one stream (#476).

        Args:
            member_id: The stream
            names: One value per variable part of the stream's path

        Returns:
            The day records, oldest first; empty for a stream that is not sealed
        """
        day_member_id = RUN_ENTRY_MEMBERS_BY_ID[member_id].day_record
        if day_member_id is None:
            return []
        day_member = RUN_ENTRY_MEMBERS_BY_ID[day_member_id]
        # One folder per trading day, named by the day, so the names sort oldest first
        days_dir = self._run_dir / day_member.path_pattern.split('/', 1)[0]
        if not days_dir.is_dir():
            return []
        paths = (self._run_dir / day_member.path_for(day=day_dir.name, **names)
                 for day_dir in sorted(days_dir.iterdir()) if day_dir.is_dir())
        return [path for path in paths if path.is_file()]

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
        # Searched from the deepest folder that has no variable part: a day record's own folder
        # is a trading day, so its files are found from days/
        fixed: List[str] = []
        for part in member.path_pattern.split('/')[:-1]:
            if '{' in part:
                break
            fixed.append(part)
        folder = self._run_dir.joinpath(*fixed)
        if not folder.is_dir():
            return []
        return sorted(path for path in folder.rglob('*') if path.is_file()
                      and member.matches(path.relative_to(self._run_dir).as_posix()))
