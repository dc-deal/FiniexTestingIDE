"""
FiniexTestingIDE - Run Footprint Types (#621)
What a run wrote, as it says itself at its close, and how a run directory reads against that.

The record is what `footprint.json` holds; the assessment is what a reader makes of a run directory
with the declaration, the record and the files on disk side by side.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from python.framework.types.store_member_types import RunEntryMemberId


class FootprintFile(BaseModel):
    """
    One file the run held when it closed.

    Args:
        path: Its path inside the run directory
        member_id: The member it is
        size_bytes: Its size at the close
    """
    model_config = ConfigDict(extra='forbid')

    path: str
    member_id: RunEntryMemberId
    size_bytes: int


class MemberExpectation(BaseModel):
    """
    Whether the run expected to write a member that is written only sometimes, and why.

    Args:
        member_id: The member
        expected: True when its condition held for this run
        reason: The condition, as it read for this run
    """
    model_config = ConfigDict(extra='forbid')

    member_id: RunEntryMemberId
    expected: bool
    reason: str


class RunFootprintRecord(BaseModel):
    """
    A run's own account of what it wrote, written as its very last step.

    Args:
        schema_version: The version of this record's shape
        run_id: The run it describes
        run_type: The pipeline that wrote it
        closed_at: When it was written
        files: Every file the run directory held then, the footprint itself left out
        expectations: For the members written only sometimes, whether this run expected them
    """
    model_config = ConfigDict(extra='forbid')

    schema_version: int
    run_id: str
    run_type: str
    closed_at: datetime
    files: List[FootprintFile]
    expectations: List[MemberExpectation] = []


class MemberFootprintState(StrEnum):
    """What a run directory says about one member, read against the run's footprint."""
    PRESENT = 'present'        # on disk, and in the footprint where there is one
    SKIPPED = 'skipped'        # written only sometimes, and this run did not expect it
    FAILED = 'failed'          # expected — or always written — and not there at the close
    UNKNOWN = 'unknown'        # written only sometimes, and nothing says if this run expected it
    MISSING = 'missing'        # in the footprint, gone from the disk since
    LATE = 'late'              # on disk, but written after the run closed

    def needs_a_look(self) -> bool:
        """
        Whether a member in this state needs a look: a file that failed, was lost or came late.

        Returns:
            True for those three states
        """
        return self in (MemberFootprintState.FAILED, MemberFootprintState.MISSING,
                        MemberFootprintState.LATE)


class RunCloseState(StrEnum):
    """Whether a run got to its close."""
    CLOSED = 'closed'                      # its footprint is there
    RUNNING = 'running'                    # no footprint yet, and the run is still going
    NOT_CLOSED = 'not_closed'              # no footprint, not going: stopped before, or older
    NOT_COMMISSIONED = 'not_commissioned'  # never meant to report, so never to close


@dataclass
class MemberFootprint:
    """
    One member of a run, as the assessment found it.

    Args:
        member_id: The member
        state: What the run directory says about it
        paths: Its files — on disk, or in the footprint when they are gone
        reason: Why it is skipped, failed or unknown; empty otherwise
    """
    member_id: RunEntryMemberId
    state: MemberFootprintState
    paths: List[str] = field(default_factory=list)
    reason: str = ''


@dataclass
class RunFootprintAssessment:
    """
    A run directory read against its declaration and its footprint.

    Args:
        run_id: The run
        run_type: Its pipeline
        close_state: Whether it got to its close
        closed_at: When it closed, when it did
        members: One entry per member its pipeline may write
        undeclared: Files no member of its pipeline declares
    """
    run_id: str
    run_type: str
    close_state: RunCloseState
    closed_at: Optional[datetime]
    members: List[MemberFootprint]
    undeclared: List[str]

    def has_problems(self) -> bool:
        """
        Whether anything here needs a look: a lost, failed or late file, a file nobody declared, or
        a run that never got to its close.

        Returns:
            True when there is something to look at
        """
        return (bool(self.undeclared) or self.close_state is RunCloseState.NOT_CLOSED
                or any(member.state.needs_a_look() for member in self.members))
