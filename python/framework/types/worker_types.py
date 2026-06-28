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


class ComputeBasis(Enum):
    """
    The temporal data a worker computes on — its 'data subscription' (#420).

    One value per worker instance, declared mandatorily by the worker (config key
    'compute_basis' overrides per instance). Unifies the former recompute cadence
    (#384) and current-bar inclusion (#387) into a single binary axis:

    - LIVE: includes the forming (current) bar / tick.mid and recomputes every tick —
      the value drifts intra-bar, so the worker reacts to events within a bar. The
      tick-native default; required by tick-reactive consumers (live %B from tick.mid).
    - BAR_CLOSE: completed bars only, recomputes only when one of the worker's required
      timeframes closes a bar (cached result served in between). Stable and cheap; only
      correct for consumers that read on the bar-close grid (an intra-bar event that
      reverts before the close is invisible to it).
    """
    LIVE = 'live'              # per-tick, intra-bar (forming bar / tick.mid)
    BAR_CLOSE = 'bar_close'    # completed bars only, recompute on bar close


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
