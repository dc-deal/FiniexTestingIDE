"""
Bar Rendering Test Fixtures.

Provides synthetic tick generators for bar renderer consistency
testing. Generates deterministic tick sequences covering standard
cases, boundary crossings, gaps, and all timeframes.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd
import pytest

from python.framework.types.market_types.market_data_types import TickData


# =============================================================================
# SYNTHETIC TICK GENERATORS
# =============================================================================

def generate_ticks(
    symbol: str = 'BTCUSD',
    start: datetime = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    count: int = 100,
    interval_seconds: int = 3,
    bid_start: float = 42000.0,
    spread: float = 1.0,
    price_step: float = 0.5,
    volume_per_tick: float = 0.1,
) -> List[TickData]:
    """
    Generate a deterministic sequence of synthetic ticks.

    Args:
        symbol: Trading symbol
        start: Start timestamp (UTC)
        count: Number of ticks to generate
        interval_seconds: Seconds between ticks
        bid_start: Starting bid price
        spread: Constant spread (ask = bid + spread)
        price_step: Price increment per tick (creates uptrend)
        volume_per_tick: Volume per tick

    Returns:
        List of TickData objects
    """
    ticks = []
    for i in range(count):
        ts = start + timedelta(seconds=i * interval_seconds)
        bid = bid_start + (i * price_step)
        ask = bid + spread
        ticks.append(TickData(
            timestamp=ts,
            symbol=symbol,
            bid=bid,
            ask=ask,
            volume=volume_per_tick,
        ))
    return ticks


def ticks_to_dataframe(ticks: List[TickData]) -> pd.DataFrame:
    """
    Convert TickData list to DataFrame matching VectorizedBarRenderer input.

    Args:
        ticks: List of TickData objects

    Returns:
        DataFrame with timestamp, bid, ask, real_volume columns
    """
    records = []
    for t in ticks:
        records.append({
            'timestamp': t.timestamp,
            'bid': t.bid,
            'ask': t.ask,
            'real_volume': t.volume,
        })
    return pd.DataFrame(records)


def generate_ticks_with_gap(
    symbol: str = 'BTCUSD',
    start: datetime = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    ticks_before_gap: int = 30,
    gap_minutes: int = 15,
    ticks_after_gap: int = 30,
    interval_seconds: int = 3,
    bid_start: float = 42000.0,
    spread: float = 1.0,
    price_step: float = 0.5,
    volume_per_tick: float = 0.1,
) -> List[TickData]:
    """
    Generate ticks with a gap in the middle (simulating market pause).

    Args:
        symbol: Trading symbol
        start: Start timestamp (UTC)
        ticks_before_gap: Ticks before the gap
        gap_minutes: Duration of gap in minutes
        ticks_after_gap: Ticks after the gap
        interval_seconds: Seconds between ticks
        bid_start: Starting bid price
        spread: Constant spread
        price_step: Price increment per tick
        volume_per_tick: Volume per tick

    Returns:
        List of TickData objects with a time gap
    """
    before = generate_ticks(
        symbol=symbol,
        start=start,
        count=ticks_before_gap,
        interval_seconds=interval_seconds,
        bid_start=bid_start,
        spread=spread,
        price_step=price_step,
        volume_per_tick=volume_per_tick,
    )

    # Resume after gap
    last_ts = before[-1].timestamp
    gap_start = last_ts + timedelta(minutes=gap_minutes)
    last_bid = bid_start + (ticks_before_gap * price_step)

    after = generate_ticks(
        symbol=symbol,
        start=gap_start,
        count=ticks_after_gap,
        interval_seconds=interval_seconds,
        bid_start=last_bid,
        spread=spread,
        price_step=price_step,
        volume_per_tick=volume_per_tick,
    )

    return before + after


def generate_boundary_ticks(
    symbol: str = 'BTCUSD',
    timeframe_minutes: int = 5,
    start: datetime = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    bid_start: float = 42000.0,
    spread: float = 1.0,
    volume_per_tick: float = 0.1,
) -> List[TickData]:
    """
    Generate ticks precisely on and around bar boundaries.

    Creates ticks at: boundary-1s, boundary, boundary+1s
    for 3 consecutive boundaries. Tests exact boundary handling.

    Args:
        symbol: Trading symbol
        timeframe_minutes: Bar duration in minutes
        start: Start timestamp (aligned to bar boundary)
        bid_start: Starting bid price
        spread: Constant spread
        volume_per_tick: Volume per tick

    Returns:
        List of TickData with ticks at/near boundaries
    """
    ticks = []
    price = bid_start

    for bar_idx in range(3):
        boundary = start + timedelta(minutes=bar_idx * timeframe_minutes)
        offsets = [-1, 0, 1]  # seconds relative to boundary

        for offset in offsets:
            ts = boundary + timedelta(seconds=offset)
            # Skip negative timestamps (before start)
            if ts < start:
                continue
            bid = price
            ask = bid + spread
            ticks.append(TickData(
                timestamp=ts,
                symbol=symbol,
                bid=bid,
                ask=ask,
                volume=volume_per_tick,
            ))
            price += 0.5

    return ticks


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def synthetic_ticks() -> List[TickData]:
    """Standard synthetic tick sequence (100 ticks, 3s interval)."""
    return generate_ticks()


@pytest.fixture
def synthetic_ticks_with_gap() -> List[TickData]:
    """Tick sequence with a 15-minute gap."""
    return generate_ticks_with_gap()


@pytest.fixture
def tick_dataframe(synthetic_ticks) -> pd.DataFrame:
    """Tick DataFrame for VectorizedBarRenderer."""
    return ticks_to_dataframe(synthetic_ticks)


@pytest.fixture
def gap_tick_dataframe(synthetic_ticks_with_gap) -> pd.DataFrame:
    """Tick DataFrame with gap for VectorizedBarRenderer."""
    return ticks_to_dataframe(synthetic_ticks_with_gap)
