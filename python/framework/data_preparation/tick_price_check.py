"""
The Price Check Where a Tick's Price Is Consumed

The tick reader derives every tick's price once and refuses nothing, so a caller that reads the
quote as data keeps it as it is. The two consumers that build from the price — the bar import and
the backtest's data preparation — refuse a file here when a tick's price is no price at all.
"""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from python.framework.exceptions.data_quality_errors import TickPriceUnusableError
from python.framework.utils.time_utils import format_log_event_time


def refuse_unusable_price(path: Path, df: pd.DataFrame) -> None:
    """
    Refuse a frame holding a tick whose price no bar may be built from.

    Unusable is a price that is not a finite number above zero, or a midpoint whose
    quote has a side at zero or below — that midpoint is positive and still wrong.

    Args:
        path: The file the frame was read from, for the message
        df: A frame from `read_tick_parquet`, with its derived `price`

    Returns:
        None
    """
    traded = (df['last'] > 0.0 if 'last' in df.columns
              else pd.Series(False, index=df.index))
    price = df['price'].to_numpy(dtype=np.float64)
    unusable = ~np.isfinite(price) | (price <= 0.0)
    from_quote = ~traded.to_numpy(dtype=bool)
    unusable |= from_quote & (
        (df['bid'].to_numpy(dtype=np.float64) <= 0.0)
        | (df['ask'].to_numpy(dtype=np.float64) <= 0.0))
    if not unusable.any():
        return

    first = int(np.argmax(unusable))
    row = df.iloc[first]
    last = row['last'] if 'last' in df.columns else None
    last_text = f'last {last}' if last is not None and last > 0.0 else 'no last'
    at = (format_log_event_time(datetime.fromtimestamp(int(row['time_msc']) / 1000, tz=timezone.utc))
          + ' UTC' if 'time_msc' in df.columns else f'row {first:,}')
    raise TickPriceUnusableError(
        f'{Path(path).name}: {int(unusable.sum()):,} of {len(df):,} ticks carry no usable price '
        f'(first at {at}: {last_text}, bid {row["bid"]}, ask {row["ask"]}). A bar built from '
        f'them would read zero or half a price; the file is refused here.'
    )
