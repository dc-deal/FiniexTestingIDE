"""
FiniexTestingIDE - Import Inbox Types
What leaves the import inbox, and where it goes (#120): how the import of each file ended,
where a file or a zip went afterwards, and the members of a zip. One inbox holds both kinds of
raw file — tick exports and signal files, loose or in zips — and each goes to its own import.

The inbox is drained by every import. A file the import took — written, or skipped by the
duplicate check because the archive holds it — goes to the finished directory; one it refused
or failed on goes to the refused directory with its reason beside it. Only what the import could
not reach stays in the inbox.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class InboxImportOutcome(Enum):
    """
    How the import of one tick export ended.
    """
    IMPORTED = 'imported'
    # A file of this name is in the archive already, and the import ran without --override
    ALREADY_ARCHIVED = 'already_archived'
    # The export is valid and holds no tick: nothing to write, counted apart and named
    EMPTY = 'empty'
    # The validation refused it, before anything was written
    REFUSED = 'refused'
    # An error while converting or writing it
    FAILED = 'failed'

    def is_taken(self) -> bool:
        """
        Whether the import is through with the export: written now, already there, or valid and
        empty — each leaves for the finished directory.

        Returns:
            True for IMPORTED, ALREADY_ARCHIVED and EMPTY
        """
        return self in (InboxImportOutcome.IMPORTED, InboxImportOutcome.ALREADY_ARCHIVED,
                        InboxImportOutcome.EMPTY)


@dataclass(frozen=True)
class InboxImportResult:
    """
    The end of one export's import.

    Args:
        outcome: How it ended
        reason: Why it was refused or failed, None when it was taken
    """
    outcome: InboxImportOutcome
    reason: Optional[str] = None


class InboxDisposition(Enum):
    """
    Where a file or a zip went when it left the tick import inbox.
    """
    MOVED_TO_FINISHED = 'moved_to_finished'
    MOVED_TO_REFUSED = 'moved_to_refused'
    # An identical file of that name lay at the destination already; the inbox copy was removed
    ALREADY_THERE = 'already_there'
    KEPT_IN_INBOX = 'kept_in_inbox'


@dataclass(frozen=True)
class InboxMove:
    """
    The result of moving one file or zip out of the tick import inbox.

    Args:
        disposition: Where it went
        destination: The directory it went to, or would have gone to; None when moving is off
        detail: Why it stayed in the inbox, None when it left
    """
    disposition: InboxDisposition
    destination: Optional[Path] = None
    detail: Optional[str] = None


class InboxFileKind(Enum):
    """
    Which import a raw file of the inbox goes to, told by its name.
    """
    # A collector's tick export: SYMBOL_YYYYMMDD_HHMMSS_ticks.json
    TICK_EXPORT = 'tick_export'
    # A signal producer's JSONL of envelopes; its pipeline is read from the envelopes
    SIGNAL_FILE = 'signal_file'


@dataclass(frozen=True)
class InboxZipMember:
    """
    One raw file inside a zip of the inbox.

    Args:
        zip_path: The zip that holds it
        name: The member's name, which is the file's name
        size_bytes: Its size unpacked, from the zip's central directory
    """
    zip_path: Path
    name: str
    size_bytes: int


@dataclass
class InboxZipContent:
    """
    What a zip of the inbox holds, read from its central directory.

    Args:
        zip_path: The zip
        members: Its files, sorted by name; empty when the zip is refused
        refusal: Why the zip is refused as a whole, None when its members can be imported
        kind: What its files are — one zip holds one kind; None when it is refused
    """
    zip_path: Path
    members: List[InboxZipMember] = field(default_factory=list)
    refusal: Optional[str] = None
    kind: Optional[InboxFileKind] = None


@dataclass
class InboxZipSettlement:
    """
    A zip of the inbox after the import: how each member's import ended and where the zip went.

    Args:
        zip_path: Where the zip lay in the inbox
        results: Each member's result by name; empty for a zip refused as a whole
        move: Where the zip went
        reason: Why the zip went to the refused directory or stayed in the inbox, None otherwise
        member_moves: Where the copy of each member the import refused or failed on went, by
            name — filled when the zip itself went to the finished directory
    """
    zip_path: Path
    results: Dict[str, InboxImportResult]
    move: InboxMove
    reason: Optional[str] = None
    member_moves: Dict[str, InboxMove] = field(default_factory=dict)

    def count(self, outcome: InboxImportOutcome) -> int:
        """
        How many members ended with an outcome.

        Args:
            outcome: The outcome

        Returns:
            The count
        """
        return sum(1 for result in self.results.values() if result.outcome is outcome)
