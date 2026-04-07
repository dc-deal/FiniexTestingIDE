from dataclasses import dataclass
from enum import Enum
from typing import Dict, TypedDict

from python.framework.types.parameter_types import OutputValue


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
    API = "api"               # HTTP requests (News API, Sentiment) - Post-V1
    # Live connections (WebSocket, AI alerts) - Post-V1
    EVENT = "event"


@dataclass
class WorkerResult:
    """
    Typed output container — values keyed by output schema names.

    Workers declare their outputs via get_output_schema(). The compute()
    method returns WorkerResult(outputs={...}) with keys matching the schema.

    Args:
        outputs: Dict of output values keyed by schema-declared names
    """
    outputs: Dict[str, OutputValue]

    def get_signal(self, name: str) -> OutputValue:
        """
        Access an output value by schema-declared name.

        Args:
            name: Output parameter name (must match get_output_schema() key)

        Returns:
            Output value
        """
        return self.outputs[name]
