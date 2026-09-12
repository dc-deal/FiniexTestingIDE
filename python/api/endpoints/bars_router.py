"""
Coverage and bars endpoints.

GET /api/v1/brokers/{broker}/symbols/{symbol}/coverage
GET /api/v1/brokers/{broker}/symbols/{symbol}/bars?timeframe=M30&from=<iso>&to=<iso>&limit=<n>
"""

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Query, Response

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.types.api.api_types import (
    BarResponse,
    CoverageResponse,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig

router = APIRouter()

MAX_BARS = 10_000

# The bar response body is a bare array, because that is what existing clients read.
# Everything a reader needs ABOUT the rows therefore travels as headers: a client that
# does not know them is unaffected, and a silently shortened payload stops being possible.
HEADER_COUNT = 'X-Bar-Count'
HEADER_TOTAL = 'X-Bar-Total'
HEADER_LIMIT = 'X-Bar-Limit'
HEADER_TRUNCATED = 'X-Bar-Truncated'

# Three facts a caller cannot infer from the rows and has no second chance to get right:
# a bar is stamped with the OPEN of its period (pandas resample labels left), the stamp is
# UTC, and OHLC is the MID of bid/ask rather than a traded price.
HEADER_TIME_BASIS = 'X-Bar-Time-Basis'
HEADER_TIMEZONE = 'X-Bar-Timezone'
HEADER_PRICE_BASIS = 'X-Bar-Price-Basis'

TIME_BASIS = 'open'
TIMEZONE = 'UTC'
PRICE_BASIS = 'mid'


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
    response: Response,
    broker: str,
    symbol: str,
    timeframe: str,
    from_time: datetime = Query(..., alias='from'),
    to_time: datetime = Query(..., alias='to'),
    limit: int = Query(MAX_BARS),
) -> list[BarResponse]:
    """
    Return OHLCV bars for a broker/symbol/timeframe within a date range.

    Timestamps in the response are UTC unix seconds and mark the bar's OPEN.
    The caller states its own row cap via `limit`; omitting it applies MAX_BARS.
    A response that was cut says so in its headers rather than ending silently.

    Args:
        response: FastAPI response, carries the row and semantics headers
        broker: Broker type identifier the bars were rendered for
        symbol: Trading symbol
        timeframe: Timeframe key (M1, M5, ...)
        from_time: Range start, inclusive
        to_time: Range end, inclusive
        limit: Maximum rows to return, at most MAX_BARS

    Returns:
        Bars within the range, oldest first, at most `limit` of them
    """
    if not TimeframeConfig.exists(timeframe):
        raise ApiException(
            400, 'invalid_timeframe',
            f"Timeframe '{timeframe}' is not valid. Valid: {TimeframeConfig.sorted()}",
        )

    if limit < 1 or limit > MAX_BARS:
        raise ApiException(
            400, 'invalid_limit',
            f"'limit' must be between 1 and {MAX_BARS}, got {limit}.",
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
    matched = df[mask]

    # The row count is taken BEFORE the cap: 'how many were there' is the number a
    # caller needs to page the rest, and it is exactly what head() throws away.
    total = len(matched)
    page = matched.head(limit)

    response.headers[HEADER_COUNT] = str(len(page))
    response.headers[HEADER_TOTAL] = str(total)
    response.headers[HEADER_LIMIT] = str(limit)
    response.headers[HEADER_TRUNCATED] = 'true' if total > limit else 'false'
    response.headers[HEADER_TIME_BASIS] = TIME_BASIS
    response.headers[HEADER_TIMEZONE] = TIMEZONE
    response.headers[HEADER_PRICE_BASIS] = PRICE_BASIS

    return [
        BarResponse(
            t=int(row['timestamp'].timestamp()),
            o=row['open'],
            h=row['high'],
            l=row['low'],
            c=row['close'],
            v=row['volume'],
            tc=int(row['tick_count']),
        )
        for _, row in page.iterrows()
    ]
