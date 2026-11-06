"""
FiniexTestingIDE - Tick Package Types
The data collector's weekly tick packages as the import sees them (#120): an entry of the
service's list, the parts of a manifest the import checks, what the fetch did with a package, and
how its receipt ended.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class TickPackageState(Enum):
    """
    Where a package stands at the collector, in its own words.
    """
    # Waiting for our receipt
    READY = 'ready'
    # Confirmed by a receipt carrying the zip's hash
    DELIVERED = 'delivered'


@dataclass(frozen=True)
class TickPackageListing:
    """
    One package as the service lists it.

    Args:
        package_id: e.g. 'kraken-0003-20261115' — collector, sequence, cutoff date
        collector: 'kraken' or 'mt5'
        size_bytes: The zip's size
        sha256: The zip's SHA-256
        state: Whether it waits for our receipt
    """
    package_id: str
    collector: str
    size_bytes: int
    sha256: str
    state: TickPackageState


@dataclass
class TickPackageManifest:
    """
    The parts of a package's manifest the import relies on. The whole manifest is kept beside the
    zip as the service served it; these are the fields checked against what arrived.

    Args:
        package_id: The package
        zip_name: The zip's file name
        zip_sha256: The zip's SHA-256
        zip_size_bytes: The zip's size
        member_sha256: Each member's SHA-256, by name
        held_for_next: Files that reach past the cutoff and wait for the next package
        excluded: Files left out, each with its reason
        open_files: Files still being written at the collector, never packed
        receipt_recorded: Whether the service holds a receipt for it already
    """
    package_id: str
    zip_name: str
    zip_sha256: str
    zip_size_bytes: int
    member_sha256: Dict[str, str] = field(default_factory=dict)
    held_for_next: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)
    open_files: List[str] = field(default_factory=list)
    receipt_recorded: bool = False


class TickPackageFetchOutcome(Enum):
    """
    What the fetch did with one listed package.
    """
    # Downloaded and proven by its hash; it now lies in the inbox
    FETCHED = 'fetched'
    # Its zip lies in the finished directory already: only the receipt is due
    ALREADY_HERE = 'already_here'
    # Its zip lies in the refused directory: not fetched again, not acknowledged
    REFUSED_BEFORE = 'refused_before'
    # The service gave up, or what arrived was not the package
    FAILED = 'failed'


@dataclass
class TickPackageFetch:
    """
    One listed package after the fetch.

    Args:
        listing: The package as listed
        outcome: What the fetch did with it
        manifest: Its manifest, None when it could not be read
        detail: Why it failed or was not fetched, None otherwise
    """
    listing: TickPackageListing
    outcome: TickPackageFetchOutcome
    manifest: Optional[TickPackageManifest] = None
    detail: Optional[str] = None


class TickReceiptOutcome(Enum):
    """
    How a package's receipt ended.
    """
    # 201: the service holds our receipt with the matching hash
    ACKNOWLEDGED = 'acknowledged'
    # 200: it held our receipt already; a repeat changes nothing
    ALREADY_ACKNOWLEDGED = 'already_acknowledged'
    # 409: what we hold is not this package; nothing was marked
    HASH_MISMATCH = 'hash_mismatch'
    # Another refusal by the service
    REFUSED = 'refused'
    # The answer was lost and reading the package back did not show a receipt: the next import
    # sends it again
    UNRESOLVED = 'unresolved'
