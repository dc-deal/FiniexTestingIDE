"""
FiniexTestingIDE - Tick Zip Inbox
A zip of tick exports in the import inbox (#120): read its members, unpack them for the
importer, and move the zip unchanged out of the inbox once its members went through the import.

A zip is only the packaging: its members go through the import exactly as loose exports do,
the duplicate check included, and their unpacked copies are removed afterwards. The zip is the
raw source of truth they arrived in, so nothing in it is rewritten — it only moves: to the
finished directory when anything in it was of use, to the refused directory when nothing was or
when it cannot be imported at all (operator, 2026-11-05). Filing it into the operator's own
archive below the finished directory is not done here.
"""

import hashlib
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Registers the Deflate64 method with zipfile; two zips of the raw archive use it
import zipfile_deflate64  # noqa: F401

from python.data_management.importers.tick_inbox_conveyor import TickInboxConveyor
from python.framework.exceptions.data_quality_errors import TickZipUnpackException
from python.framework.types.import_types.tick_inbox_types import (
    InboxDisposition,
    InboxMove,
    TickImportOutcome,
    TickImportResult,
    TickZipContent,
    TickZipMember,
    TickZipSettlement,
)
from python.framework.utils.file_utils import create_directory

# A tick export's own name: SYMBOL_YYYYMMDD_HHMMSS_ticks.json. The stamp is the collector's file
# start on its own clock. A zip member has to carry it, flat — anything else refuses the zip.
TICK_FILE_NAME = re.compile(r'^(?P<symbol>.+)_(?P<stamp>\d{8}_\d{6})_ticks\.json$')

_COPY_CHUNK_BYTES = 1 << 20
_BYTES_PER_GB = 1 << 30

# The manifest a fetched package arrives with, beside its zip: <zip stem>.manifest.json
MANIFEST_SUFFIX = '.manifest.json'


class TickZipInbox:
    """
    The zips of the tick import inbox.

    A zip is refused as a whole, before anything of it is written, when a member is not a tick
    export lying flat in it, when a name occurs twice in it, or when a name is already taken by
    a loose export or by another zip of the same import. Otherwise its members are unpacked
    into a folder of their own below the unpacking directory and imported; the copy of a member
    the archive took is removed, the copy of one it refused or failed on goes to the refused
    directory with its reason — unless nothing in the zip was of use, when the zip itself goes
    there and the copies are removed.
    """

    def __init__(self, inbox_dir: Path, unpacking_dir: Path,
                 finished_dir: Optional[Path], refused_dir: Optional[Path]) -> None:
        """
        Args:
            inbox_dir: The tick import inbox
            unpacking_dir: Where members are unpacked, one folder per zip
            finished_dir: Where a zip goes once anything in it was of use, None when imported
                files stay in the inbox
            refused_dir: Where a zip or a member goes that the import refused, None when
                refused files stay in the inbox
        """
        self._inbox_dir = inbox_dir
        self._unpacking_dir = unpacking_dir
        self._conveyor = TickInboxConveyor(finished_dir=finished_dir, refused_dir=refused_dir)

    def collect_zips(self) -> List[Path]:
        """
        The zips waiting in the inbox.

        Returns:
            The *.zip files of the inbox, sorted by name
        """
        return sorted(self._inbox_dir.glob('*.zip'))

    def read_content(self, zip_path: Path, taken_names: Set[str]) -> TickZipContent:
        """
        Read a zip's members from its central directory, refusing the zip as a whole where
        its members cannot be imported as they stand.

        Args:
            zip_path: The zip
            taken_names: Export names already claimed by a loose file or an earlier zip of
                this import

        Returns:
            Its content, with the refusal when there is one
        """
        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = archive.infolist()
        except zipfile.BadZipFile as error:
            return TickZipContent(zip_path=zip_path,
                                  refusal=f'not a readable zip archive: {error}')

        members: List[TickZipMember] = []
        seen: Set[str] = set()
        for info in infos:
            name = info.filename
            if '/' in name or '\\' in name:
                return TickZipContent(
                    zip_path=zip_path,
                    refusal=f"its members must lie flat in it, '{name}' does not")
            if not TICK_FILE_NAME.match(name):
                return TickZipContent(
                    zip_path=zip_path,
                    refusal=f"'{name}' is not a tick export (SYMBOL_YYYYMMDD_HHMMSS_ticks.json) — "
                            'only tick zips are imported this way')
            if name in seen:
                return TickZipContent(zip_path=zip_path,
                                      refusal=f"'{name}' occurs twice in it")
            if name in taken_names:
                return TickZipContent(
                    zip_path=zip_path,
                    refusal=f"'{name}' also lies loose in the inbox or in another zip of "
                            'this import')
            seen.add(name)
            members.append(TickZipMember(zip_path=zip_path, name=name,
                                         size_bytes=info.file_size))
        if not members:
            return TickZipContent(zip_path=zip_path, refusal='it holds no tick export')
        return TickZipContent(zip_path=zip_path,
                              members=sorted(members, key=lambda member: member.name))

    def read_member_head(self, member: TickZipMember, byte_count: int) -> bytes:
        """
        The first bytes of a member, unpacked — enough for the metadata block at its head.

        Args:
            member: The member
            byte_count: How many bytes to read

        Returns:
            The bytes, empty when the member cannot be read
        """
        try:
            with zipfile.ZipFile(member.zip_path) as archive:
                with archive.open(member.name) as stream:
                    return stream.read(byte_count)
        except (zipfile.BadZipFile, OSError, NotImplementedError, ValueError):
            return b''

    def get_unpacking_folder(self, zip_path: Path) -> Path:
        """
        The folder a zip's members are unpacked into.

        Args:
            zip_path: The zip

        Returns:
            Its folder below the unpacking directory
        """
        return self._unpacking_dir / zip_path.stem

    def clear_unpacking_folder(self, zip_path: Path) -> None:
        """
        Remove what an earlier import left in a zip's unpacking folder. It only ever holds
        copies of that zip's members, so nothing is lost.

        Args:
            zip_path: The zip

        Returns:
            None
        """
        folder = self.get_unpacking_folder(zip_path)
        if folder.exists():
            shutil.rmtree(folder)
        self._remove_empty_unpacking_dir()

    def unpack_member(self, member: TickZipMember) -> Tuple[Path, str]:
        """
        Unpack one member into its zip's unpacking folder, hashing it on the way. zipfile checks
        the member's CRC once the stream has ended, so a damaged member fails instead of arriving
        short — as a TickZipUnpackException, like a member whose compression cannot be read. A
        file system that refuses the copy is not the zip's fault and stays an OSError.

        Args:
            member: The member

        Returns:
            (the unpacked file, its SHA-256)
        """
        folder = create_directory(self.get_unpacking_folder(member.zip_path),
                                  holds='the members of a zip during their import')
        target = folder / member.name
        digest = hashlib.sha256()
        try:
            with zipfile.ZipFile(member.zip_path) as archive:
                with archive.open(member.name) as source, open(target, 'wb') as sink:
                    for chunk in iter(lambda: source.read(_COPY_CHUNK_BYTES), b''):
                        digest.update(chunk)
                        sink.write(chunk)
        except (zipfile.BadZipFile, NotImplementedError, ValueError) as error:
            raise TickZipUnpackException(
                f"'{member.name}' could not be unpacked: {error}") from error
        return target, digest.hexdigest()

    def get_disk_shortage(self, content: TickZipContent) -> Optional[str]:
        """
        Whether a zip's members fit on the disk the unpacking directory lies on.

        Args:
            content: The zip's content

        Returns:
            Why they do not fit, None when they do
        """
        needed = sum(member.size_bytes for member in content.members)
        existing = self._unpacking_dir
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        free = shutil.disk_usage(existing).free
        if needed <= free:
            return None
        return (f'its files need {needed / _BYTES_PER_GB:.1f} GB unpacked and the disk of '
                f'{self._unpacking_dir} has {free / _BYTES_PER_GB:.1f} GB free')

    def settle(self, zip_path: Path,
               results: Dict[str, TickImportResult]) -> TickZipSettlement:
        """
        Move a zip out of the inbox once its members went through the import. When anything in
        it was of use — a member written now or already in the archive — the zip goes to the
        finished directory, and the copy of each member refused or failed on goes to the refused
        directory with its reason. When nothing was of use, the zip itself goes to the refused
        directory with every member's reason, and the copies are removed: the zip holds them.

        Args:
            zip_path: The zip
            results: Each member's result by name

        Returns:
            The settlement
        """
        folder = self.get_unpacking_folder(zip_path)
        if any(result.outcome.is_taken() for result in results.values()):
            member_moves: Dict[str, InboxMove] = {}
            for name, result in results.items():
                copy = folder / name
                if result.outcome.is_taken() or not copy.exists():
                    continue
                member_moves[name] = self._conveyor.to_refused(
                    copy, f'{result.reason or result.outcome.value} — unpacked from '
                          f'{zip_path.name}, which holds the original')
            self._remove_empty_unpacking_folder(zip_path)
            move = self._conveyor.to_finished(zip_path)
            self._conveyor.move_beside(manifest_companion(zip_path), move)
            return TickZipSettlement(zip_path=zip_path, results=results, move=move,
                                     reason=move.detail, member_moves=member_moves)

        reason = _why_nothing_was_of_use(results)
        move = self._conveyor.to_refused(zip_path, reason)
        if move.disposition is not InboxDisposition.KEPT_IN_INBOX:
            for name in results:
                (folder / name).unlink(missing_ok=True)
            self._remove_empty_unpacking_folder(zip_path)
        self._conveyor.move_beside(manifest_companion(zip_path), move)
        return TickZipSettlement(zip_path=zip_path, results=results, move=move,
                                 reason=reason if move.detail is None else move.detail)

    def refuse(self, content: TickZipContent) -> TickZipSettlement:
        """
        Move a zip refused as a whole to the refused directory, nothing of it written.

        Args:
            content: The zip's content, with its refusal

        Returns:
            The settlement
        """
        self.clear_unpacking_folder(content.zip_path)
        move = self._conveyor.to_refused(content.zip_path, content.refusal)
        self._conveyor.move_beside(manifest_companion(content.zip_path), move)
        return TickZipSettlement(
            zip_path=content.zip_path, results={}, move=move,
            reason=content.refusal if move.detail is None else move.detail)

    def keep(self, zip_path: Path, reason: str) -> TickZipSettlement:
        """
        Leave a zip in the inbox for the next import: what stopped it lay outside the zip —
        a disk too small for its files, a file system that refused the unpacking.

        Args:
            zip_path: The zip
            reason: What stopped it

        Returns:
            The settlement
        """
        self.clear_unpacking_folder(zip_path)
        return TickZipSettlement(
            zip_path=zip_path, results={},
            move=InboxMove(disposition=InboxDisposition.KEPT_IN_INBOX, detail=reason),
            reason=reason)

    def _remove_empty_unpacking_folder(self, zip_path: Path) -> None:
        """
        Remove a zip's unpacking folder once its last member's copy is gone.

        Args:
            zip_path: The zip

        Returns:
            None
        """
        folder = self.get_unpacking_folder(zip_path)
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
        self._remove_empty_unpacking_dir()

    def _remove_empty_unpacking_dir(self) -> None:
        """
        Remove the unpacking directory once no zip's folder is left in it. It exists only while a
        zip is unpacked: left behind empty, it reads on disk as a directory of data with no store,
        and unpack_member creates it again for the next zip.

        Returns:
            None
        """
        if self._unpacking_dir.exists() and not any(self._unpacking_dir.iterdir()):
            self._unpacking_dir.rmdir()


def manifest_companion(zip_path: Path) -> Path:
    """
    The manifest that lies beside a zip when the zip came as a package. It travels with the zip,
    to wherever the zip goes.

    Args:
        zip_path: The zip

    Returns:
        The manifest's path, which need not exist
    """
    return zip_path.with_name(f'{zip_path.stem}{MANIFEST_SUFFIX}')


def _why_nothing_was_of_use(results: Dict[str, TickImportResult]) -> str:
    """
    Why a zip goes to the refused directory: none of its members got into the archive.

    Args:
        results: Its members' results, none of them taken

    Returns:
        The reason, with each member's own
    """
    counts: Dict[TickImportOutcome, int] = {}
    for result in results.values():
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    parts = [f'{count} {outcome.value}'
             for outcome, count in sorted(counts.items(), key=lambda item: item[0].value)]
    members = ' · '.join(f'{name}: {result.reason or result.outcome.value}'
                         for name, result in sorted(results.items()))
    return f'none of its files got into the archive ({" · ".join(parts)}) — {members}'
