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
from typing import List, Optional

import pandas as pd
import pyarrow.parquet as pq



def read_tick_parquet(path: Path, columns: Optional[List[str]] = None) -> pd.DataFrame:
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
        columns: Raw columns to read, None for all. A column the file does not carry is
            skipped; `bid` and `ask` are needed for the price. A reader holding a symbol's
            whole history in one frame reads only what it uses — the two string columns
            alone are a quarter of a frame.

    Returns:
        DataFrame with normalized column names (volume guaranteed present)
    """
    if columns is None:
        df = pd.read_parquet(path)
    else:
        parquet_file = pq.ParquetFile(path)
        present = [column for column in columns if column in parquet_file.schema_arrow.names]
        df = parquet_file.read(columns=present).to_pandas()

    # A quote-driven venue reports no traded volume, so its rows carry 0.0 here — an absence,
    # not a measurement. It cannot be misread downstream: the one worker that reads volume
    # (OBV) declares volume as its activity metric, and a scenario on a market whose activity
    # metric is not volume is refused before the run.
    if 'real_volume' in df.columns and 'volume' not in df.columns:
        df = df.rename(columns={'real_volume': 'volume'})
    elif 'real_volume' not in df.columns and 'volume' not in df.columns:
        df['volume'] = 0.0

    # A file without an arrival time stores no `collected_msc` column (the deficiency
    # `no_arrival_time` says so in the index). The tick loop's own convention for "not
    # recorded" is 0 — it then times on the event clock — so the column is added HERE, once,
    # and a window spanning files with and without one concatenates without a gap. A
    # projected read that did not ask for it gets none.
    if 'collected_msc' not in df.columns and (columns is None or 'collected_msc' in columns):
        df['collected_msc'] = 0

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


