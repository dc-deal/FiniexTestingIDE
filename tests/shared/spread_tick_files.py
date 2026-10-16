"""
Small Kraken-shaped tick files for the spread estimate tests.

The estimate reads a file's trades — time, price and aggressor side — and, where the file recorded
a quote, its bid and ask. These helpers write such a file into a test directory and return the
tick index entry that describes it, with the fields the measurement and its cache read.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.framework.types.data_deficiency_types import DataDeficiency

# (time_msc, price, side) of one trade; side is 'BUY' or 'SELL'
Trade = Tuple[int, float, str]


def write_tick_file(directory: Path, name: str, trades: List[Trade],
                    quote: Optional[Tuple[float, float]] = None,
                    origin_class: str = 'external') -> Dict:
    """
    Write one tick file and return its tick index entry.

    Args:
        directory: Where to write
        name: File name
        trades: The trades, in time order
        quote: (bid, ask) on every row; None writes bid = ask = the price, a file without a quote
        origin_class: The entry's origin class

    Returns:
        The tick index entry
    """
    frame = pd.DataFrame({
        'time_msc': [trade[0] for trade in trades],
        'last': [trade[1] for trade in trades],
        'bid': [quote[0] if quote else trade[1] for trade in trades],
        'ask': [quote[1] if quote else trade[1] for trade in trades],
        'tick_flags': [trade[2] for trade in trades],
    })
    path = directory / name
    frame.to_parquet(path)
    return {'path': str(path), 'file': name, 'tick_count': len(trades),
            'start_time': _iso(trades[0][0]), 'end_time': _iso(trades[-1][0]),
            'origin_class': origin_class,
            'deficiencies': [] if quote else [DataDeficiency.NO_QUOTE]}


def _iso(time_msc: int) -> str:
    """
    The tick index's second-precision timestamp of an instant.

    Args:
        time_msc: Epoch milliseconds

    Returns:
        The ISO string, without a zone like the index writes it
    """
    instant = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc).replace(tzinfo=None)
    return instant.replace(microsecond=0).isoformat()
