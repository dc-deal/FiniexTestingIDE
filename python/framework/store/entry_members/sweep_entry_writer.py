"""
FiniexTestingIDE - Sweep Entry Writer (#621)
Writes a sweep directory's own files by the sweep store's declaration: the log of the data load
every combination reuses, and the ranking. A writer names a member, never a path it builds itself;
each combination is a run, written by the run entry writer.
"""

from pathlib import Path
from typing import Any, Dict, Iterable, List

from python.framework.store.entry_members.sweep_entry_members import SWEEP_ENTRY_MEMBERS_BY_ID
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberShape,
    SweepEntryMemberId,
)
from python.framework.utils.csv_table_utils import write_csv_table


class SweepEntryWriter:
    """One sweep's own files, written by member."""

    def __init__(self, sweep_dir: Path):
        """
        Args:
            sweep_dir: The sweep's directory
        """
        self._sweep_dir = Path(sweep_dir)

    def write_table(self, member_id: SweepEntryMemberId, columns: List[str],
                    rows: Iterable[Dict[str, Any]]) -> Path:
        """
        Write one table as CSV: a header row, then one row per record.

        Args:
            member_id: The member, a report output written as a table
            columns: The column names, in order
            rows: One mapping of column to value per row

        Returns:
            The written file
        """
        member = self._member(member_id, EntryMemberShape.REPORT_OUTPUT)
        return write_csv_table(self._sweep_dir / member.path_for(), columns, rows)

    def log_path(self, member_id: SweepEntryMemberId) -> Path:
        """
        Where one log of this sweep lies, for its logger to open.

        Args:
            member_id: The member, a log

        Returns:
            The path
        """
        return self._sweep_dir / self._member(member_id, EntryMemberShape.LOG).path_for()

    def _member(self, member_id: SweepEntryMemberId, shape: EntryMemberShape) -> EntryMember:
        """
        The member as declared, refused when it is another shape than the method writes.

        Args:
            member_id: The member
            shape: The shape the calling method writes

        Returns:
            The declared member
        """
        member = SWEEP_ENTRY_MEMBERS_BY_ID[member_id]
        member.require_shape(shape)
        return member
