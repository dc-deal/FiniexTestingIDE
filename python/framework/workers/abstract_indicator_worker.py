"""
FiniexTestingIDE - Abstract Indicator Worker
Base class for bar-centric INDICATOR workers (RSI, Bollinger, MACD, ...)
"""

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Set

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import ComputeBasis, WorkerResult
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.workers.abstract_worker import AbstractWorker


class AbstractIndicatorWorker(AbstractWorker):
    """
    Base class for INDICATOR workers — synchronous computation from bar data.

    Carries the bar-centric machinery: the 'periods' warmup contract, the
    compute basis (#420), the effective-bars window, and the tick-driven
    recompute cadence. The cross-type contract (identity, output schema,
    subscription, metadata) lives on AbstractWorker.
    """

    def __init__(
        self,
        name: str,
        logger,
        parameters=None,
        trading_context=None
    ):
        """
        Initialize indicator worker.

        Args:
            name: Worker name/identifier
            logger: ScenarioLogger instance (REQUIRED)
            parameters: ValidatedParameters or dict (auto-wrapped)
            trading_context: TradingContext (optional)
        """
        super().__init__(
            name=name, logger=logger,
            parameters=parameters, trading_context=trading_context
        )

        # --- Infrastructure: auto-extract 'periods' for INDICATOR workers ---
        # This eliminates the #1 boilerplate pattern across all INDICATOR workers
        # and prevents the OBV bug (missing self.periods) from ever recurring.
        self.periods = self.params.get('periods')

        # Compute basis (#420) — resolved lazily on first use and cached
        # (config 'compute_basis' override → the worker's declaration).
        self._compute_basis: Optional[ComputeBasis] = None

    @abstractmethod
    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Get warmup requirements per timeframe.

        Calculated from instance parameters (e.g., self.period).

        Returns:
            Dict[timeframe, bars_needed]
            Example: {"M5": 20, "M15": 20}
        """
        pass

    @abstractmethod
    def get_required_timeframes(self) -> List[str]:
        """
        Get required timeframes for this worker instance.

        Calculated from instance parameters (e.g., self.timeframe).

        Returns:
            List of timeframe strings
            Example: ["M5"]
        """
        pass

    @abstractmethod
    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """
        Determine if worker should recompute on this tick

        Args:
            tick: Current tick data
            bar_updated: Whether a bar was updated/completed

        Returns:
            True if recomputation needed
        """
        pass

    @abstractmethod
    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Compute worker output based on bar data

        Args:
            tick: Current tick (for metadata/timestamp)
            bar_history: Historical bars per timeframe
            current_bars: Current bars per timeframe

        Returns:
            WorkerResult with outputs dict matching get_output_schema() keys
        """
        pass

    def get_compute_basis(self) -> ComputeBasis:
        """
        Effective compute basis for this worker instance (#420), cached.

        The per-instance config key 'compute_basis' overrides the worker's
        declaration (get_default_compute_basis): the same CORE worker class can be
        LIVE for a tick-reactive strategy and BAR_CLOSE for a bar-grid strategy,
        decided in the run config. Resolved once and cached — no per-tick lookup.

        Returns:
            ComputeBasis governing when/what the orchestrator recomputes this worker
        """
        if self._compute_basis is None:
            configured = self.parameters.get('compute_basis')
            self._compute_basis = (
                ComputeBasis(configured) if configured
                else self.get_default_compute_basis())
        return self._compute_basis

    def effective_bars(
        self,
        timeframe: str,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
        count: Optional[int] = None,
    ) -> List[Bar]:
        """
        Bars this worker computes on for a timeframe (#420).

        Completed history, plus the current (forming) bar when the basis is LIVE
        (intra-bar view); completed-bars-only under BAR_CLOSE (the value changes
        only on a bar close). Centralizes the append that was previously duplicated
        inline in every worker's compute().

        A window-bounded worker passes 'count' — the number of completed bars it
        actually reads — so only that tail is materialized. For a worker that uses
        just its last 'count' bars the result is identical to the full-history path,
        but the per-compute cost drops from O(bar_max_history) to O(count) instead
        of copying / scanning the whole history on every tick.

        Args:
            timeframe: Timeframe key
            bar_history: Completed bars per timeframe
            current_bars: Current (forming) bar per timeframe
            count: Completed-bar window to keep (None = full history)

        Returns:
            List of bars to compute on (history tail, plus the current bar when LIVE)
        """
        bars = bar_history.get(timeframe, [])
        if count is not None:
            bars = bars[-count:]
        if self.get_compute_basis() == ComputeBasis.LIVE:
            current_bar = current_bars.get(timeframe)
            if current_bar:
                bars = (bars if count is not None else list(bars)) + [current_bar]
        return bars

    def get_default_compute_basis(self) -> ComputeBasis:
        """
        Declare the worker's compute basis (#420) — MUST be overridden by every worker.

        Instance method, so the declaration may depend on the worker's own config
        (self.params). Return ComputeBasis.LIVE (per-tick, intra-bar — the tick-native
        default that preserves existing-set determinism) or ComputeBasis.BAR_CLOSE
        (completed bars only, recompute on close — stable + cheap, only for consumers
        that read on the bar-close grid). The per-instance config key 'compute_basis'
        overrides this per run.

        Returns:
            The worker's declared ComputeBasis

        Raises:
            NotImplementedError: If a subclass does not override this method.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must declare get_default_compute_basis(). "
            f"Return ComputeBasis.LIVE for a tick-reactive worker (intra-bar value, "
            f"e.g. band position from tick.mid) or ComputeBasis.BAR_CLOSE for a "
            f"completed-bar indicator read on the bar-close grid. "
            f"See docs/user_guides/worker_naming_doc.md."
        )

    @classmethod
    def _reserved_config_keys(cls) -> Set[str]:
        """
        Non-schema config keys an INDICATOR worker accepts.

        Extends the base set with the per-instance compute-basis opt-in (#420),
        which is meaningful only for bar-centric workers.

        Returns:
            Set of reserved config keys that are not unknown parameters
        """
        reserved = super()._reserved_config_keys()
        reserved.add('compute_basis')
        return reserved

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """
        Validate config: compute basis + required 'periods' field.

        Raises ValueError on an invalid compute_basis or a missing/empty
        'periods' dict.

        Args:
            config: Worker configuration dict

        Raises:
            ValueError: If compute_basis invalid or 'periods' missing/empty
        """
        # Reserved framework key: compute basis (per-instance opt-in, #420)
        compute_basis = config.get('compute_basis')
        if compute_basis is not None:
            valid = [c.value for c in ComputeBasis]
            if compute_basis not in valid:
                raise ValueError(
                    f"Worker '{cls.__name__}': invalid 'compute_basis' "
                    f"'{compute_basis}'. Allowed: {valid}"
                )

        # Generic required-fields check (INDICATOR → 'periods' present)
        super().validate_config(config)

        # Validate 'periods' is not empty for INDICATOR
        periods = config.get('periods')
        if not periods:
            raise ValueError(
                f"INDICATOR worker '{cls.__name__}' requires non-empty "
                f"'periods' dict (e.g. {{'M5': 20}})"
            )

        # Validate timeframe keys inside 'periods'
        for tf in periods.keys():
            # uses our central registry
            TimeframeConfig.normalize(tf)

    @classmethod
    def calculate_requirements(cls, config: Dict[str, Any]) -> Dict[str, int]:
        """
        Calculate warmup requirements from config WITHOUT creating instance.

        This is THE KEY METHOD that eliminates double worker creation:
        - Phase 0: Call this to get requirements (no instance needed)
        - Phase 6: Create actual worker instance for execution

        Override in subclass for custom logic (e.g., MACD max(fast, slow)).

        Args:
            config: Worker configuration dict

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 20, "M30": 50}

        Example:
            >>> config = {"periods": {"M5": 20, "M30": 50}, "deviation": 2.0}
            >>> BollingerWorker.calculate_requirements(config)
            {"M5": 20, "M30": 50}
        """
        return config.get("periods", {})
