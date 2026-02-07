"""
FiniexTestingIDE - Worker Computation Tests
Shared fixtures: Bar factory, Tick factory, mock logger.

All test data is deterministic - no randomness.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def mock_logger():
    """Mock ScenarioLogger for worker instantiation."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


# ============================================
# Bar Factory
# ============================================

def make_bars(
    closes: list,
    timeframe: str = "M5",
    symbol: str = "EURUSD",
) -> list:
    """
    Create Bar list from close prices.

    Open/High/Low are synthetic but OHLC-valid:
    - open = close (flat bars, only close matters for indicators)
    - high = close + 0.0005
    - low  = close - 0.0005
    - volume = 100.0 (constant, irrelevant for RSI/Envelope/MACD)

    Args:
        closes: List of close prices
        timeframe: Bar timeframe (default "M5")
        symbol: Symbol name (default "EURUSD")

    Returns:
        List of Bar instances
    """
    return make_bars_with_volume(
        closes=closes,
        volumes=[100.0] * len(closes),
        timeframe=timeframe,
        symbol=symbol,
    )


def make_bars_with_volume(
    closes: list,
    volumes: list,
    timeframe: str = "M5",
    symbol: str = "EURUSD",
) -> list:
    """
    Create Bar list from close prices and volumes.

    Required for OBV tests where volume matters.

    Args:
        closes: List of close prices
        volumes: List of volume values (must match length of closes)
        timeframe: Bar timeframe
        symbol: Symbol name

    Returns:
        List of Bar instances
    """
    assert len(closes) == len(volumes), (
        f"closes ({len(closes)}) and volumes ({len(volumes)}) must match"
    )

    bars = []
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        bars.append(Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=f"2025-10-01T00:{i:02d}:00+00:00",
            open=close,
            high=close + 0.0005,
            low=close - 0.0005,
            close=close,
            volume=volume,
            tick_count=50,
            is_complete=True,
        ))
    return bars


def make_tick(
    bid: float,
    ask: float = None,
    symbol: str = "EURUSD",
) -> TickData:
    """
    Create a TickData instance.

    Args:
        bid: Bid price
        ask: Ask price (default: bid + 0.0002 spread)
        symbol: Symbol name

    Returns:
        TickData instance with mid = (bid + ask) / 2
    """
    if ask is None:
        ask = bid + 0.0002

    return TickData(
        timestamp=datetime(2025, 10, 1, 12, 0, 0),
        symbol=symbol,
        bid=bid,
        ask=ask,
        volume=0.0,
    )
