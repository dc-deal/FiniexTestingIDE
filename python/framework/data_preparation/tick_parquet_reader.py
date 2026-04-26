"""
Central Tick Parquet Reader

Single entry point for reading tick parquet files with column normalization.
Converts broker-native column names to framework convention:
- real_volume → volume (trade volume, 0.0 for forex CFDs)

All tick parquet consumers that need normalized data should use
read_tick_parquet() instead of pd.read_parquet() directly.

Exceptions (raw access intentional):
- data_inspector.py — displays raw parquet schema for debugging
- tick_index_manager.py — reads metadata/statistics only
- tick_importer.py — duplicate detection on raw data
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from python.framework.types.market_types.market_data_types import TickData


def read_tick_parquet(path: Path) -> pd.DataFrame:
    """
    Read a tick parquet file and normalize column names to framework convention.

    Normalization rules:
    - real_volume → volume (if real_volume present and volume absent)
    - If neither real_volume nor volume exists: adds volume column with 0.0

    All other raw columns (bid, ask, tick_volume, tick_flags, etc.)
    are preserved — consumers may still need them.

    Args:
        path: Path to tick parquet file

    Returns:
        DataFrame with normalized column names (volume guaranteed present)
    """
    df = pd.read_parquet(path)

    if 'real_volume' in df.columns and 'volume' not in df.columns:
        df = df.rename(columns={'real_volume': 'volume'})
    elif 'real_volume' not in df.columns and 'volume' not in df.columns:
        df['volume'] = 0.0

    return df


def load_ticks_from_parquet(path: Path, symbol: str) -> List[TickData]:
    """
    Load tick data from a parquet file and return as TickData objects.

    Reads via read_tick_parquet() (column normalization applied), sorts
    by time_msc, and converts each row to a TickData instance.

    Args:
        path: Path to tick parquet file
        symbol: Trading symbol to assign to each tick (e.g. 'BTCUSD')

    Returns:
        List of TickData sorted by time_msc ascending
    """
    if not path.exists():
        raise FileNotFoundError(f'Parquet tick data not found: {path}')

    df = read_tick_parquet(path)

    if 'time_msc' not in df.columns:
        raise ValueError(f"Parquet file missing 'time_msc' column: {path}")

    df = df.sort_values('time_msc').reset_index(drop=True)

    ticks: List[TickData] = []
    for _, row in df.iterrows():
        time_msc = int(row['time_msc'])
        ts = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)
        ticks.append(TickData(
            timestamp=ts,
            symbol=symbol,
            bid=float(row['bid']),
            ask=float(row['ask']),
            volume=float(row.get('volume', 0.0)),
            time_msc=time_msc,
            collected_msc=int(row.get('collected_msc', 0)),
        ))

    return ticks
