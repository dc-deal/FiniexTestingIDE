
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Tuple
from python.framework.types.market_data_types import Bar, TickData


def process_deserialize_ticks_batch(scenario_symbol: str, ticks_tuple_list: Dict[str, Tuple[Any, ...]]) -> Tuple[TickData, ...]:
    """
    Optimierte Batch-Deserialisierung für große Tick-Mengen.

    Nutzt list comprehension für bessere Performance.
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
            ts = tick_data['timestamp']
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)

            result.append(TickData(
                timestamp=ts,
                symbol=scenario_symbol,
                bid=float(tick_data['bid']),
                ask=float(tick_data['ask']),
                volume=float(tick_data.get('volume', 0.0))
            ))
    return tuple(result)


# ============================================================================
# BAR DESERIALIZATION (Top-level function)
# ============================================================================


def deserialize_bars_batch(symbol: str, bars_tuple: Tuple[Any, ...]) -> Tuple[Bar, ...]:
    """
    Deserialize bar dicts from shared_data into Bar objects.

    Converts Pandas-based bar data to framework Bar objects:
    - Pandas Timestamp → ISO string
    - float tick_count → int
    - Drops extra fields (bar_type, synthetic_fields, reason)

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
