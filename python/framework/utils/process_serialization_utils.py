"""
Process Serialization Utilities

Centralized serialization contract for cross-process tick data transport.
Owns both the "pack" (DataFrame -> transport dicts) and "unpack" (transport dicts -> TickData) logic.

Transport contract: Only fields listed in TickTransportColumn cross the process boundary.
All other Parquet columns are trimmed before serialization to reduce pickle payload (~50% reduction).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Tuple

import pandas as pd

from python.framework.types.market_types.market_data_types import Bar, TickData, TickTransportColumn


# ============================================================================
# TICK SERIALIZATION (DataFrame -> transport dicts)
# ============================================================================


def serialize_ticks_for_transport(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Trim DataFrame to transport columns and convert to list of dicts.

    Filters the DataFrame to only the columns needed by the tick loop consumer,
    dropping all other columns (last, tick_volume, chart_tick_volume,
    spread_points, spread_pct, tick_flags, session, etc.).

    Expects normalized column names (volume, not real_volume) — callers
    should use read_tick_parquet() for loading. Gracefully handles missing
    columns (e.g. collected_msc in pre-V1.3.0 data) — consumer uses defaults.

    Args:
        df: DataFrame with tick data (normalized columns from read_tick_parquet)

    Returns:
        List of dicts with only transport-relevant fields
    """
    available_cols = [
        c.value for c in TickTransportColumn if c.value in df.columns]
    return df[available_cols].to_dict('records')


# ============================================================================
# TICK DESERIALIZATION (transport dicts -> TickData)
# ============================================================================


def process_deserialize_ticks_batch(scenario_symbol: str, ticks_tuple_list: Dict[str, Tuple[Any, ...]]) -> Tuple[TickData, ...]:
    """
    Batch deserialization of transport tick dicts into TickData objects.

    Derives timestamp from time_msc (epoch ms -> UTC datetime).
    Symbol is taken from scenario config, not from the dict.

    Args:
        scenario_symbol: Trading symbol from scenario config (authoritative source)
        ticks_tuple_list: Dict mapping symbol -> tuple of tick dicts

    Returns:
        Tuple of TickData objects for the tick loop
    """
    ticks_tuple = ticks_tuple_list[scenario_symbol]
    if not ticks_tuple:
        raise KeyError(
            f"Ticks for scenario {scenario_symbol} could not be found in sharded data for process (ticks)")
    result = []
    for tick_data in ticks_tuple:
        if isinstance(tick_data, TickData):
            result.append(tick_data)
        elif isinstance(tick_data, dict):
            time_msc = int(tick_data[TickTransportColumn.TIME_MSC])
            ts = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)

            result.append(TickData(
                timestamp=ts,
                symbol=scenario_symbol,
                bid=float(tick_data[TickTransportColumn.BID]),
                ask=float(tick_data[TickTransportColumn.ASK]),
                volume=float(tick_data.get(TickTransportColumn.VOLUME, 0.0)),
                time_msc=time_msc,
                collected_msc=int(tick_data.get(
                    TickTransportColumn.COLLECTED_MSC, 0)),
                is_clipped=bool(tick_data.get(
                    TickTransportColumn.IS_CLIPPED, False))
            ))
    return tuple(result)


# ============================================================================
# TICK TIME RANGE HELPER
# ============================================================================


def time_range_from_transport_ticks(
    ticks: List[Dict[str, Any]]
) -> Tuple[datetime, datetime]:
    """
    Extract (first_tick, last_tick) datetime range from transport tick dicts.

    Derives UTC datetime from time_msc. Used by SharedDataPreparator for
    tick_ranges metadata (before process boundary).

    Args:
        ticks: List of transport tick dicts (must have 'time_msc' key)

    Returns:
        (first_tick_datetime, last_tick_datetime) as UTC-aware datetimes
    """
    first_msc = int(ticks[0][TickTransportColumn.TIME_MSC])
    last_msc = int(ticks[-1][TickTransportColumn.TIME_MSC])
    return (
        datetime.fromtimestamp(first_msc / 1000, tz=timezone.utc),
        datetime.fromtimestamp(last_msc / 1000, tz=timezone.utc)
    )


# ============================================================================
# BAR DESERIALIZATION (Top-level function)
# ============================================================================


def deserialize_bars_batch(symbol: str, bars_tuple: Tuple[Any, ...]) -> Tuple[Bar, ...]:
    """
    Deserialize bar dicts from shared_data into Bar objects.

    Converts Pandas-based bar data to framework Bar objects:
    - Pandas Timestamp → ISO string
    - float tick_count → int
    - Picks only OHLCV + metadata fields (ignores any extra columns)

    Args:
        symbol: Trading symbol from config (authoritative source)
        bars_tuple: Tuple of bar dicts (immutable, CoW-friendly)

    Returns:
        List of Bar objects for BarRenderer
    """
    result = []
    for bar_dict in bars_tuple:
        timestamp_str = bar_dict['timestamp'].isoformat()

        bar = Bar(
            symbol=symbol,
            timeframe=bar_dict['timeframe'],
            timestamp=timestamp_str,
            open=float(bar_dict['open']),
            high=float(bar_dict['high']),
            low=float(bar_dict['low']),
            close=float(bar_dict['close']),
            volume=float(bar_dict['volume']),
            tick_count=int(bar_dict['tick_count']),
            is_complete=True
        )
        result.append(bar)

    return tuple(result)


def serialize_value(v):
    """Safe recursive serializer for unknown structures."""
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: serialize_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [serialize_value(x) for x in v]

    # Fallback: convert to string as last resort
    return str(v)


def serialize_current_bars(current_bars: Dict[str, Any]) -> Dict[str, Dict]:
    """
    Serialize current bars for JSON export.

    Args:
        current_bars: Dict[timeframe, Bar] from bar_rendering_controller

    Returns:
        JSON-serializable dict
    """
    serialized = {}

    for timeframe, bar in current_bars.items():
        serialized[timeframe] = {
            "time": bar.timestamp if hasattr(bar, 'timestamp') else None,
            "open": float(bar.open) if hasattr(bar, 'open') else None,
            "high": float(bar.high) if hasattr(bar, 'high') else None,
            "low": float(bar.low) if hasattr(bar, 'low') else None,
            "close": float(bar.close) if hasattr(bar, 'close') else None,
            "volume": float(bar.volume) if hasattr(bar, 'volume') else None,
        }

    return serialized
