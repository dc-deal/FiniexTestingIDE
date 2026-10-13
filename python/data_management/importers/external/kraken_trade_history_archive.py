"""
Kraken Trade History Archive — Kraken's published time-and-sales download, kept unchanged.

One fetch folder holds the parts exactly as Kraken publishes them, Kraken's checksum list, and
this side's own manifest. The parts are byte ranges of ONE zip; they are read in place as the file
they join into, never copied together. Every check a reader of this archive needs is made here,
once: the parts against Kraken's checksums, the vendor manifest's schema, a member's header.
"""

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterator, List

import pandas as pd

from python.framework.exceptions.external_history_errors import ExternalArchiveError
from python.framework.types.import_types.external_history_types import (
    ExternalFetchManifest,
    FetchedPart,
)
from python.framework.utils.concatenated_parts_reader import ConcatenatedPartsReader

VENDOR = 'kraken'
DATASET = 'time_and_sales'
PART_GLOB = 'Kraken_Trades_Full.zip.part*'
INCREMENT_GLOB = 'Kraken_Trades_20*.zip'
CHECKSUMS_FILE = 'Trades_Full_PARTS_SHA256SUMS.txt'
MANIFEST_FILE = 'manifest.json'
FETCH_STARTED_FILE = 'fetch_started_utc.txt'
FETCH_FINISHED_FILE = 'fetch_finished_utc.txt'
VENDOR_MANIFEST_MEMBER = 'MANIFEST.json'

# The vendor manifest this reader understands (measured 2026-10-13, release r1). Schema 1 put the
# price first and named six fields: a positional parser reads a price where it expects a
# timestamp without raising, so a different schema is refused, never adapted to.
EXPECTED_SCHEMA_VERSION = 2
EXPECTED_COLUMNS = ['timestamp', 'price', 'volume', 'type', 'order_type', 'misc', 'trade_id']

# Kraken still names some assets by legacy codes in its pair names — measured 2026-10-13 from the
# archive's member list: our BTCUSD is the member XBTUSD.csv, while XBTUSDC, XBTUSDT and the rest
# are other markets and never taken instead.
_LEGACY_ASSET_CODES = {'BTC': 'XBT', 'DOGE': 'XDG'}
_HASH_BLOCK = 8 * 1024 * 1024


class KrakenTradeHistoryArchive:
    """
    One fetch of Kraken's trade history, as the external history store keeps it.

    Args:
        fetch_dir: The fetch folder (parts, Kraken's checksum list, this side's manifest)
    """

    def __init__(self, fetch_dir: Path):
        self._fetch_dir = Path(fetch_dir)

    def get_fetch_dir(self) -> Path:
        """
        The fetch folder.

        Returns:
            Its path
        """
        return self._fetch_dir

    def verify(self) -> ExternalFetchManifest:
        """
        Check every part against Kraken's published checksum and record the fetch.

        Reads every byte once (about ten minutes for 28 GB on this machine), which is why
        conversion does not repeat it: it reads the manifest this writes and checks only that
        the parts kept their size.

        Returns:
            The manifest written to the fetch folder
        """
        published = self._published_checksums()
        parts = [self._fetched_part(path, published.get(path.name, ''))
                 for path in self._part_paths()]
        wrong = [part.name for part in parts if not part.matches_published()]
        if wrong:
            raise ExternalArchiveError(
                f'{len(wrong)} part(s) differ from Kraken\'s published checksum: '
                f'{", ".join(wrong)} — download them again; nothing was recorded.')

        increments = [self._fetched_part(path, '')
                      for path in sorted(self._fetch_dir.glob(INCREMENT_GLOB))]
        with self.open() as archive:
            vendor_manifest = self._read_vendor_manifest(archive)

        manifest = ExternalFetchManifest(
            vendor=VENDOR, dataset=DATASET,
            fetch_started=self._read_text(FETCH_STARTED_FILE),
            fetch_finished=self._read_text(FETCH_FINISHED_FILE),
            parts=parts, increments=increments, vendor_manifest=vendor_manifest)
        (self._fetch_dir / MANIFEST_FILE).write_text(
            json.dumps(asdict(manifest), indent=2), encoding='utf-8')
        return manifest

    def load_manifest(self) -> ExternalFetchManifest:
        """
        Read this side's manifest, and refuse when the parts changed since it was written.

        Returns:
            The manifest
        """
        path = self._fetch_dir / MANIFEST_FILE
        if not path.exists():
            raise ExternalArchiveError(
                f'No {MANIFEST_FILE} in {self._fetch_dir} — run the verify step first; '
                f'nothing is converted from parts nobody checked.')
        raw = json.loads(path.read_text(encoding='utf-8'))
        manifest = ExternalFetchManifest(
            **{**raw, 'parts': [FetchedPart(**part) for part in raw['parts']],
               'increments': [FetchedPart(**part) for part in raw['increments']]})
        on_disk = {path.name: path.stat().st_size for path in self._part_paths()}
        recorded = {part.name: part.size for part in manifest.parts}
        if on_disk != recorded:
            raise ExternalArchiveError(
                f'The parts in {self._fetch_dir} are not the ones verified — run verify again.')
        return manifest

    def open(self) -> zipfile.ZipFile:
        """
        Open the joined archive, reading the parts in place.

        Returns:
            The zip, to be closed by the caller
        """
        reader = io.BufferedReader(ConcatenatedPartsReader(self._part_paths()),
                                   buffer_size=1024 * 1024)
        return zipfile.ZipFile(reader)

    @staticmethod
    def member_name(base_currency: str, quote_currency: str) -> str:
        """
        The archive member holding one pair: Kraken's pair name, legacy asset codes included.

        Args:
            base_currency: Base currency, as the broker configuration states it
            quote_currency: Quote currency, as the broker configuration states it

        Returns:
            The member's file name
        """
        base = _LEGACY_ASSET_CODES.get(base_currency, base_currency)
        quote = _LEGACY_ASSET_CODES.get(quote_currency, quote_currency)
        return f'{base}{quote}.csv'

    def read_rows(self, archive: zipfile.ZipFile, member: str,
                  chunk_rows: int) -> Iterator[pd.DataFrame]:
        """
        Stream one member's rows in chunks, after checking its header.

        Args:
            archive: The opened archive
            member: Exact member name
            chunk_rows: Rows per chunk

        Returns:
            Iterator over DataFrames with the expected columns
        """
        if member not in archive.namelist():
            raise ExternalArchiveError(
                f'The archive holds no member {member!r} — a pair name must match exactly, '
                f'and a near match is another market.')
        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding='utf-8', newline='') as text:
            header = next(csv.reader([text.readline()]))
            if header != EXPECTED_COLUMNS:
                raise ExternalArchiveError(
                    f'{member}: header {header} is not {EXPECTED_COLUMNS} — another schema; '
                    f'refused rather than read positionally.')
            for chunk in pd.read_csv(
                    text, header=None, names=EXPECTED_COLUMNS, chunksize=chunk_rows,
                    dtype={'timestamp': 'float64', 'price': 'float64', 'volume': 'float64',
                           'type': 'string', 'order_type': 'string', 'misc': 'string'}):
                if chunk['trade_id'].isna().any() or chunk['type'].isna().any():
                    raise ExternalArchiveError(
                        f'{member}: a row lacks a field — fewer columns than the header names, '
                        f'as a delisted pair carries; refused.')
                yield chunk

    def _part_paths(self) -> List[Path]:
        """
        The parts in their joining order.

        Returns:
            Paths sorted by part number
        """
        parts = sorted(self._fetch_dir.glob(PART_GLOB))
        if not parts:
            raise ExternalArchiveError(f'No archive parts ({PART_GLOB}) in {self._fetch_dir}')
        return parts

    def _published_checksums(self) -> Dict[str, str]:
        """
        Kraken's checksum list, by part name.

        Returns:
            Part name to SHA-256
        """
        path = self._fetch_dir / CHECKSUMS_FILE
        if not path.exists():
            raise ExternalArchiveError(
                f'No {CHECKSUMS_FILE} in {self._fetch_dir} — the parts cannot be checked.')
        checksums = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            fields = line.split()
            if len(fields) == 2:
                checksums[fields[1].lstrip('*')] = fields[0].lower()
        return checksums

    @staticmethod
    def _fetched_part(path: Path, published_sha256: str) -> FetchedPart:
        """
        Hash one part.

        Args:
            path: The part
            published_sha256: Kraken's checksum for it, empty where none is published

        Returns:
            The part's record
        """
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(_HASH_BLOCK), b''):
                digest.update(block)
        return FetchedPart(name=path.name, size=path.stat().st_size,
                           sha256=digest.hexdigest(), published_sha256=published_sha256)

    @staticmethod
    def _read_vendor_manifest(archive: zipfile.ZipFile) -> Dict:
        """
        Read Kraken's own manifest and refuse a schema this reader does not know.

        Args:
            archive: The opened archive

        Returns:
            The manifest, verbatim
        """
        manifest = json.loads(archive.read(VENDOR_MANIFEST_MEMBER))
        if manifest.get('schema_version') != EXPECTED_SCHEMA_VERSION:
            raise ExternalArchiveError(
                f'Vendor manifest schema {manifest.get("schema_version")} is not '
                f'{EXPECTED_SCHEMA_VERSION} — the column order may differ; refused.')
        if manifest.get('columns') != EXPECTED_COLUMNS or not manifest.get('has_header'):
            raise ExternalArchiveError(
                f'Vendor manifest columns {manifest.get("columns")} are not {EXPECTED_COLUMNS} '
                f'with a header row; refused.')
        return manifest

    def _read_text(self, name: str) -> str:
        """
        One small text file of the fetch folder, empty when absent.

        Args:
            name: File name

        Returns:
            Its stripped content
        """
        path = self._fetch_dir / name
        return path.read_text(encoding='utf-8').strip() if path.exists() else ''
