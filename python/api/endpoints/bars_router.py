"""
Coverage and bars endpoints.

GET /api/v1/brokers/{broker}/symbols/{symbol}/coverage
GET /api/v1/brokers/{broker}/symbols/{symbol}/bars?timeframe=M30&from=<iso>&to=<iso>
"""

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Query

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.api.api_types import (
    ApiException,
    BarResponse,
    CoverageResponse,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig

router = APIRouter()

MAX_BARS = 10_000


def _load_index() -> BarsIndexManager:
    index = BarsIndexManager()
    index.load_index()
    return index


def _require_broker_symbol(index: BarsIndexManager, broker: str, symbol: str) -> None:
    if broker not in index.list_broker_types():
        raise ApiException(404, 'not_found', f"Broker '{broker}' not found.")
    if symbol not in index.list_symbols(broker_type=broker):
        raise ApiException(404, 'not_found', f"Symbol '{symbol}' not found for broker '{broker}'.")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get('/brokers/{broker}/symbols/{symbol}/coverage', response_model=CoverageResponse)
def get_coverage(broker: str, symbol: str) -> CoverageResponse:
    """Return available date range and timeframes for a broker/symbol pair."""
    index = _load_index()
    _require_broker_symbol(index, broker, symbol)

    stats = index.get_symbol_stats(broker, symbol)
    if not stats:
        raise ApiException(404, 'not_found', f"No bar data for '{broker}/{symbol}'.")

    start_times = [pd.Timestamp(v['start_time']) for v in stats.values()]
    end_times = [pd.Timestamp(v['end_time']) for v in stats.values()]

    return CoverageResponse(
        start=min(start_times).isoformat(),
        end=max(end_times).isoformat(),
        timeframes=sorted(stats.keys()),
    )


@router.get('/brokers/{broker}/symbols/{symbol}/bars', response_model=list[BarResponse])
def get_bars(
    broker: str,
    symbol: str,
    timeframe: str,
    from_time: datetime = Query(..., alias='from'),
    to_time: datetime = Query(..., alias='to'),
) -> list[BarResponse]:
    """
    Return OHLCV bars for a broker/symbol/timeframe within a date range.

    Timestamps in the response are UTC unix seconds.
    Capped at MAX_BARS per request.
    """
    if not TimeframeConfig.exists(timeframe):
        raise ApiException(
            400, 'invalid_timeframe',
            f"Timeframe '{timeframe}' is not valid. Valid: {TimeframeConfig.sorted()}",
        )

    from_utc = _utc(from_time)
    to_utc = _utc(to_time)

    if from_utc >= to_utc:
        raise ApiException(400, 'invalid_range', "'from' must be earlier than 'to'.")

    index = _load_index()
    _require_broker_symbol(index, broker, symbol)

    bar_file = index.get_bar_file(broker, symbol, timeframe)
    if bar_file is None:
        raise ApiException(
            404, 'not_found',
            f"No bars for '{broker}/{symbol}' at timeframe '{timeframe}'.",
        )

    df = pd.read_parquet(bar_file)
    mask = (df['timestamp'] >= from_utc) & (df['timestamp'] <= to_utc)
    df = df[mask].head(MAX_BARS)

    return [
        BarResponse(
            t=int(row['timestamp'].timestamp()),
            o=row['open'],
            h=row['high'],
            l=row['low'],
            c=row['close'],
            v=row['volume'],
        )
        for _, row in df.iterrows()
    ]
