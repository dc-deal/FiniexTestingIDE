"""
FiniexTestingIDE - Host Identity Manager (#551)

The installation's identity — the `host` every run header states. Minted once, on the first start
that asks for it, and read back unchanged ever after.

These rules decide everything here, and they are the reason this is not a hostname lookup:
- a MISSING file is minted and the mint is logged loudly, naming the file;
- a file that is PRESENT but cannot be trusted refuses the start and is never re-minted, because a
  silent re-mint is an identity change nobody notices;
- under config isolation the declared test identity is stated and nothing is read or written, so
  no test can mint an installation's identity or depend on the operator's one.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

from pydantic import ValidationError

from python.framework.exceptions.host_identity_errors import HostIdentityError
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.host_identity_config_types import (
    HOST_ID_ALPHABET,
    HOST_ID_PREFIX,
    HOST_ID_RANDOM_LENGTH,
    HOST_IDENTITY_FILE,
    TEST_HOST_ID,
    HostIdentityRecord,
)
from python.framework.utils.config_merge_utils import is_config_isolation_active

_HOST_IDENTITY_PATH = HOST_IDENTITY_FILE


class HostIdentityManager:
    """
    Mints and reads the installation's identity.

    Globally available (§28): instantiate where needed, no injection. The answer is cached per
    process and per file, so a run asking at every entry point pays for one read.

    The mint publishes first-writer-wins: the record is written to a temporary file and then
    hard-linked to its final name, which fails when another process published first — and that
    process's identity is then the installation's. A plain rename would let the later writer
    replace an identity the earlier one had already stated in a run header. Where the filesystem
    offers no hard links, the mint falls back to that rename, which is atomic and gives up only
    the first-writer guarantee.
    """

    _cache: Dict[str, str] = {}
    _lock = Lock()

    def __init__(self, identity_path: Optional[str] = None):
        """
        The path is resolved from the module constant at CALL time rather than bound as a
        signature default, so a test can point the manager at a temporary file.

        Args:
            identity_path: The identity file; `user_configs/host_identity.json` when omitted
        """
        self._path = Path(identity_path or _HOST_IDENTITY_PATH)

    def get_host_id(self) -> str:
        """
        The installation's identity, minted on the first call that finds no file.

        Returns:
            The minted id (`h_` plus six characters), or the declared test id under isolation
        """
        if is_config_isolation_active():
            return TEST_HOST_ID

        key = os.path.abspath(self._path)
        cached = HostIdentityManager._cache.get(key)
        if cached is not None:
            return cached

        with HostIdentityManager._lock:
            if key not in HostIdentityManager._cache:
                HostIdentityManager._cache[key] = self._read_or_mint()
            return HostIdentityManager._cache[key]

    @classmethod
    def clear_cache(cls) -> None:
        """Drop the cached identities so the next call reads the file again."""
        with cls._lock:
            cls._cache.clear()

    def _read_or_mint(self) -> str:
        """
        Read the identity file, or mint it when there is none.

        Read directly rather than after an existence check: one file operation instead of two on
        the bridged mount (§42), and no window in which the answer to the check goes stale.

        Returns:
            The identity the file holds, or the one just minted
        """
        try:
            text = self._path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return self._mint()
        except (OSError, UnicodeDecodeError) as error:
            raise self._refusal(f'it cannot be read ({error})')
        return self._parse(text).host_id

    def _parse(self, text: str) -> HostIdentityRecord:
        """
        Validate the file's content against the minted shape.

        Args:
            text: The file's content

        Returns:
            The validated record
        """
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise self._refusal(f'it is not valid JSON ({error})')
        try:
            return HostIdentityRecord.model_validate(raw)
        except ValidationError as error:
            raise self._refusal(f'it does not carry the minted shape\n{error}')

    def _mint(self) -> str:
        """
        Mint a new identity and publish it, unless another process published one first.

        Returns:
            The identity now in the file — this process's, or the one that won the race
        """
        random_tail = ''.join(
            secrets.choice(HOST_ID_ALPHABET) for _ in range(HOST_ID_RANDOM_LENGTH))
        record = HostIdentityRecord(
            host_id=HOST_ID_PREFIX + random_tail,
            minted_at=datetime.now(timezone.utc).replace(microsecond=0),
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(
            f'.{self._path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as handle:
                handle.write(record.model_dump_json(indent=2) + '\n')
            published = self._publish(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not published:
            return self._read_or_mint()

        get_global_logger().warning(
            f'🆔 Minted a new host identity {record.host_id} → {self._path}\n'
            f'   Every run from this installation states it. Keep the file: a broken one '
            'refuses the start, and it is never re-minted automatically.'
        )
        return record.host_id

    def _publish(self, tmp_path: Path) -> bool:
        """
        Give the written record its final name, first writer wins.

        Args:
            tmp_path: The fully written temporary record

        Returns:
            True when this record became the file, False when another process's already was
        """
        try:
            os.link(tmp_path, self._path)
        except FileExistsError:
            return False
        except OSError:
            os.replace(tmp_path, self._path)
        return True

    def _refusal(self, reason: str) -> HostIdentityError:
        """
        The error refusing a present but untrustworthy identity file.

        Args:
            reason: What is wrong with the file

        Returns:
            The error to raise, naming the file and the two ways forward
        """
        return HostIdentityError(
            f'Host identity file {self._path} is present but unusable: {reason}\n'
            'It is never re-minted automatically — a new identity would make every run from '
            'here on claim to come from a different installation.\n'
            'What to do:\n'
            '  • restore the file from a backup — the normal path\n'
            '  • or delete it deliberately; the next start mints a new identity and logs it'
        )
