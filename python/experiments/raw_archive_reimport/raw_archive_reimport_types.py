"""
Types of the raw-archive re-import tool: what a member of the archive is, what a re-import
selects, what it records before it writes, and what its checks answer.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class MemberOrigin(Enum):
    """Where a raw file of the archive lies."""
    # Inside a zip directly under the archives directory
    ZIP = 'zip'
    # Loose in the finished directory and in no zip: a source, not a copy
    LOOSE = 'loose'


class BrokerSource(Enum):
    """What a member's broker type was read from."""
    # The tick index row whose source_file is the member
    TICK_INDEX = 'tick_index'
    # The member's own JSON header (broker_type, or the older data_collector)
    MEMBER_HEADER = 'member_header'
    # Neither answered with a configured broker type: reported, never selected
    UNRESOLVED = 'unresolved'


class ReimportCheckId(Enum):
    """The checks verify runs once the import has run."""
    # Archive sources and index rows over the (broker, symbols) scope are equally many
    COUNT = 'count'
    # The same scope, name by name, in both directions
    NAMES = 'names'
    # No selected file is left in the importer's inbox
    INBOX = 'inbox'
    # Every selected file's parquet was written after the file was extracted
    REWRITTEN = 'rewritten'


@dataclass(frozen=True)
class TickIndexRow:
    """One row of the tick index, as this tool reads it."""
    source_file: str
    broker_type: str
    symbol: str
    parquet_path: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    tick_count: int


@dataclass(frozen=True)
class ArchiveMember:
    """
    One raw tick file of the archive.

    `name` is the file's own name, whatever the zip that holds it is called. `container` is the
    zip for a ZIP member and the file itself for a LOOSE one; `entry_name` is the path inside
    the zip (the file name for a LOOSE member). `index_start` / `index_end` are the span of the
    member's tick index row as stored, None when the index does not know the member.
    """
    name: str
    symbol: str
    name_time: datetime
    broker_type: Optional[str]
    broker_source: BrokerSource
    origin: MemberOrigin
    container: Path
    entry_name: str
    size_bytes: int
    crc32: Optional[int]
    index_start: Optional[datetime]
    index_end: Optional[datetime]


@dataclass(frozen=True)
class ReimportScope:
    """
    What a re-import covers.

    `symbols` empty means every symbol of the broker. Both window ends are UTC-aware and
    inclusive; None leaves that side open.
    """
    broker_type: str
    symbols: Tuple[str, ...] = ()
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None


@dataclass
class ReimportManifest:
    """
    What extract selected and what the tick index said about it before anything was written.

    `before` holds the index rows each selected name had at extraction time (an empty tuple
    for a name the index did not know); `extracted_at` the moment each member reached the
    inbox, absent for a member the extraction did not reach.
    """
    tool_version: str
    created_at: datetime
    scope: ReimportScope
    members: List[ArchiveMember]
    before: Dict[str, Tuple[TickIndexRow, ...]]
    extracted_at: Dict[str, datetime]


@dataclass(frozen=True)
class ReimportCheck:
    """One answer of verify: whether it passed, a line saying why, and the names that failed."""
    check_id: ReimportCheckId
    passed: bool
    detail: str
    offenders: Tuple[str, ...]
