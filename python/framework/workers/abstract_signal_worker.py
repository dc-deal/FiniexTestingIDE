"""
FiniexTestingIDE - Abstract Signal Worker
Base class for SIGNAL workers — pre-collected external data lookup (#141)
"""

from abc import abstractmethod
from typing import Dict, List, Optional

from python.framework.exceptions.signal_data_errors import SignalProviderNotInjectedError
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.signal_data_types import ResolvedSignal
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class AbstractSignalWorker(AbstractWorker):
    """
    Base class for SIGNAL workers — values looked up from pre-collected external
    data by timestamp, not computed from bars.

    Lookup-centric: a SIGNAL worker holds an injected SignalDataProvider and, on
    each tick, resolves the most recent snapshot with collected_msc <= tick. It
    refreshes only when the tick crosses into a new snapshot window
    (should_refresh) — the analogue of an INDICATOR's bar-update recompute. No
    warmup, no timeframes, no compute basis. The live API/EVENT refresh path
    rides #375; should_refresh is the forward-compatible seam.
    """

    # Signal source key this worker reads (e.g. 'llm_sentiment') — consumed by the
    # data-preparation layer to load the matching archive. Concrete workers set it.
    SIGNAL_SOURCE: str = ''

    def __init__(
        self,
        name: str,
        logger,
        parameters=None,
        trading_context: TradingContext = None
    ):
        """
        Initialize signal worker.

        Args:
            name: Worker name/identifier
            logger: ScenarioLogger instance (REQUIRED)
            parameters: ValidatedParameters or dict (auto-wrapped)
            trading_context: TradingContext (provides the scenario symbol)
        """
        super().__init__(
            name=name, logger=logger,
            parameters=parameters, trading_context=trading_context
        )
        # The scenario symbol whose per-symbol result this instance reads.
        self._symbol: Optional[str] = (
            trading_context.symbol if trading_context else None
        )
        # Injected by the framework at construction (sim subprocess / live boot).
        self._signal_provider: Optional[SignalDataProvider] = None
        # collected_msc of the last served snapshot (refresh-window tracking).
        self._last_snapshot_msc = None

    def set_signal_provider(self, provider: SignalDataProvider) -> None:
        """
        Inject the signal data provider (framework collaborator).

        Args:
            provider: SignalDataProvider built from the prepared signal series
        """
        self._signal_provider = provider

    def _require_provider(self) -> SignalDataProvider:
        """
        Return the injected provider or fail loudly (no silent fallback).

        Returns:
            The injected SignalDataProvider

        Raises:
            SignalProviderNotInjectedError: If no provider was injected
        """
        if self._signal_provider is None:
            raise SignalProviderNotInjectedError(
                f"SIGNAL worker '{self.name}' has no injected SignalDataProvider. "
                f"It must be built from the prepared signal series and injected at "
                f"construction (sim subprocess / live boot)."
            )
        return self._signal_provider

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        """SIGNAL — pre-collected external data lookup."""
        return WorkerType.SIGNAL

    @classmethod
    def get_signal_source(cls) -> str:
        """The signal source key this worker reads (e.g. 'llm_sentiment')."""
        return cls.SIGNAL_SOURCE

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """SIGNAL workers read external data — no market-activity dependency."""
        return None

    def get_warmup_requirements(self) -> Dict[str, int]:
        """SIGNAL workers have no warmup — data is looked up by timestamp."""
        return {}

    def get_required_timeframes(self) -> List[str]:
        """SIGNAL workers consume no bar timeframes."""
        return []

    def should_refresh(self, tick: TickData) -> bool:
        """
        Whether the tick crossed into a new snapshot window (recompute trigger).

        True at cold start and whenever the most recent snapshot with
        collected_msc <= tick differs from the last one served.

        Args:
            tick: Current tick

        Returns:
            True if the worker should recompute its result this tick
        """
        resolved = self._require_provider().nearest(tick.timestamp, self._symbol)
        current_msc = resolved.collected_msc if resolved else None
        return current_msc != self._last_snapshot_msc

    def compute_signal(self, tick: TickData) -> WorkerResult:
        """
        Resolve the point-in-time signal for this tick and map it to a WorkerResult.

        Looks up the most recent snapshot (collected_msc <= tick), records its
        receive stamp for refresh-window tracking, and delegates field mapping to
        the concrete worker (_build_result). A gap (no snapshot) yields an empty
        result via _build_result(None, tick).

        Args:
            tick: Current tick

        Returns:
            WorkerResult with outputs matching get_output_schema()
        """
        resolved = self._require_provider().nearest(tick.timestamp, self._symbol)
        self._last_snapshot_msc = resolved.collected_msc if resolved else None
        return self._build_result(resolved, tick)

    @abstractmethod
    def _build_result(
        self,
        resolved: Optional[ResolvedSignal],
        tick: TickData
    ) -> WorkerResult:
        """
        Map a resolved signal (or a gap) to this worker's WorkerResult.

        Args:
            resolved: The point-in-time signal, or None on a gap (no snapshot <= tick)
            tick: Current tick (for staleness against collected_msc)

        Returns:
            WorkerResult with outputs matching get_output_schema()
        """
        pass
