"""
FiniexTestingIDE - Run Entry Writer (#621)
Writes one run's files by the run store's declaration: a writer names a member and hands over the
content, never a path it builds itself, and a member its pipeline does not write is refused.

Each shape has its method — a report section, a snapshot, a table, a stream — so where a file's
content lands can change in this one module. Two kinds still receive a path: a log, which its
logger opens and keeps open, and the two streams that keep their own writer until the stream half
of the run store is built (the order-event stream and the study captures).
"""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TypeVar

from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.artifact_specs import ArtifactSpec
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS_BY_ID,
    section_member_of,
)
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberShape,
    EntryMemberWriteMode,
    RunEntryMemberId,
)
from python.framework.utils.atomic_file_utils import write_text_atomic
from python.framework.utils.csv_table_utils import write_csv_table
from python.framework.utils.file_utils import create_directory

T = TypeVar('T')


class RunEntryWriter:
    """One run's files, written by member."""

    def __init__(self, run_dir: Path, run_type: str):
        """
        Args:
            run_dir: The run's directory
            run_type: Its pipeline — what decides which members it may write
        """
        self._run_dir = Path(run_dir)
        self._run_type = run_type

    def path_of(self, member_id: RunEntryMemberId, **names: str) -> Path:
        """
        Where one member of this run lies.

        Args:
            member_id: The member
            names: One value per variable part of the member's path

        Returns:
            The path
        """
        return self._run_dir / self._member(member_id).path_for(**names)

    def write_section(self, spec: ArtifactSpec[T], report: T) -> Path:
        """
        Write one report section.

        Args:
            spec: The section
            report: The built report

        Returns:
            The written file
        """
        member = self._member(section_member_of(spec.filename).member_id)
        path = self._run_dir / member.path_for()
        create_directory(path.parent, holds="the run's report sections")
        return write_artifact(report, path.parent, spec)

    def write_snapshot(self, member_id: RunEntryMemberId, text: str, **names: str) -> Path:
        """
        Write one snapshot whole, replacing what was there — old or new, never half.

        Args:
            member_id: The member, a snapshot
            text: The full content
            names: One value per variable part of the member's path

        Returns:
            The written file
        """
        member = self._member(member_id, EntryMemberShape.SNAPSHOT)
        path = self._run_dir / member.path_for(**names)
        if member.write_mode is EntryMemberWriteMode.ATOMIC_REPLACE:
            write_text_atomic(path, text)
        else:
            create_directory(path.parent, holds=f'the file {path.name}')
            path.write_text(text, encoding='utf-8')
        return path

    def write_table(self, member_id: RunEntryMemberId, columns: List[str],
                    rows: Iterable[Dict[str, Any]], ignore_extra_columns: bool = False,
                    **names: str) -> Path:
        """
        Write one table as CSV: a header row, then one row per record.

        Args:
            member_id: The member, a report output written as a table
            columns: The column names, in order
            rows: One mapping of column to value per row
            ignore_extra_columns: Leave out a row's keys that are no column, instead of refusing
                the row — for a table whose rows a strategy fills
            names: One value per variable part of the member's path

        Returns:
            The written file
        """
        member = self._member(member_id, EntryMemberShape.REPORT_OUTPUT)
        return write_csv_table(self._run_dir / member.path_for(**names), columns, rows,
                               ignore_extra_columns)

    def open_stream(self, member_id: RunEntryMemberId, run_id: str,
                    logger: Optional[AbstractLogger], stops: str,
                    header_fields: Optional[Dict[str, Any]] = None) -> RunStreamAppender:
        """
        Open one stream: its header line names the member and the version the declaration gives it.

        Args:
            member_id: The member, a stream
            run_id: The run, written into the header line
            logger: Where the first failed write is said
            stops: What the failure means for the session, said with it
            header_fields: Further fields of the header line

        Returns:
            The appender; one that could not open says so and writes nothing
        """
        member = self._member(member_id, EntryMemberShape.STREAM)
        return RunStreamAppender(
            self._run_dir / member.path_for(), member.member_id.value, member.schema_version,
            run_id, logger, stops=stops, header_fields=header_fields)

    def write_strategy_file(self, member_id: RunEntryMemberId, text: str, **names: str) -> Path:
        """
        Write one of the strategy's own files whole, replacing what was there — old or new,
        never half.

        Args:
            member_id: The member, a strategy output
            text: The full content
            names: One value per variable part of the member's path

        Returns:
            The written file
        """
        member = self._member(member_id, EntryMemberShape.STRATEGY_OUTPUT)
        path = self._run_dir / member.path_for(**names)
        write_text_atomic(path, text)
        return path

    def log_path(self, member_id: RunEntryMemberId, **names: str) -> Path:
        """
        Where one log of this run lies, for its logger to open.

        Args:
            member_id: The member, a log
            names: One value per variable part of the member's path

        Returns:
            The path
        """
        return self._run_dir / self._member(member_id, EntryMemberShape.LOG).path_for(**names)

    def _member(self, member_id: RunEntryMemberId,
                shape: Optional[EntryMemberShape] = None) -> EntryMember:
        """
        The member as declared, refused when this pipeline does not write it or it is another
        shape than the method writes.

        Args:
            member_id: The member
            shape: The shape the calling method writes; None for any

        Returns:
            The declared member
        """
        member = RUN_ENTRY_MEMBERS_BY_ID[member_id]
        if not member.is_written_by(self._run_type):
            raise EntryMemberRefusedError(
                f'a {self._run_type} run does not write {member_id.value} '
                f'({member.path_pattern})')
        if shape is not None:
            member.require_shape(shape)
        return member
