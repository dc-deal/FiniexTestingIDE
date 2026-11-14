"""
FiniexTestingIDE - Tick Package Client
Reads the data collector's package service (#120): the list, a manifest, a zip, and sends the
receipt for a package the import took.

Every read runs under the connection ladder and through the classified HTTP attempt, so a
transport fault, a 429 or a 5xx is retried and a refusal is not. The receipt is a write: it is
never resent blindly — when its answer is lost, the package is read back, and a recorded receipt
settles it. The token is read from the credential cascade at each request and never kept.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from python.configuration.credentials_cascade import resolve_credentials_file
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.data_collector_config_types import ActivePackageSource
from python.framework.types.import_types.tick_package_types import (
    TickPackageListing,
    TickPackageManifest,
    TickPackageState,
    TickReceiptOutcome,
)
from python.framework.utils.connection_ladder import (
    ConnectionLadder,
    is_terminal_status,
    run_with_ladder,
)
from python.framework.utils.http_attempt import send_classified

vLog = get_global_logger()

# The connection as the operator reads it in a ladder's log line
LADDER_NAME = 'tick_packages'

_CHUNK_BYTES = 1 << 20
# The service's own limit for a receipt's note
_RECEIPT_NOTE_MAX = 500


class TickPackageClient:
    """
    The package service's routes, each answered or given up by the ladder.
    """

    def __init__(self, source: ActivePackageSource,
                 read_token: Optional[Callable[[], str]] = None,
                 wait: Optional[Callable[[float], None]] = None,
                 chunk_bytes: int = _CHUNK_BYTES) -> None:
        """
        Args:
            source: The service, its credential file and its ladder
            read_token: Reads the token for one request; the credential cascade when omitted
            wait: How to wait between attempts (time.sleep when omitted); a test injects one
            chunk_bytes: How much of a zip is read and written at a time. A download that breaks
                off keeps every chunk written before the break, so the next attempt resumes
                from there; the chunk it was reading is lost and fetched again
        """
        self._source = source
        self._chunk_bytes = chunk_bytes
        self._read_token = (read_token if read_token is not None
                            else lambda: _token_from_cascade(source.credentials_file))
        self._wait = wait
        self._ladder = ConnectionLadder(name=LADDER_NAME, policy=source.connection, logger=vLog)

    def list_packages(self) -> Optional[List[TickPackageListing]]:
        """
        Every package the service lists, oldest first. An entry in a state this client does not
        know is left out, with a warning.

        Returns:
            The packages, None when the ladder gave up
        """
        data = run_with_ladder(lambda: self._get_json('/v1/packages', 'the package list'),
                               self._ladder, self._wait)
        if data is None:
            return None
        entries = data.get('packages', []) if isinstance(data, dict) else data
        listings: List[TickPackageListing] = []
        for entry in entries:
            listing = _listing_of(entry)
            if listing is not None:
                listings.append(listing)
        return listings

    def read_manifest(self, package_id: str) -> Optional[Tuple[TickPackageManifest, Dict[str, Any]]]:
        """
        One package's manifest, parsed for the checks and as served.

        Args:
            package_id: The package

        Returns:
            (the parsed fields, the manifest as served), None when the ladder gave up
        """
        data = run_with_ladder(
            lambda: self._get_json(f'/v1/packages/{package_id}', f'package {package_id}'),
            self._ladder, self._wait)
        if data is None:
            return None
        return parse_manifest(data), data

    def download_zip(self, package_id: str, part: Path) -> bool:
        """
        Download a package's zip into a part file, resuming with a Range request where an earlier
        attempt — of this import or one before — left off.

        Args:
            package_id: The package
            part: The part file it is written to

        Returns:
            True when the download ran to its end, False when the ladder gave up
        """
        return run_with_ladder(lambda: self._download_once(package_id, part),
                               self._ladder, self._wait) is True

    def send_receipt(self, package_id: str, zip_sha256: str,
                     note: str) -> Tuple[TickReceiptOutcome, Optional[str]]:
        """
        Acknowledge a package with the hash of the zip we hold. Sent once: an answer that is lost,
        or one that says nothing about whether it was recorded, is settled by reading the package
        back — the next import sends it again when no receipt shows.

        Args:
            package_id: The package
            zip_sha256: The SHA-256 of our copy of its zip
            note: What the import did with it, cut to the service's limit

        Returns:
            How the receipt ended, and the service's words when it refused
        """
        url = f'{self._source.base_url}/v1/packages/{package_id}/receipt'
        body = {'zip_sha256': zip_sha256, 'note': note[:_RECEIPT_NOTE_MAX]}
        try:
            response = requests.post(url, json=body, headers=self._headers(),
                                     timeout=self._source.connection.request_timeout_s)
        except requests.exceptions.RequestException as error:
            vLog.warning(f'📦 The receipt for {package_id} got no answer ({type(error).__name__}) '
                         '— reading the package back')
            return self._settle_lost_receipt(package_id), None
        if response.status_code == 201:
            return TickReceiptOutcome.ACKNOWLEDGED, None
        if response.status_code == 200:
            return TickReceiptOutcome.ALREADY_ACKNOWLEDGED, None
        if response.status_code == 409:
            return TickReceiptOutcome.HASH_MISMATCH, _short_text(response)
        if not is_terminal_status(response.status_code):
            return self._settle_lost_receipt(package_id), None
        return TickReceiptOutcome.REFUSED, f'HTTP {response.status_code}: {_short_text(response)}'

    def _settle_lost_receipt(self, package_id: str) -> TickReceiptOutcome:
        """
        Read a package back after its receipt's answer was lost.

        Args:
            package_id: The package

        Returns:
            ACKNOWLEDGED when the service holds a receipt, UNRESOLVED otherwise
        """
        read = self.read_manifest(package_id)
        if read is not None and read[0].receipt_recorded:
            return TickReceiptOutcome.ACKNOWLEDGED
        return TickReceiptOutcome.UNRESOLVED

    def _get_json(self, path: str, what: str) -> Any:
        """
        One GET, classified for the ladder, answered as JSON.

        Args:
            path: The route
            what: What it asks for, for the operator's message

        Returns:
            The decoded answer
        """
        url = f'{self._source.base_url}{path}'
        response = send_classified(
            lambda: requests.get(url, headers=self._headers(),
                                 timeout=self._source.connection.request_timeout_s),
            endpoint=what)
        return response.json()

    def _download_once(self, package_id: str, part: Path) -> bool:
        """
        One attempt at a zip. A stream that breaks off counts as a transport fault, so the ladder
        tries again and the next attempt resumes from what the part file holds.

        Args:
            package_id: The package
            part: The part file

        Returns:
            True
        """
        have = part.stat().st_size if part.exists() else 0
        headers = self._headers()
        if have:
            headers['Range'] = f'bytes={have}-'
        url = f'{self._source.base_url}/v1/packages/{package_id}/zip'
        response = send_classified(
            lambda: requests.get(url, headers=headers, stream=True,
                                 timeout=self._source.connection.request_timeout_s),
            endpoint=f'the zip of package {package_id}')
        # A service that ignores the Range header answers 200 with the whole zip
        mode = 'ab' if have and response.status_code == 206 else 'wb'
        try:
            with open(part, mode) as sink:
                for chunk in response.iter_content(self._chunk_bytes):
                    sink.write(chunk)
        except requests.exceptions.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'{type(error).__name__} while reading the zip of package {package_id}: {error}',
                terminal=False) from error
        finally:
            response.close()
        return True

    def _headers(self) -> Dict[str, str]:
        """
        The request headers, with the token read for this request only.

        Returns:
            The headers; no Authorization header when no token is configured
        """
        token = self._read_token()
        return {'Authorization': f'Bearer {token}'} if token else {}


def _token_from_cascade(credentials_file: str) -> str:
    """
    The package service's token, read through the credential cascade.

    Args:
        credentials_file: The configured name, e.g. 'peers/collector_credentials.json'

    Returns:
        The token, empty when none is configured
    """
    path = resolve_credentials_file(credentials_file)
    if path is None:
        return ''
    with open(path, 'r') as handle:
        return json.load(handle).get('api_token', '')


def _listing_of(entry: Dict[str, Any]) -> Optional[TickPackageListing]:
    """
    One list entry, parsed. The state falls back to the receipt time where a service does not
    send one.

    Args:
        entry: The entry as served

    Returns:
        The listing, None for a state this client does not know
    """
    raw_state = entry.get('state') or ('delivered' if entry.get('received_at') else 'ready')
    try:
        state = TickPackageState(raw_state)
    except ValueError:
        vLog.warning(f"📦 Package {entry.get('package_id')} is in the state '{raw_state}', "
                     'which this import does not know — left alone')
        return None
    return TickPackageListing(
        package_id=entry['package_id'], collector=entry.get('collector', ''),
        size_bytes=int(entry['size_bytes']), sha256=entry['sha256'], state=state)


def parse_manifest(data: Dict[str, Any]) -> TickPackageManifest:
    """
    The fields of a served manifest the import checks.

    Args:
        data: The answer of the package route — the manifest, with its receipt beside it

    Returns:
        The parsed fields
    """
    manifest = data['manifest'] if isinstance(data.get('manifest'), dict) else data
    zip_block = manifest['zip']
    return TickPackageManifest(
        package_id=manifest['package_id'],
        zip_name=zip_block['name'],
        zip_sha256=zip_block['sha256'],
        zip_size_bytes=int(zip_block['size_bytes']),
        member_sha256={member['name']: member['sha256'] for member in manifest.get('members', [])},
        held_for_next=[_name_of(item) for item in manifest.get('held_for_next', [])],
        excluded=[f"{_name_of(item)}: {item.get('reason', '')}" if isinstance(item, dict)
                  else str(item) for item in manifest.get('excluded', [])],
        open_files=[_name_of(item) for item in manifest.get('open_files', [])],
        receipt_recorded=bool(data.get('receipt') or manifest.get('receipt')),
    )


def _name_of(item: Any) -> str:
    """
    A file's name from a manifest list, which names it as a string or as an object.

    Args:
        item: The list item

    Returns:
        The file name
    """
    return item.get('name', '') if isinstance(item, dict) else str(item)


def _short_text(response: requests.Response) -> str:
    """
    The beginning of a refusal's body, for the operator's message.

    Args:
        response: The answer

    Returns:
        At most 200 characters of it
    """
    return response.text[:200]
