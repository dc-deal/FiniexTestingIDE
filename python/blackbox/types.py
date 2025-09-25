"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class WorkerState(Enum):
    """Worker execution states"""

    IDLE = "idle"
    WORKING = "working"
    READY = "ready"
    ERROR = "error"
    ASYNC_WORKING = "async_working"


@dataclass
class TickData:
    """Tick data structure"""

    timestamp: str
    symbol: str
    bid: float
    ask: float
    volume: float = 0.0

    @property
    def mid(self) -> float:
        """Mid price between bid/ask"""
        return (self.bid + self.ask) / 2.0


@dataclass
class Bar:
    """Standard bar structure for all timeframes"""

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


@dataclass
class WorkerResult:
    """Result from worker computation"""

    worker_name: str
    value: Any
    confidence: float = 1.0
    computation_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False


@dataclass
class WorkerContract:
    """Contract defining worker requirements"""

    min_warmup_bars: int = 0
    parameters: Dict[str, Any] = field(default_factory=dict)
    price_change_sensitivity: float = 0.0001
    max_computation_time_ms: float = 100.0
    can_work_async: bool = False
    required_timeframes: List[str] = field(default_factory=lambda: ["M1"])
    warmup_requirements: Dict[str, int] = field(default_factory=dict)


@dataclass
class TestScenario:
    """Test scenario configuration for batch testing"""

    symbol: str
    start_date: str
    end_date: str
    max_ticks: Optional[int] = None
    data_mode: str = "realistic"
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    name: Optional[str] = None

    def __post_init__(self):
        if self.name is None:
            self.name = f"{self.symbol}_{self.start_date}_{self.end_date}"


@dataclass
class GlobalContract:
    """Aggregated contract from all scenarios/workers"""

    max_warmup_bars: int
    all_timeframes: List[str]
    warmup_by_timeframe: Dict[str, int]
    total_workers: int
    all_parameters: Dict[str, Any]


class TimeframeConfig:
    """Timeframe configuration and utilities"""

    TIMEFRAME_MINUTES = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }

    @classmethod
    def get_minutes(cls, timeframe: str) -> int:
        """Get minutes for timeframe"""
        return cls.TIMEFRAME_MINUTES.get(timeframe, 1)

    @classmethod
    def get_bar_start_time(cls, timestamp: datetime, timeframe: str) -> datetime:
        """Calculate bar start time for given timestamp"""
        minutes = cls.get_minutes(timeframe)
        total_minutes = timestamp.hour * 60 + timestamp.minute
        bar_start_minute = (total_minutes // minutes) * minutes

        return timestamp.replace(
            minute=bar_start_minute % 60,
            hour=bar_start_minute // 60,
            second=0,
            microsecond=0,
        )

    @classmethod
    def is_bar_complete(
        cls, bar_start: datetime, current_time: datetime, timeframe: str
    ) -> bool:
        """Check if bar is complete"""
        from datetime import timedelta

        bar_duration = timedelta(minutes=cls.get_minutes(timeframe))
        bar_end = bar_start + bar_duration
        return current_time >= bar_end
