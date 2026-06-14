from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Set, Tuple


@dataclass
class TickData:
    """
    Tick data structure

    PERFORMANCE OPTIMIZED:
    - timestamp is now datetime object instead of string
    - Parsing happens once during data loading, not during bar rendering
    - Expected speedup: 50-70% in bar rendering operations
    """
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    volume: float = 0.0
    # Millisecond-precision Unix timestamp from broker (0 = not available)
    time_msc: int = 0
    # Local device clock at tick receipt, ms precision (0 = not available)
    collected_msc: int = 0
    # True if tick was clipped by tick processing budget (broker sees it, algo skips it)
    is_clipped: bool = False

    @property
    def mid(self) -> float:
        """Mid price between bid/ask"""
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'symbol': self.symbol,
            'bid': self.bid,
            'ask': self.ask,
            'volume': self.volume,
            'mid': self.mid
        }


@dataclass
class Bar:
    """
    Standard bar structure for all timeframes.

    OPTIMIZATION: Uses __slots__ to reduce memory footprint and pickle overhead.
    Removes __dict__ from each instance (~15% pickle size reduction).
    """

    symbol: str
    timeframe: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    tick_count: int = 0
    is_complete: bool = False

    @property
    def ohlc(self) -> Tuple[float, float, float, float]:
        """OHLC tuple"""
        return (self.open, self.high, self.low, self.close)

    def update_with_tick(self, tick_price: float, tick_volume: float = 0):
        """Update bar with new tick data"""
        if self.open == 0:
            self.open = tick_price
            self.high = tick_price
            self.low = tick_price
        else:
            self.high = max(self.high, tick_price)
            self.low = min(self.low, tick_price)

        self.close = tick_price
        self.volume += tick_volume
        self.tick_count += 1

    def to_dict(self) -> dict:
        """
        Convert Bar to dictionary for serialization.

        Used for:
        - BacktestingMetadata bar snapshots
        - Cross-process data transfer
        - JSON serialization

        Note: timestamp is already a string in Bar (not datetime).

        Returns:
            Dict with all bar fields
        """
        return {
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'timestamp': self.timestamp,  # Already ISO string
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'tick_count': self.tick_count,
            'is_complete': self.is_complete
        }


@dataclass
class BarRenderState:
    """
    Bar-lifecycle transitions the bar renderer surfaces for one consumed algo pass.

    The bar renderer is the single authority for when a bar closes (the moment a
    bar is historized). Instead of letting the orchestrator re-derive that from
    tick timing, the renderer lifts the transition as typed state that flows to
    the worker orchestrator. The controller accumulates it across clipped ticks /
    idle heartbeats and hands it over once per algo pass, so a close on a skipped
    tick is never lost.

    Forward seam (#375): these transitions become first-class timeline events
    (BarClosedEvent). This package is the typed precursor — extend it here as more
    bar-lifecycle states surface (e.g. bar_opened).

    Args:
        closed_timeframes: Timeframes whose bar closed (was historized) since the
            previous consume — the recompute trigger for ON_BAR_CLOSE workers
    """
    closed_timeframes: Set[str] = field(default_factory=set)


# ============================================================================
# TICK TRANSPORT CONTRACT
# ============================================================================


class TickTransportColumn(str, Enum):
    """
    Columns that cross the process boundary via pickle.

    Shared between serialize (pack) and deserialize (unpack).
    timestamp is NOT transported — derived from TIME_MSC during deserialization.
    str-based Enum: values are usable directly as dict keys and DataFrame column names.
    """
    TIME_MSC = 'time_msc'
    COLLECTED_MSC = 'collected_msc'
    BID = 'bid'
    ASK = 'ask'
    VOLUME = 'volume'
    IS_CLIPPED = 'is_clipped'
