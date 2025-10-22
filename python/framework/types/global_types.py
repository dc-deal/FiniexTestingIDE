"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from python.components.logger.scenario_logger import ScenarioLogger


class WorkerState(Enum):
    """Worker execution states"""
    IDLE = "idle"
    WORKING = "working"
    READY = "ready"
    ERROR = "error"
    ASYNC_WORKING = "async_working"


class WorkerType(Enum):
    """Worker type classification for monitoring and performance tracking."""
    COMPUTE = "compute"  # Synchronous calculations (RSI, SMA, etc.)
    API = "api"          # HTTP requests (News API, Sentiment) - Post-MVP
    EVENT = "event"      # Live connections (WebSocket, AI alerts) - Post-MVP


@dataclass
class TickData:
    """
    Tick data structure

    PERFORMANCE OPTIMIZED:
    - timestamp is now datetime object instead of string
    - Parsing happens once during data loading, not during bar rendering
    - Expected speedup: 50-70% in bar rendering operations
    """
    timestamp: datetime  # Changed from str to datetime!
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


# ============================================
# Trading Decision Structure
# ============================================
@dataclass
class Decision:
    """
    Trading decision output from DecisionLogic.

    Replaces dict-based decision format for type safety.
    DecisionLogic returns this structured output to orchestrator.
    """
    action: str  # "BUY", "SELL", "FLAT", "DEFENSIVE", etc.
    confidence: float  # 0.0 - 1.0
    reason: str = ""
    price: float = 0.0
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for logging/serialization"""
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "price": self.price,
            "timestamp": self.timestamp,
            **self.metadata
        }


@dataclass
class TestScenario:
    """Test scenario configuration for batch testing"""

    symbol: str
    start_date: str
    end_date: str
    max_ticks: Optional[int] = None
    data_mode: str = "realistic"
    enabled: bool = True  # Default: enabled
    logger: ScenarioLogger = None

    # ============================================
    # STRATEGY PARAMETERS
    # ============================================
    # Strategy-Logic (→ WorkerCoordinator sammelt Requirements & dessen Parameter)
    strategy_config: Dict[str, Any] = field(default_factory=dict)

    # NEW: Execution-Optimization (→ Framework)
    execution_config: Optional[Dict[str, Any]] = None

    # NEW: TradeSimulator configuration (per scenario)
    # Allows each scenario to have different balance/currency/leverage
    trade_simulator_config: Optional[Dict[str, Any]] = None

    name: Optional[str] = None

    def __post_init__(self):
        if self.name is None:
            self.name = f"{self.symbol}_{self.start_date}_{self.end_date}"

        # Smart Defaults für Execution Config
        if self.execution_config is None:
            self.execution_config = {
                # ============================================
                # EXECUTION CONFIGURATION STANDARD
                # ============================================
                # Worker-Level Parallelization
                # True = Workers parallel (gut bei 4+ workers)
                "parallel_workers": None,  # Auto-detect
                "worker_parallel_threshold_ms": 1.0,  # Nur parallel wenn Worker >1ms
                # ← NEU: Künstliche Last - NUR für Heavy workers
                # Ist eher für self-testing szenarios und stress tests gedacht.
                "artificial_load_ms": 5.0,  # 5ms pro Worker
                # Performance Tuning
                "adaptive_parallelization": True,  # Auto-detect optimal mode
                "log_performance_stats": True,  # Log timing statistics
            }


class TimeframeConfig:
    """
    Timeframe configuration and utilities

    PERFORMANCE OPTIMIZED:
    - Added caching for get_bar_start_time()
    - Same minute always returns same bar start time
    """

    TIMEFRAME_MINUTES = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }

    # Cache for bar start times: (timestamp_minute_key, timeframe) -> bar_start_time
    _bar_start_cache: Dict[Tuple[str, str], datetime] = {}

    @classmethod
    def get_minutes(cls, timeframe: str) -> int:
        """Get minutes for timeframe"""
        return cls.TIMEFRAME_MINUTES.get(timeframe, 1)

    @classmethod
    def get_bar_start_time(cls, timestamp: datetime, timeframe: str) -> datetime:
        """
        Calculate bar start time for given timestamp.

        PERFORMANCE OPTIMIZED:
        - Results are cached per minute
        - Same minute + timeframe always returns cached result
        - Reduces 40,000 calculations to ~few hundred cache lookups
        """
        # Create cache key from minute precision (ignore seconds/microseconds)
        cache_key = (
            f"{timestamp.year}-{timestamp.month:02d}-{timestamp.day:02d}"
            f"T{timestamp.hour:02d}:{timestamp.minute:02d}",
            timeframe
        )

        # Check cache first
        if cache_key in cls._bar_start_cache:
            return cls._bar_start_cache[cache_key]

        # Calculate bar start time
        minutes = cls.get_minutes(timeframe)
        total_minutes = timestamp.hour * 60 + timestamp.minute
        bar_start_minute = (total_minutes // minutes) * minutes

        bar_start = timestamp.replace(
            minute=bar_start_minute % 60,
            hour=bar_start_minute // 60,
            second=0,
            microsecond=0,
        )

        # Cache result
        cls._bar_start_cache[cache_key] = bar_start

        # Limit cache size (keep last 10,000 entries)
        if len(cls._bar_start_cache) > 10000:
            # Remove oldest 5000 entries
            keys_to_remove = list(cls._bar_start_cache.keys())[:5000]
            for key in keys_to_remove:
                del cls._bar_start_cache[key]

        return bar_start

    @classmethod
    def is_bar_complete(
        cls, bar_start: datetime, current_time: datetime, timeframe: str
    ) -> bool:
        """Check if bar is complete"""
        from datetime import timedelta
        bar_duration = timedelta(minutes=cls.get_minutes(timeframe))
        bar_end = bar_start + bar_duration
        return current_time >= bar_end


@dataclass
class BatchExecutionSummary:
    """Summary of batch execution results."""
    success: bool
    scenarios_count: int
    summary_execution_time: float
