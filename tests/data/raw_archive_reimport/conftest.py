"""
FiniexTestingIDE - Raw Archive Re-import Test Fixtures

Builds a raw archive in a temp tree: zips of collector-shaped JSON members, loose files beside
them, the importer's inbox, parquets carrying the importer's footer stamps, and the tick index
frame describing those parquets. Nothing is committed as a binary fixture; every zip and parquet
is written by the test that needs it.

`ArchiveTree.simulate_import` stands in for `data_index_cli.py import --override`: it rewrites
the parquet of each inbox file, updates its index row and moves the file to the finished
directory — or, on request, refuses a file (left in the inbox, old parquet kept) or moves it
without rewriting its parquet.
"""

import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from python.experiments.raw_archive_reimport.raw_archive_catalog import RawArchiveCatalog
from python.experiments.raw_archive_reimport.raw_archive_reimport import (
    MANIFEST_FILE_NAME,
    RawArchiveReimport,
)

KNOWN_BROKER_TYPES = ['mt5', 'kraken_spot']

# When the parquets of the archive were first written — long before any test extracts a file.
OLD_PROCESSED_AT = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)

# The US winter of 2025/26, the window #562 re-imports.
WINTER_START = datetime(2025, 11, 2, tzinfo=timezone.utc)
WINTER_END = datetime(2026, 3, 8, 23, 59, 59, 999999, tzinfo=timezone.utc)


def member_json(symbol: str, broker_type: Optional[str] = None,
                header_key: str = 'broker_type', tick_count: int = 3,
                marker: str = '') -> bytes:
    """
    A raw tick file shaped like the collector's: the metadata block first, then the ticks.

    Args:
        symbol: Symbol in the header
        broker_type: Broker type the header declares; None declares none
        header_key: 'broker_type', or the older 'data_collector'
        tick_count: Ticks in the file
        marker: Extra text in the metadata, to make two files of one name differ

    Returns:
        The file's bytes
    """
    metadata: Dict[str, Any] = {'symbol': symbol, 'data_format_version': '1.5.0'}
    if broker_type is not None:
        metadata[header_key] = broker_type
    if marker:
        metadata['note'] = marker
    ticks = [{'timestamp': f'2026.01.15 04:02:{index:02d}', 'time_msc': 1768449727000 + index,
              'collected_msc': 1768438927000 + index, 'bid': 1.1, 'ask': 1.1, 'last': 1.1}
             for index in range(tick_count)]
    payload = {'metadata': metadata, 'ticks': ticks, 'summary': {'total_ticks': tick_count}}
    return json.dumps(payload, indent=2).encode('utf-8')


class ArchiveTree:
    """A raw archive, an inbox, a finished directory and an index, all under one temp root."""

    def __init__(self, root: Path) -> None:
        """
        Args:
            root: The temp root
        """
        self.root = root
        self.finished_dir = root / 'finished'
        self.archives_dir = self.finished_dir / 'Archives'
        self.raw_dir = root / 'raw'
        self.processed_dir = root / 'processed'
        self.manifest_path = self.raw_dir / MANIFEST_FILE_NAME
        for directory in (self.archives_dir, self.raw_dir, self.processed_dir):
            directory.mkdir(parents=True)
        self._rows: Dict[str, Dict[str, Any]] = {}

    def write_zip(self, zip_name: str, members: Dict[str, bytes],
                  compression: int = zipfile.ZIP_DEFLATED) -> Path:
        """
        Write a zip directly under the archives directory.

        Args:
            zip_name: The zip's file name
            members: Entry name -> content; an entry may repeat a name of another zip
            compression: zipfile compression constant

        Returns:
            The zip's path
        """
        path = self.archives_dir / zip_name
        with zipfile.ZipFile(path, 'w', compression=compression) as archive:
            for entry_name, content in members.items():
                archive.writestr(entry_name, content)
        return path

    def write_loose(self, name: str, content: bytes) -> Path:
        """
        Write a loose file into the finished directory.

        Args:
            name: File name
            content: File content

        Returns:
            The file's path
        """
        path = self.finished_dir / name
        path.write_bytes(content)
        return path

    def index_member(self, name: str, broker_type: str, symbol: str, start: datetime,
                     end: datetime, tick_count: int = 3,
                     processed_at: datetime = OLD_PROCESSED_AT) -> Path:
        """
        Give a member a parquet with the importer's footer stamps and a tick index row.

        Args:
            name: The member's name, stamped as source_file
            broker_type: Broker type the row is filed under
            symbol: Symbol of the row
            start: First tick, UTC
            end: Last tick, UTC
            tick_count: Ticks in the parquet
            processed_at: The footer's processed_at

        Returns:
            The parquet's path
        """
        stem = name[:-len('_ticks.json')]
        parquet = self.processed_dir / broker_type / 'ticks' / symbol / f'{stem}.parquet'
        self._write_parquet(parquet, name, processed_at, tick_count)
        self._rows[name] = {
            'broker_type': broker_type, 'symbol': symbol, 'file': parquet.name,
            'path': str(parquet), 'start_time': start, 'end_time': end,
            'tick_count': tick_count, 'source_file': name,
        }
        return parquet

    def tick_index(self) -> pd.DataFrame:
        """
        The tick index as the real one stores it: times naive, read as UTC.

        Returns:
            The frame
        """
        columns = ['broker_type', 'symbol', 'file', 'path', 'start_time', 'end_time',
                   'tick_count', 'source_file']
        frame = pd.DataFrame(list(self._rows.values()), columns=columns)
        for column in ('start_time', 'end_time'):
            frame[column] = pd.to_datetime(frame[column], utc=True).dt.tz_localize(None)
        return frame

    def build_tool(self, known_broker_types: Optional[List[str]] = None) -> RawArchiveReimport:
        """
        A fresh tool over the archive and the CURRENT index, as each command line run builds one.

        Args:
            known_broker_types: Configured broker types; the default pair when None

        Returns:
            The tool
        """
        return RawArchiveReimport(
            catalog=self.build_catalog(known_broker_types), raw_dir=self.raw_dir,
            manifest_path=self.manifest_path)

    def build_catalog(self, known_broker_types: Optional[List[str]] = None) -> RawArchiveCatalog:
        """
        A fresh catalog over the archive and the current index.

        Args:
            known_broker_types: Configured broker types; the default pair when None

        Returns:
            The catalog
        """
        return RawArchiveCatalog(
            archives_dir=self.archives_dir, finished_dir=self.finished_dir,
            tick_index=self.tick_index(),
            known_broker_types=known_broker_types or KNOWN_BROKER_TYPES)

    def simulate_import(self, shift: timedelta = timedelta(0), refused: Iterable[str] = (),
                        not_rewritten: Iterable[str] = ()) -> None:
        """
        Stand in for the import with --override over every tick file in the inbox.

        Args:
            shift: How far each rewritten file's start and end move
            refused: Names the importer refuses: the file stays in the inbox and its old
                parquet and index row stay as they were
            not_rewritten: Names moved to the finished directory while their parquet keeps
                its old processed_at
        """
        refused_names = set(refused)
        stale_names = set(not_rewritten)
        for source in sorted(self.raw_dir.glob('*_ticks.json')):
            name = source.name
            if name in refused_names:
                continue
            if name not in stale_names:
                row = self._rows[name]
                self._write_parquet(Path(row['path']), name, datetime.now(timezone.utc),
                                    row['tick_count'])
                row['start_time'] = row['start_time'] + shift
                row['end_time'] = row['end_time'] + shift
            os.replace(source, self.finished_dir / name)

    @staticmethod
    def _write_parquet(path: Path, source_file: str, processed_at: datetime,
                       tick_count: int) -> None:
        """
        Write a small parquet with the two footer stamps the tool reads.

        Args:
            path: Where to write it
            source_file: The footer's source_file
            processed_at: The footer's processed_at
            tick_count: Rows to write
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table({'bid': [1.1] * tick_count})
        table = table.replace_schema_metadata({
            'source_file': source_file,
            'processed_at': processed_at.isoformat(),
        })
        pq.write_table(table, path)


# The standard archive: two zips, one of them named after a kraken_spot member while most of
# what it holds is mt5. Name -> (broker type, symbol, first tick, last tick).
STANDARD_MEMBERS: Dict[str, Dict[str, Any]] = {
    'All_ticks_v3_restauration_ticks.zip': {
        'EURUSD_20260115_040207_ticks.json': ('mt5', 'EURUSD', datetime(2026, 1, 15, 1, 2, 7),
                                              datetime(2026, 1, 16, 10, 0, 0)),
        'EURUSD_20260601_120000_ticks.json': ('mt5', 'EURUSD', datetime(2026, 6, 1, 9, 0, 0),
                                              datetime(2026, 6, 2, 9, 0, 0)),
        'GBPUSD_20260120_000000_ticks.json': ('mt5', 'GBPUSD', datetime(2026, 1, 19, 21, 0, 0),
                                              datetime(2026, 1, 20, 21, 0, 0)),
        'BTCUSD_20260115_000000_ticks.json': ('kraken_spot', 'BTCUSD',
                                              datetime(2026, 1, 15, 0, 0, 0),
                                              datetime(2026, 1, 16, 0, 0, 0)),
    },
    'XRPUSD_20260911_134741_ticks.zip': {
        'XRPUSD_20260911_134741_ticks.json': ('kraken_spot', 'XRPUSD',
                                              datetime(2026, 9, 11, 13, 47, 41),
                                              datetime(2026, 9, 12, 13, 47, 0)),
        'USDJPY_20260110_220000_ticks.json': ('mt5', 'USDJPY', datetime(2026, 1, 10, 19, 0, 0),
                                              datetime(2026, 1, 11, 19, 0, 0)),
        'USDJPY_20260910_100000_ticks.json': ('mt5', 'USDJPY', datetime(2026, 9, 10, 7, 0, 0),
                                              datetime(2026, 9, 11, 7, 0, 0)),
    },
}

# The mt5 members of the standard archive that fall in the US winter.
WINTER_MT5 = ['EURUSD_20260115_040207_ticks.json', 'GBPUSD_20260120_000000_ticks.json',
              'USDJPY_20260110_220000_ticks.json']
ALL_MT5 = WINTER_MT5 + ['EURUSD_20260601_120000_ticks.json',
                        'USDJPY_20260910_100000_ticks.json']


def build_standard_archive(tree: ArchiveTree) -> None:
    """
    Write the standard archive and index every member of it, as a previous import left them.

    Args:
        tree: The archive tree to write into
    """
    for zip_name, members in STANDARD_MEMBERS.items():
        tree.write_zip(zip_name, {
            name: member_json(symbol, broker_type)
            for name, (broker_type, symbol, _, _) in members.items()})
        for name, (broker_type, symbol, start, end) in members.items():
            tree.index_member(name, broker_type, symbol, start.replace(tzinfo=timezone.utc),
                              end.replace(tzinfo=timezone.utc))


@pytest.fixture
def tree(tmp_path) -> ArchiveTree:
    """An empty archive tree under the test's temp directory."""
    return ArchiveTree(tmp_path)
