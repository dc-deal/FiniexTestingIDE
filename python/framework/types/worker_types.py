from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, TypedDict, Union

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


class _AllSignalsSentinel:
    """Sentinel: a decision logic reads ALL of a worker's outputs (compute-all)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return 'SUBSCRIBE_ALL'


# Public sentinel — WorkerRequirement(signals=SUBSCRIBE_ALL) / WorkerRequirement.all(type)
SUBSCRIBE_ALL = _AllSignalsSentinel()


@dataclass(frozen=True)
class WorkerRequirement:
    """
    One decision-logic → worker-instance requirement (#425).

    Unifies the former worker-instance type declaration and the consumed-output
    declaration into one mandatory, per-instance object. signals is either an
    explicit frozenset of the worker's output-enum members, or the SUBSCRIBE_ALL
    sentinel (read everything — the explicit successor of the old empty-map
    default, bit-identical compute-all).

    Args:
        worker_type: Worker type string (e.g. 'CORE/bollinger')
        signals: Consumed output keys (frozenset of enum members) or SUBSCRIBE_ALL
    """
    worker_type: str
    signals: Union[FrozenSet[str], _AllSignalsSentinel]

    @classmethod
    def of(cls, worker_type: str, *signals: str) -> 'WorkerRequirement':
        """
        Build a requirement reading an explicit subset of a worker's outputs.

        Args:
            worker_type: Worker type string
            signals: Output enum members the logic reads from this instance

        Returns:
            WorkerRequirement with the given signal subset
        """
        return cls(worker_type=worker_type, signals=frozenset(signals))

    @classmethod
    def all(cls, worker_type: str) -> 'WorkerRequirement':
        """
        Build a requirement reading ALL of a worker's outputs (compute-all).

        Args:
            worker_type: Worker type string

        Returns:
            WorkerRequirement with the SUBSCRIBE_ALL sentinel
        """
        return cls(worker_type=worker_type, signals=SUBSCRIBE_ALL)
