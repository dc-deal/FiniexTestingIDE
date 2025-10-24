from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


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
class WorkerResult:
    """Result from worker computation"""
    worker_name: str
    value: Any
    confidence: float = 1.0
    computation_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False
