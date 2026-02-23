from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, TypedDict


class IndicatorConfig(TypedDict, total=False):
    """
    Type-safe config for INDICATOR workers.

    Required:
        periods: Dict[timeframe, bars] - Warmup requirements per timeframe

    Optional:
        Any worker-specific parameters (deviation, threshold, etc.)
    """
    periods: Dict[str, int]  # REQUIRED - e.g. {"M5": 20, "M30": 50}


class WorkerState(Enum):
    """Worker execution states"""
    IDLE = "idle"
    WORKING = "working"
    READY = "ready"
    ERROR = "error"
    ASYNC_WORKING = "async_working"


class WorkerType(Enum):
    """Worker type classification for monitoring and performance tracking."""
    INDICATOR = "indicator"   # Synchronous calculations (RSI, SMA, etc.)
    API = "api"               # HTTP requests (News API, Sentiment) - Post-MVP
    # Live connections (WebSocket, AI alerts) - Post-MVP
    EVENT = "event"


@dataclass
class WorkerResult:
    """Result from worker computation"""
    worker_name: str
    value: Any
    confidence: float = 1.0
    computation_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
