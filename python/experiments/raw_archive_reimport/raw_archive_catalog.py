"""
The raw tick archive as a list of members: every tick file inside a zip directly under the
archives directory, plus every loose tick file in the finished directory that no zip holds.
"""

import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd

from python.experiments.raw_archive_reimport.raw_archive_reimport_types import (
    ArchiveMember,
    BrokerSource,
    MemberOrigin,
    ReimportScope,
    TickIndexRow,
)
from python.framework.utils.time_utils import ensure_utc_aware

# A raw tick file's own name: SYMBOL_YYYYMMDD_HHMMSS_ticks.json. The stamp is the collector's
# file start on its own clock, which is why it is only a fallback for the window.
_MEMBER_NAME = re.compile(r'^(?P<symbol>.+)_(?P<stamp>\d{8}_\d{6})_ticks\.json$')
_NAME_STAMP_FORMAT = '%Y%m%d_%H%M%S'

# A zip whose name says it holds signals is never read: the signal archives are a different
# store with a different importer.
_SIGNAL_ARCHIVE_MARKER = 'signal'

# How much of a member is read to find its broker type. The metadata block opens the file and
# ended before byte 1,400 on the members read on 2026-10-05; the rest leaves room for blocks a
# newer collector adds.
_HEADER_READ_BYTES = 16_384
_TICKS_KEY = '"ticks"'
_BROKER_TYPE_KEY = re.compile(r'"broker_type"\s*:\s*"([^"\\]*)"')
_LEGACY_BROKER_KEY = re.compile(r'"data_collector"\s*:\s*"([^"\\]*)"')

_HASH_CHUNK_BYTES = 1 << 20

# The tick index columns this tool reads.
TICK_INDEX_COLUMNS = (
    'source_file', 'broker_type', 'symbol', 'path', 'start_time', 'end_time', 'tick_count')


class RawArchiveCatalog:
    """
    Lists the members of the raw archive and resolves each one's broker type.

    A member is selected by its OWN name, never by the name of the zip that holds it: a batch
    zip is named after one of its members and can hold another broker's files. The broker type
    comes from the tick index row whose source_file is the member, otherwise from the member's
    own header matched exactly against the configured broker types, otherwise it stays
    unresolved. It is never inferred from the shape of the symbol.

    Only central directories are read to list a zip; a member is opened when the index does not
    know it (its first kilobytes), or when it is extracted or compared.
    """

    def __init__(self, archives_dir: Path, finished_dir: Path, tick_index: pd.DataFrame,
                 known_broker_types: List[str]) -> None:
        """
        Prepare the catalog; the archive is read on first use.

        Args:
            archives_dir: Directory whose zips (not its subdirectories) form the archive
            finished_dir: Directory holding loose tick files the importer moved there
            tick_index: The tick index as a frame (source_file, broker_type, symbol, path,
                start_time, end_time, tick_count); an empty frame when there is none
            known_broker_types: The configured broker types a member header must name exactly
        """
        self._archives_dir = archives_dir
        self._finished_dir = finished_dir
        self._known_broker_types: Set[str] = set(known_broker_types)
        self._index_rows: List[TickIndexRow] = self._read_index_rows(tick_index)
        self._index_by_name: Dict[str, List[TickIndexRow]] = {}
        for row in self._index_rows:
            self._index_by_name.setdefault(row.source_file, []).append(row)

        self._scanned = False
        self._archives: List[Path] = []
        self._skipped_archives: List[Path] = []
        self._members: List[ArchiveMember] = []
        self._loose_copies: List[Path] = []
        self._ignored: List[str] = []
        self._open_archives: Dict[Path, zipfile.ZipFile] = {}

    # =========================================================================
    # WHAT THE ARCHIVE HOLDS
    # =========================================================================

    def get_archives_dir(self) -> Path:
        """
        The directory whose zips form the archive.

        Returns:
            The archives directory
        """
        return self._archives_dir

    def get_finished_dir(self) -> Path:
        """
        The directory the importer moves an imported file to, and where loose files lie.

        Returns:
            The finished directory
        """
        return self._finished_dir

    def get_known_broker_types(self) -> List[str]:
        """
        The configured broker types a member header has to name exactly.

        Returns:
            Broker types, sorted
        """
        return sorted(self._known_broker_types)

    def get_archives(self) -> List[Path]:
        """
        The zips read as part of the archive.

        Returns:
            Zip paths, sorted by name
        """
        self._scan()
        return list(self._archives)

    def get_skipped_archives(self) -> List[Path]:
        """
        The zips beside them that were not read because their name marks them as signals.

        Returns:
            Zip paths, sorted by name
        """
        self._scan()
        return list(self._skipped_archives)

    def get_members(self) -> List[ArchiveMember]:
        """
        Every tick file of the archive: zip members first, then loose sources.

        Returns:
            Members in archive order; a name held twice appears twice
        """
        self._scan()
        return list(self._members)

    def get_loose_copies(self) -> List[Path]:
        """
        Loose files in the finished directory whose name a zip also holds.

        Returns:
            Paths of the copies, sorted by name
        """
        self._scan()
        return list(self._loose_copies)

    def get_ignored_entries(self) -> List[str]:
        """
        Entries that are not tick files by name (directories, other files).

        Returns:
            '<zip>:<entry>' for a zip entry, the file name for a loose file
        """
        self._scan()
        return list(self._ignored)

    def get_duplicate_names(self) -> Dict[str, List[str]]:
        """
        Member names held more than once across the zips, twice in one zip included.

        Returns:
            Member name -> the names of the zips holding it, one per occurrence
        """
        containers: Dict[str, List[str]] = {}
        for member in self.get_members():
            if member.origin is MemberOrigin.ZIP:
                containers.setdefault(member.name, []).append(member.container.name)
        return {name: sorted(held_by) for name, held_by in containers.items()
                if len(held_by) > 1}

    def get_archived_members(self, name: str) -> List[ArchiveMember]:
        """
        The zip members carrying a name.

        Args:
            name: Member name

        Returns:
            Every zip member of that name; empty when no zip holds it
        """
        return [member for member in self.get_members()
                if member.origin is MemberOrigin.ZIP and member.name == name]

    # =========================================================================
    # THE TICK INDEX, AS THIS TOOL READS IT
    # =========================================================================

    def get_index_rows(self, name: str, broker_type: str) -> List[TickIndexRow]:
        """
        The tick index rows whose source_file is a name, under one broker type.

        Args:
            name: Member name
            broker_type: Broker type the rows must be filed under

        Returns:
            The rows; normally one, empty when the index does not know the file
        """
        return [row for row in self._index_by_name.get(name, [])
                if row.broker_type == broker_type]

    def get_index_rows_in_scope(self, scope: ReimportScope) -> List[TickIndexRow]:
        """
        The tick index rows of the scope's broker and symbols; the window is not applied.

        Args:
            scope: The re-import scope

        Returns:
            The rows
        """
        return [row for row in self._index_rows
                if row.broker_type == scope.broker_type
                and self._symbol_in_scope(row.symbol, scope)]

    # =========================================================================
    # SELECTION
    # =========================================================================

    def select(self, scope: ReimportScope) -> List[ArchiveMember]:
        """
        The members a re-import of this scope covers: broker, symbols and window.

        Args:
            scope: The re-import scope

        Returns:
            Selected members in archive order; an unresolved member is never among them
        """
        return [member for member in self.get_members()
                if self._in_broker_scope(member, scope) and self.is_in_window(member, scope)]

    def get_members_in_broker_scope(self, scope: ReimportScope) -> List[ArchiveMember]:
        """
        The members of the scope's broker and symbols, whatever the window — what the count
        and the name check compare with the index.

        Args:
            scope: The re-import scope

        Returns:
            Members in archive order
        """
        return [member for member in self.get_members() if self._in_broker_scope(member, scope)]

    def get_unresolved_in_scope(self, scope: ReimportScope) -> List[ArchiveMember]:
        """
        Unresolved members the scope could cover: their symbol and window match, and nobody can
        say whether their broker does.

        Args:
            scope: The re-import scope

        Returns:
            Members in archive order
        """
        return [member for member in self.get_members()
                if member.broker_source is BrokerSource.UNRESOLVED
                and self._symbol_in_scope(member.symbol, scope)
                and self.is_in_window(member, scope)]

    def is_in_window(self, member: ArchiveMember, scope: ReimportScope) -> bool:
        """
        Whether a member falls in the scope's window.

        The member's tick index row decides by overlap, its start..end as stored. A member the
        index does not know falls back to the date and time in its own name, as one instant.

        Args:
            member: The member
            scope: The re-import scope

        Returns:
            True when the scope has no window or the member overlaps it
        """
        if member.index_start is not None and member.index_end is not None:
            start, end = member.index_start, member.index_end
        else:
            start = end = member.name_time
        if scope.window_start is not None and end < scope.window_start:
            return False
        if scope.window_end is not None and start > scope.window_end:
            return False
        return True

    # =========================================================================
    # READING A MEMBER
    # =========================================================================

    def open_member(self, member: ArchiveMember) -> IO[bytes]:
        """
        Open a member as a byte stream. A zip member's checksum is verified by zipfile when the
        stream is read to its end.

        Args:
            member: The member

        Returns:
            The open stream; the caller closes it
        """
        if member.origin is MemberOrigin.LOOSE:
            return open(member.container, 'rb')
        archive = self._open_archives.get(member.container)
        if archive is None:
            archive = zipfile.ZipFile(member.container)
            self._open_archives[member.container] = archive
        return archive.open(member.entry_name)

    def prove_identical(self, loose_file: Path, member: ArchiveMember) -> bool:
        """
        Whether a loose file is byte for byte its archived member, by SHA-256 of both streams.

        Args:
            loose_file: The loose file
            member: The member it may be a copy of

        Returns:
            True when both digests are equal
        """
        with open(loose_file, 'rb') as stream:
            loose_digest = self._sha256(stream)
        with self.open_member(member) as stream:
            member_digest = self._sha256(stream)
        return loose_digest == member_digest

    def close(self) -> None:
        """Close the zips opened for reading members."""
        for archive in self._open_archives.values():
            archive.close()
        self._open_archives.clear()

    @staticmethod
    def parse_member_name(name: str) -> Optional[Tuple[str, datetime]]:
        """
        Read the symbol and the start stamp from a raw tick file's name.

        Args:
            name: File name, without directories

        Returns:
            (symbol, the stamp read as UTC), or None when the name is not a tick file's
        """
        match = _MEMBER_NAME.match(name)
        if match is None:
            return None
        try:
            stamp = datetime.strptime(match.group('stamp'), _NAME_STAMP_FORMAT)
        except ValueError:
            return None
        return match.group('symbol'), stamp.replace(tzinfo=timezone.utc)

    # =========================================================================
    # INTERNALS
    # =========================================================================

    def _scan(self) -> None:
        """Read the central directories and the loose files, once."""
        if self._scanned:
            return
        self._scanned = True

        zip_members: List[ArchiveMember] = []
        if self._archives_dir.is_dir():
            for path in sorted(self._archives_dir.glob('*.zip')):
                if not path.is_file():
                    continue
                if _SIGNAL_ARCHIVE_MARKER in path.name.lower():
                    self._skipped_archives.append(path)
                    continue
                self._archives.append(path)
                zip_members.extend(self._read_central_directory(path))

        archived_names = {member.name for member in zip_members}
        self._members = zip_members + self._read_loose_files(archived_names)

    def _read_central_directory(self, path: Path) -> List[ArchiveMember]:
        """
        List one zip's tick files from its central directory.

        Args:
            path: The zip

        Returns:
            Its members, in the zip's own order
        """
        members: List[ArchiveMember] = []
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = PurePosixPath(info.filename).name
                parsed = None if info.is_dir() else self.parse_member_name(name)
                if parsed is None:
                    self._ignored.append(f'{path.name}:{info.filename}')
                    continue
                symbol, name_time = parsed
                broker_type, broker_source = self._resolve_broker(
                    name, lambda entry=info: archive.open(entry))
                members.append(self._build_member(
                    name=name, symbol=symbol, name_time=name_time, broker_type=broker_type,
                    broker_source=broker_source, origin=MemberOrigin.ZIP, container=path,
                    entry_name=info.filename, size_bytes=info.file_size, crc32=info.CRC))
        return members

    def _read_loose_files(self, archived_names: Set[str]) -> List[ArchiveMember]:
        """
        List the loose tick files that are sources: their name is in no zip.

        A loose file whose name a zip holds is a copy and is recorded as one instead.

        Args:
            archived_names: Every member name the zips hold

        Returns:
            The loose sources, sorted by name
        """
        members: List[ArchiveMember] = []
        if not self._finished_dir.is_dir():
            return members
        for path in sorted(self._finished_dir.glob('*_ticks.json')):
            if not path.is_file():
                continue
            parsed = self.parse_member_name(path.name)
            if parsed is None:
                self._ignored.append(path.name)
                continue
            if path.name in archived_names:
                self._loose_copies.append(path)
                continue
            symbol, name_time = parsed
            broker_type, broker_source = self._resolve_broker(
                path.name, lambda source=path: open(source, 'rb'))
            members.append(self._build_member(
                name=path.name, symbol=symbol, name_time=name_time, broker_type=broker_type,
                broker_source=broker_source, origin=MemberOrigin.LOOSE, container=path,
                entry_name=path.name, size_bytes=path.stat().st_size, crc32=None))
        return members

    def _build_member(self, name: str, symbol: str, name_time: datetime,
                      broker_type: Optional[str], broker_source: BrokerSource,
                      origin: MemberOrigin, container: Path, entry_name: str,
                      size_bytes: int, crc32: Optional[int]) -> ArchiveMember:
        """
        Build a member, with the span of its tick index row when the index knows it.

        Args:
            name: Member name
            symbol: Symbol read from the name
            name_time: Stamp read from the name, as UTC
            broker_type: Resolved broker type, None when unresolved
            broker_source: What the broker type was read from
            origin: ZIP or LOOSE
            container: The zip, or the loose file
            entry_name: Path inside the zip, or the loose file's name
            size_bytes: Uncompressed size
            crc32: The zip's checksum for the member, None for a loose file

        Returns:
            The member
        """
        rows = self.get_index_rows(name, broker_type) if broker_type is not None else []
        starts = [row.start_time for row in rows if row.start_time is not None]
        ends = [row.end_time for row in rows if row.end_time is not None]
        return ArchiveMember(
            name=name, symbol=symbol, name_time=name_time, broker_type=broker_type,
            broker_source=broker_source, origin=origin, container=container,
            entry_name=entry_name, size_bytes=size_bytes, crc32=crc32,
            index_start=min(starts) if starts else None,
            index_end=max(ends) if ends else None)

    def _resolve_broker(self, name: str,
                        open_stream: Callable[[], IO[bytes]]) -> Tuple[Optional[str], BrokerSource]:
        """
        Resolve a member's broker type: the tick index first, the member's own header second.

        A name the index files under two broker types answers nothing there, and falls through
        to the header like a name the index does not know.

        Args:
            name: Member name
            open_stream: Opens the member, called only when the index cannot answer

        Returns:
            (broker type, source); (None, UNRESOLVED) when neither answers with a known type
        """
        filed_under = {row.broker_type for row in self._index_by_name.get(name, [])}
        if len(filed_under) == 1:
            return filed_under.pop(), BrokerSource.TICK_INDEX

        with open_stream() as stream:
            head = stream.read(_HEADER_READ_BYTES)
        declared = self._read_declared_broker(head)
        if declared is not None and declared in self._known_broker_types:
            return declared, BrokerSource.MEMBER_HEADER
        return None, BrokerSource.UNRESOLVED

    @staticmethod
    def _read_declared_broker(head: bytes) -> Optional[str]:
        """
        Read the broker type a member's header declares, with the importer's precedence:
        `broker_type`, and only where it is absent the older `data_collector`.

        Args:
            head: The first bytes of the member

        Returns:
            The declared value as written, or None when the header declares none
        """
        text = head.decode('utf-8', errors='replace')
        ticks_at = text.find(_TICKS_KEY)
        if ticks_at >= 0:
            text = text[:ticks_at]
        match = _BROKER_TYPE_KEY.search(text) or _LEGACY_BROKER_KEY.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _read_index_rows(tick_index: pd.DataFrame) -> List[TickIndexRow]:
        """
        Turn the tick index frame into rows, with the stored times read as UTC.

        Args:
            tick_index: The tick index frame

        Returns:
            One row per frame row; empty for an empty frame
        """
        if tick_index.empty:
            return []
        missing = [column for column in TICK_INDEX_COLUMNS if column not in tick_index.columns]
        if missing:
            raise ValueError(f'The tick index lacks the columns {missing}')

        starts = pd.to_datetime(tick_index['start_time'], utc=True)
        ends = pd.to_datetime(tick_index['end_time'], utc=True)
        rows: List[TickIndexRow] = []
        for source_file, broker_type, symbol, path, start, end, tick_count in zip(
                tick_index['source_file'], tick_index['broker_type'], tick_index['symbol'],
                tick_index['path'], starts, ends, tick_index['tick_count']):
            rows.append(TickIndexRow(
                source_file=str(source_file), broker_type=str(broker_type), symbol=str(symbol),
                parquet_path=str(path),
                start_time=None if pd.isna(start) else ensure_utc_aware(start.to_pydatetime()),
                end_time=None if pd.isna(end) else ensure_utc_aware(end.to_pydatetime()),
                tick_count=int(tick_count)))
        return rows

    @staticmethod
    def _symbol_in_scope(symbol: str, scope: ReimportScope) -> bool:
        """
        Whether a symbol is covered by the scope, by exact equality.

        Args:
            symbol: The symbol
            scope: The re-import scope

        Returns:
            True when the scope names no symbols or names this one
        """
        return not scope.symbols or symbol in scope.symbols

    def _in_broker_scope(self, member: ArchiveMember, scope: ReimportScope) -> bool:
        """
        Whether a member belongs to the scope's broker and symbols.

        Args:
            member: The member
            scope: The re-import scope

        Returns:
            True when both match; never for an unresolved member
        """
        return member.broker_type == scope.broker_type and self._symbol_in_scope(
            member.symbol, scope)

    @staticmethod
    def _sha256(stream: IO[bytes]) -> str:
        """
        Digest a stream to its end.

        Args:
            stream: The open stream

        Returns:
            The SHA-256 hex digest
        """
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
        return digest.hexdigest()
