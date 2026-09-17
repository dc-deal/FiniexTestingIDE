"""
Central Tick Parquet Reader

Single entry point for reading tick parquet files with column normalization.
Converts broker-native column names to framework convention:
- real_volume → volume (trade volume, 0.0 for forex CFDs)
- last → price (the STRATEGY's price: what traded where the venue prints trades,
  the book midpoint where it does not)

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
    - price: the traded price where the venue has one, the bid/ask midpoint where it
      does not. Derived HERE, so the rule exists once and every reader inherits it —
      the renderer needs no broker argument and no config lookup.

    All other raw columns (bid, ask, last, tick_volume, tick_flags, etc.)
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

    # A traded price of exactly zero is not a price in any market: a quote-driven venue has
    # no central place where trades happen and writes 0.0 on every row (measured: 100 % of
    # MT5 forex ticks), so those fall back to the midpoint. Where the venue does print
    # trades, this is what it printed — the same basis the venue's own charts use.
    mid = (df['bid'] + df['ask']) / 2.0
    if 'last' in df.columns:
        df['price'] = df['last'].where(df['last'] > 0.0, mid)
    else:
        df['price'] = mid

    return df


