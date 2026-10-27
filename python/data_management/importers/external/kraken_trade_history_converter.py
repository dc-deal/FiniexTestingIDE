"""
Kraken Trade History Converter — an emulated collector over Kraken's published trade record.

Turns one pair's rows of the archive into collector-shaped files at data format 1.7.0, so they go
through the SAME import as our own data: validation, origin stamp, parquet. What makes them
recognisable is stated in the file, never guessed afterwards:

- the origin block names the converter's own identity, registered as `external`;
- `external_source` names the archive parts, their checksums and the member read;
- no `collected_msc` at all — nothing was received, and the absent column is the statement.

Conversion stops at our first collected tick (floored to the minute): external data lives only
BEFORE our collection, never inside it and never in a gap of it. It is deterministic on content —
the same parts, converter version and cut give the same ticks — and it writes one file per symbol
and month, split where a month holds more ticks than one file should.

Example — Kraken's first BTCUSD trade:

    archive row  1381095256.0907948,122.00000,0.10000000,s,l,,1
    tick         timestamp 2013.10.06 21:34:16 · time_msc 1381095256090 · bid = ask = last 122.0
                 real_volume 0.1 · tick_flags SELL · trade_id 1
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from python.data_management.importers.external.kraken_trade_history_archive import (
    DATASET,
    VENDOR,
    KrakenTradeHistoryArchive,
)
from python.framework.types.import_types.external_history_types import (
    ConversionResult,
    ConvertedFile,
    ExternalFetchManifest,
)
from python.framework.utils.file_utils import create_directory

# The identity this converter states in every file it writes — registered in
# configs/data_origins.json with class `external`.
CONVERTER_IDENTITY = 'd351d787e800'
CONVERTER_NAME = 'finiex-kraken-history-converter'
CONVERTER_VERSION = '1.0.0'
DATA_FORMAT_VERSION = '1.7.0'
BROKER_TYPE = 'kraken_spot'

# The importer reads each file whole, so a file stays small enough to parse at once; a month
# holding more ticks is split.
MAX_TICKS_PER_FILE = 500_000
_CHUNK_ROWS = 1_000_000
_SIDE_FLAGS = {'b': 'BUY', 's': 'SELL'}


@dataclass
class _Pending:
    """
    Rows waiting to be written as one file.

    Args:
        month: The calendar month (UTC) the rows belong to, as year × 12 + month
        frames: The rows, in archive order
        count: How many rows are waiting
    """
    month: int = -1
    frames: List[pd.DataFrame] = field(default_factory=list)
    count: int = 0


class KrakenTradeHistoryConverter:
    """
    Converts one pair of Kraken's trade history into collector-shaped files.

    Args:
        archive: The verified fetch
        raw_dir: Where the files go — the import inbox
        max_ticks_per_file: Upper bound per written file
    """

    def __init__(self, archive: KrakenTradeHistoryArchive, raw_dir: Path,
                 max_ticks_per_file: int = MAX_TICKS_PER_FILE):
        self._archive = archive
        self._raw_dir = Path(raw_dir)
        self._max_ticks_per_file = max_ticks_per_file

    def convert(self, symbol: str, base_currency: str, quote_currency: str,
                cut_at: Optional[datetime], dry_run: bool = False) -> ConversionResult:
        """
        Convert one pair up to the cut.

        Args:
            symbol: Our symbol
            base_currency: Its base currency (broker configuration)
            quote_currency: Its quote currency (broker configuration)
            cut_at: Our first collected tick, floored to the minute; None converts everything
            dry_run: Count and name the files without writing them

        Returns:
            What was converted
        """
        manifest = self._archive.load_manifest()
        member = KrakenTradeHistoryArchive.member_name(base_currency, quote_currency)
        result = ConversionResult(symbol=symbol, member=member, cut_at=cut_at)
        cut_msc = int(cut_at.timestamp() * 1000) if cut_at else None
        source = self._external_source(manifest, member)
        pending = _Pending()

        with self._archive.open() as archive:
            for chunk in self._archive.read_rows(archive, member, _CHUNK_ROWS):
                result.rows_read += len(chunk)
                msc = np.floor(chunk['timestamp'].to_numpy() * 1000).astype('int64')
                reached_cut = cut_msc is not None and bool((msc >= cut_msc).any())
                if cut_msc is not None:
                    keep = msc < cut_msc
                    chunk, msc = chunk[keep], msc[keep]
                if len(chunk):
                    rows = self._ticks_frame(chunk, msc)
                    result.rows_converted += len(rows)
                    self._take(rows, pending, symbol, source, result, dry_run)
                # The archive is sorted by time: past the cut nothing further is ours to take
                if reached_cut:
                    break
        self._flush(pending, symbol, source, result, dry_run)
        return result

    @staticmethod
    def _ticks_frame(chunk: pd.DataFrame, msc: np.ndarray) -> pd.DataFrame:
        """
        Map archive rows to collector-shaped tick columns.

        Args:
            chunk: Archive rows
            msc: Their event time in epoch milliseconds

        Returns:
            One row per tick, in archive order
        """
        price = chunk['price'].to_numpy(dtype='float64')
        flags = chunk['type'].map(_SIDE_FLAGS)
        if flags.isna().any():
            unknown = sorted(set(chunk.loc[flags.isna(), 'type'].astype(str)))
            raise ValueError(f'Unknown trade side(s) {unknown} — expected b or s')
        return pd.DataFrame({
            'timestamp': pd.to_datetime(msc, unit='ms').strftime('%Y.%m.%d %H:%M:%S'),
            'time_msc': msc,
            'bid': price,
            'ask': price,
            'last': price,
            'tick_volume': 0,
            'real_volume': chunk['volume'].to_numpy(dtype='float64'),
            'chart_tick_volume': 1,
            'spread_points': 0,
            'spread_pct': 0.0,
            'quote_age_ms': None,
            'trade_id': chunk['trade_id'].to_numpy(dtype='int64'),
            'tick_flags': flags.to_numpy(),
            'session': '24h',
        })

    def _take(self, rows: pd.DataFrame, pending: _Pending, symbol: str, source: Dict,
              result: ConversionResult, dry_run: bool) -> None:
        """
        Add rows to the waiting file, writing it whenever its month ends or it is full.

        Args:
            rows: Tick rows in archive order
            pending: The waiting file
            symbol: Our symbol
            source: The external_source block
            result: Collects the written files
            dry_run: Count and name without writing
        """
        stamps = pd.to_datetime(rows['time_msc'].to_numpy(), unit='ms')
        months = (stamps.year * 12 + stamps.month).to_numpy()
        start = 0
        while start < len(rows):
            month = months[start]
            end = start + int(np.argmax(months[start:] != month)) if (months[start:] != month).any() else len(rows)
            if pending.count and pending.month != month:
                self._flush(pending, symbol, source, result, dry_run)
            pending.month = month
            position = start
            while position < end:
                room = self._max_ticks_per_file - pending.count
                take = min(room, end - position)
                pending.frames.append(rows.iloc[position:position + take])
                pending.count += take
                position += take
                if pending.count >= self._max_ticks_per_file:
                    self._flush(pending, symbol, source, result, dry_run)
                    pending.month = month
            start = end

    def _flush(self, pending: _Pending, symbol: str, source: Dict,
               result: ConversionResult, dry_run: bool) -> None:
        """
        Write the waiting rows as one collector-shaped file.

        Args:
            pending: The waiting file — emptied here
            symbol: Our symbol
            source: The external_source block
            result: Collects the written file
            dry_run: Name it without writing
        """
        if not pending.count:
            return
        rows = pd.concat(pending.frames, ignore_index=True)
        pending.frames, pending.count = [], 0

        first_msc = int(rows['time_msc'].iloc[0])
        last_msc = int(rows['time_msc'].iloc[-1])
        first = datetime.fromtimestamp(first_msc / 1000, timezone.utc)
        name = f'{symbol}_{first:%Y%m%d_%H%M%S}_ticks.json'
        result.files.append(ConvertedFile(
            name=name, first_tick=first,
            last_tick=datetime.fromtimestamp(last_msc / 1000, timezone.utc),
            tick_count=len(rows)))
        if dry_run:
            return

        document = {
            'metadata': {
                'symbol': symbol,
                'broker': 'Kraken',
                'server': 'kraken_trade_history',
                'broker_type': BROKER_TYPE,
                'start_time': rows['timestamp'].iloc[0],
                'start_time_unix': first_msc // 1000,
                'timeframe': 'TICK',
                'data_format_version': DATA_FORMAT_VERSION,
                'collection_purpose': 'backtesting',
                'operator': CONVERTER_NAME,
                'origin': {
                    'instance_id': CONVERTER_IDENTITY,
                    'collected_on': VENDOR,
                    'producer': CONVERTER_NAME,
                    'producer_version': CONVERTER_VERSION,
                },
                'external_source': source,
            },
            'ticks': rows.to_dict('records'),
            'summary': {'total_ticks': len(rows)},
        }
        create_directory(
            self._raw_dir, holds='the converted history files waiting for their import')
        partial = self._raw_dir / f'{name}.part'
        partial.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')
        partial.replace(self._raw_dir / name)

    @staticmethod
    def _external_source(manifest: ExternalFetchManifest, member: str) -> Dict:
        """
        The block naming the vendor material a file was made of.

        Args:
            manifest: The verified fetch
            member: The member read

        Returns:
            The external_source block
        """
        return {
            'vendor': VENDOR,
            'dataset': DATASET,
            'parts': [{'name': part.name, 'sha256': part.sha256} for part in manifest.parts],
            'member': member,
            'manifest_schema_version': str(manifest.vendor_manifest.get('schema_version', '')),
            'converter_version': CONVERTER_VERSION,
        }
