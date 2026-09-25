"""
FiniexTestingIDE - Run Patch Store (#551)

The uncommitted code a run ran, kept so the run can still be tied — and restored — to it.

A run header names each repository's commit. On a dirty tree that commit is not what ran: the
working tree differs from it, and a commit hash alone then points at code that never executed.
This store keeps the patch separating the tree from its commit, so applying it on top of the
recorded commit reproduces the code.

**Keyed by the SHA256 of the patch BYTES — not by the header's `diff_hash`.** The two digests
differ on purpose. `diff_hash` is taken over the CONTENT of the changed paths, so the same delta
has the same identity under any git configuration or version; a patch is one rendering of that
delta, and its key is the hash of exactly the bytes this store holds. The header's `patch_ref`
names the entry, and the entry's file name is its key.

**Content-addressed, and therefore immutable.** The key IS the content: equal patches are one
file however many runs ran them, and a second put of the same patch writes nothing. A put
VERIFIES the hash before it writes, and a read verifies it again before it serves — an entry
whose bytes differ from its name would tie a run to code it never ran.

**No index.** A patch is opened by the key its header's `patch_ref` already names — a lookup by
identity, never a search, which is the case §44 exempts.

**No retention policy here.** How long a patch has to outlive the runs that name it is the
lifetime question #535 owns; until it is answered, nothing in this store deletes anything.
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from python.framework.exceptions.run_patch_errors import (
    RunPatchCorruptError,
    RunPatchHashMismatchError,
)
from python.framework.logging.bootstrap_logger import get_global_logger

# One entry per distinct patch, named after the hash of its bytes.
PATCH_SUFFIX = '.patch'

# A SHA256 hex digest — the only shape a key can take. Checked on read because the key becomes
# a file name, and a key that is not a digest could name a file outside the store.
_PATCH_HASH_PATTERN = re.compile(r'[0-9a-f]{64}')


class RunPatchStore:
    """
    The patches of dirty trees that runs ran from, keyed by the SHA256 of their bytes.

    Args:
        root: The `run_patches` directory
    """

    def __init__(self, root: Path):
        self._root = Path(root)

    def put(self, patch_hash: str, patch: bytes) -> str:
        """
        Keep one patch under its hash, and answer where it lies.

        Shaped as the `PatchSink` the code identity builder takes, so a bound `put` is passed as
        it is. Idempotent: an entry that already holds these bytes is left untouched.

        Args:
            patch_hash: The SHA256 hex digest of the patch bytes
            patch: The patch bytes

        Returns:
            The entry's reference — the configured root plus the file name, so under the
            default configuration `run_patches/<patch_hash>.patch`
        """
        actual = hashlib.sha256(patch).hexdigest()
        if actual != patch_hash:
            raise RunPatchHashMismatchError(patch_hash, actual)

        target = self._path_of(patch_hash)
        existing = self._read(target)
        if existing == patch:
            return self._ref_of(target)
        if existing is not None:
            # The name fixes the content, so a present file with other bytes is damage rather
            # than an older version — and the verified bytes in hand are the only right answer.
            # Restoring them is repair, not a change to an immutable record.
            get_global_logger().warning(
                f'Run patch {target} did not hash to its name — rewritten from verified bytes')
        self._write_atomic(target, patch)
        return self._ref_of(target)

    def get(self, patch_hash: str) -> Optional[bytes]:
        """
        One patch by its hash, verified before it is served.

        Args:
            patch_hash: The SHA256 hex digest of the patch bytes — the file name the run
                header's `patch_ref` ends in, never its `diff_hash`

        Returns:
            The patch bytes, or None when the store holds no patch under this hash
        """
        if not _PATCH_HASH_PATTERN.fullmatch(patch_hash):
            raise ValueError(
                f"'{patch_hash}' is not a run patch key — a key is the SHA256 hex digest of the "
                f"patch bytes, the file name the run header's patch_ref ends in")
        path = self._path_of(patch_hash)
        patch = self._read(path)
        if patch is None:
            return None
        actual = hashlib.sha256(patch).hexdigest()
        if actual != patch_hash:
            raise RunPatchCorruptError(patch_hash, path, actual)
        return patch

    def _path_of(self, patch_hash: str) -> Path:
        """
        Where one hash's entry belongs.

        Args:
            patch_hash: The SHA256 hex digest of the patch bytes

        Returns:
            The path, whether or not it exists yet
        """
        return self._root / f'{patch_hash}{PATCH_SUFFIX}'

    @staticmethod
    def _ref_of(path: Path) -> str:
        """
        The reference a run header carries for an entry.

        Args:
            path: The entry's path

        Returns:
            The path in forward-slash form, as configured — relative under the default root
        """
        return path.as_posix()

    @staticmethod
    def _read(path: Path) -> Optional[bytes]:
        """
        An entry's bytes, when it exists.

        Args:
            path: The entry's path

        Returns:
            Its bytes, or None when there is no such file
        """
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _write_atomic(self, target: Path, patch: bytes) -> None:
        """
        Write one entry through a temporary file and a rename.

        The temporary name is unique per writer: two runs starting from the same tree at once
        write the same bytes to the same entry, and a shared temporary file would let one of
        them rename the other's half-written file into place.

        Args:
            target: The entry's path
            patch: The verified patch bytes
        """
        self._root.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=self._root, prefix=f'{target.stem}.', suffix='.tmp')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(patch)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
