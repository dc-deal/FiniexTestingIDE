from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum


class WorkerState(Enum):
    IDLE = "idle"
    WORKING = "working"
    READY = "ready"
    ERROR = "error"
    ASYNC_WORKING = "async_working"  # For API calls etc.


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
        return (self.bid + self.ask) / 2.0


@dataclass
class WorkerResult:
    """Result from worker computation"""

    worker_name: str
    value: Any
    confidence: float = 1.0
    computation_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False  # True if recomputation was skipped


@dataclass
class WorkerContract:
    """Contract defining worker requirements"""

    min_warmup_bars: int = 0
    parameters: Dict[str, Any] = field(default_factory=dict)
    price_change_sensitivity: float = 0.0001
    max_computation_time_ms: float = 100.0  # Timeout for worker
    can_work_async: bool = False  # Can return "still working" response
