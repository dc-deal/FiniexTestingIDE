"""
External History Types.

What a vendor's published archive is, as this project keeps and converts it (#561): the parts
fetched and their checksums, and what one conversion wrote.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class FetchedPart:
    """
    One downloaded part of a vendor archive.

    Args:
        name: File name as the vendor publishes it
        size: Bytes on disk
        sha256: Its SHA-256, computed here
        published_sha256: The checksum the vendor publishes for it, empty where none is published
    """
    name: str
    size: int
    sha256: str
    published_sha256: str = ''

    def matches_published(self) -> bool:
        """
        Whether the part is byte-identical to what the vendor published.

        Returns:
            True when a published checksum exists and agrees
        """
        return bool(self.published_sha256) and self.sha256 == self.published_sha256


@dataclass
class ExternalFetchManifest:
    """
    One fetch of a vendor archive, as kept in the external history store.

    Keyed on each part's checksum and the time it was fetched, never on the vendor's release
    label: a vendor may re-upload a release under the same label, so the label alone does not pin
    the bytes.

    Args:
        vendor: Who published the data
        dataset: Which of the vendor's datasets
        fetch_started: When the download began (UTC, provenance only)
        fetch_finished: When it ended (UTC, provenance only)
        parts: The parts that join into the archive, in order
        increments: Further archives fetched beside it, standalone
        vendor_manifest: The vendor's own manifest, verbatim
    """
    vendor: str
    dataset: str
    fetch_started: str
    fetch_finished: str
    parts: List[FetchedPart] = field(default_factory=list)
    increments: List[FetchedPart] = field(default_factory=list)
    vendor_manifest: Dict = field(default_factory=dict)


@dataclass
class ConvertedFile:
    """
    One collector-shaped file a conversion wrote.

    Args:
        name: File name in the import inbox
        first_tick: Event time of its first tick (UTC)
        last_tick: Event time of its last tick (UTC)
        tick_count: Number of ticks
    """
    name: str
    first_tick: datetime
    last_tick: datetime
    tick_count: int


@dataclass
class ConversionResult:
    """
    What converting one symbol produced.

    Args:
        symbol: Our symbol
        member: The archive member it was read from
        rows_read: Rows the member holds
        rows_converted: Rows before the cut, written as ticks
        cut_at: The instant conversion stopped at — our first collected tick, floored to the
            minute; None where we never collected the symbol
        files: The files written, in order
    """
    symbol: str
    member: str
    rows_read: int = 0
    rows_converted: int = 0
    cut_at: Optional[datetime] = None
    files: List[ConvertedFile] = field(default_factory=list)
