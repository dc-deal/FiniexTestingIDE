"""
Discovery Types
===============
Type definitions for market discovery results.

Location: python/framework/types/discovery_types.py
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List


class MoveDirection(Enum):
    """Direction of an extreme price move."""
    LONG = "long"
    SHORT = "short"

    def __str__(self) -> str:
        return self.value


@dataclass
class ExtremeMove:
    """
    A detected extreme directional price movement window.

    Represents a contiguous time window where price moved strongly
    in one direction, measured by ATR multiples.
    """
    broker_type: str
    symbol: str
    timeframe: str
    direction: MoveDirection

    # Time window
    start_time: datetime
    end_time: datetime
    bar_count: int

    # Price data
    entry_price: float      # open of first bar
    extreme_price: float    # highest high (LONG) or lowest low (SHORT)
    exit_price: float       # close of last bar

    # Movement metrics
    move_pips: float         # directional move in pips (entry → extreme)
    move_atr_multiple: float # move size as multiple of avg ATR
    max_adverse_pips: float  # max retracement against the move direction
    window_atr: float        # average ATR over this window (raw price units)

    # Tick activity
    tick_count: int


@dataclass
class ExtremeMoveResult:
    """
    Collection of discovered extreme moves for a symbol.
    """
    broker_type: str
    symbol: str
    timeframe: str
    longs: List[ExtremeMove]   # sorted by move_atr_multiple descending
    shorts: List[ExtremeMove]  # sorted by move_atr_multiple descending
    scanned_bars: int
    avg_atr: float             # average ATR used for normalization
    pip_size: float            # pip size for this symbol
    generated_at: datetime
