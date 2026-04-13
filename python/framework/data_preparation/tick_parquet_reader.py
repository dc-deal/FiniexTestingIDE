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

from pathlib import Path

import pandas as pd


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
