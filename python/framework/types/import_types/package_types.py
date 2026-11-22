"""
FiniexTestingIDE - Package Types
The packages of a package service as the import sees them (#120) — the data collector's tick
packages, the RAG engine's signal packages: whether a service is switched on, an entry of its
list, the parts of a manifest the import checks, what the fetch did with a package, and how its
receipt ended.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from python.framework.types.config_types.package_service_config_types import ActivePackageSource
from python.framework.types.import_types.import_progress_types import ImportSection


class PackageServiceState(Enum):
    """
    Whether an import may fetch from a package service, as its configuration says.
    """
    ON = 'on'
    # Its block says enabled: false
    SWITCHED_OFF = 'switched_off'
    # Switched on, but its active endpoint names no address
    NO_ADDRESS = 'no_address'


@dataclass(frozen=True)
class PackageServiceResolution:
    """
    One package service as its configuration resolves it — the import fetches from the ones that
    are on, and the status command lists every one.

    Args:
        package_service: The service's name, e.g. 'collector' or 'rag'
        delivers: The archive its packages feed
        state: Whether it is switched on and has an address
        endpoint_name: The active endpoint's name in its file
        address_set: Whether the active endpoint names an address, switched on or not
        credentials_file: The credential file's configured name
        source: The service to fetch from; None unless the state is ON
    """
    package_service: str
    delivers: ImportSection
    state: PackageServiceState
    endpoint_name: str
    address_set: bool
    credentials_file: str
    source: Optional[ActivePackageSource]

    def describe_state(self) -> str:
        """
        The state in the words a skipped fetch shows.

        Returns:
            e.g. 'rag: switched off'
        """
        words = {PackageServiceState.ON: 'on',
                 PackageServiceState.SWITCHED_OFF: 'switched off',
                 PackageServiceState.NO_ADDRESS: 'no address'}[self.state]
        return f'{self.package_service}: {words}'


@dataclass(frozen=True)
class PackageServiceStatus:
    """
    One package service as the status command shows it: its configuration, and with a check what
    its list holds.

    Args:
        package_service: The service's name
        delivers: The archive its packages feed
        state: Whether it is switched on and has an address
        endpoint_name: The active endpoint's name
        address_set: Whether the active endpoint names an address
        credential: Which file the credential cascade answered from, and whether its token is
            empty — never the token
        waiting: Packages waiting for our receipt; None when the list was not read
        acknowledged: Packages acknowledged; None when the list was not read
        newest_package: The newest package listed; None when the list was not read or is empty
        check_note: Why the list was not read when a check was asked for ('' otherwise)
    """
    package_service: str
    delivers: ImportSection
    state: PackageServiceState
    endpoint_name: str
    address_set: bool
    credential: str
    waiting: Optional[int] = None
    acknowledged: Optional[int] = None
    newest_package: Optional[str] = None
    check_note: str = ''


class PackageState(Enum):
    """
    Where a package stands at the collector, in its own words.
    """
    # Waiting for our receipt
    READY = 'ready'
    # Confirmed by a receipt carrying the zip's hash
    DELIVERED = 'delivered'


@dataclass(frozen=True)
class PackageListing:
    """
    One package as the service lists it.

    Args:
        package_id: e.g. 'kraken-0003-20261115' — source, sequence, cutoff date. A source may
            hold an underscore ('crypto_sentiment'), so an id is read from the right
        source: The package source: a collector ('kraken', 'mt5', 'krakendev') or a signal
            pipeline
        size_bytes: The zip's size
        sha256: The zip's SHA-256
        state: Whether it waits for our receipt
    """
    package_id: str
    source: str
    size_bytes: int
    sha256: str
    state: PackageState


@dataclass
class PackageManifest:
    """
    The parts of a package's manifest the import relies on. The whole manifest is kept beside the
    zip as the service served it; these are the fields checked against what arrived.

    Args:
        package_id: The package
        zip_name: The zip's file name
        zip_sha256: The zip's SHA-256
        zip_size_bytes: The zip's size
        member_sha256: Each member's SHA-256, by name
        member_size_bytes: Each member's size unpacked, by name — with its name and its hash the
            third field of a member's integrity
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
    member_size_bytes: Dict[str, int] = field(default_factory=dict)
    held_for_next: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)
    open_files: List[str] = field(default_factory=list)
    receipt_recorded: bool = False


class PackageFetchOutcome(Enum):
    """
    What the fetch did with one listed package.
    """
    # Downloaded and proven by its hash; it now lies in the inbox
    FETCHED = 'fetched'
    # Its zip lay in the inbox already under its own name and hash — carried in by hand, or left
    # by an import that stopped before it took it; nothing was downloaded
    IN_INBOX = 'in_inbox'
    # Its zip lies in the finished directory already: only the receipt is due
    ALREADY_HERE = 'already_here'
    # Its zip lies in the refused directory: not fetched again, not acknowledged
    REFUSED_BEFORE = 'refused_before'
    # The service gave up, or what arrived was not the package
    FAILED = 'failed'


@dataclass
class PackageFetch:
    """
    One listed package after the fetch.

    Args:
        package_service: The service that listed it, and that its receipt goes to
        listing: The package as listed
        outcome: What the fetch did with it
        manifest: Its manifest, None when it could not be read
        detail: Why it failed or was not fetched, None otherwise
    """
    package_service: str
    listing: PackageListing
    outcome: PackageFetchOutcome
    manifest: Optional[PackageManifest] = None
    detail: Optional[str] = None


class PackageReceiptOutcome(Enum):
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
