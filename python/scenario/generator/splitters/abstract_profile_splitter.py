"""
Abstract Profile Splitter
=========================
Shared scaffolding for the volatility-profile-based splitters (volatility_split / continuous).

Both produce a WindowSet from continuous data regions + volatility periods; they differ only in
how a region is cut into windows (`_build_windows`). The region extraction, volatility-period
fetch, metadata assembly and summary printing live here once.
"""

from abc import abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.discoveries.data_coverage.data_coverage_report_cache import DataCoverageReportCache
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer_cache import VolatilityProfileAnalyzerCache
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityPeriod,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationStrategy,
    ProfileStrategyConfig,
)
from python.framework.types.scenario_types.window_set_types import (
    GeneratedWindow,
    WindowSet,
    WindowSplitConfig,
)
from python.scenario.generator.splitters.abstract_splitter import AbstractSplitter
from python.scenario.generator.splitters.continuous_region_extractor import ContinuousRegionExtractor

vLog = get_global_logger()


class AbstractProfileSplitter(AbstractSplitter):
    """Base for volatility-profile-driven splitters (ATR-minima + continuous-region)."""

    def __init__(
        self,
        config: ProfileStrategyConfig,
        logger: AbstractLogger = None
    ):
        """
        Initialize profile splitter.

        Args:
            config: Profile strategy configuration
            logger: Logger instance (falls back to global logger)
        """
        self._config = config
        self._logger = logger or vLog
        self._coverage_cache = DataCoverageReportCache(logger=self._logger)
        self._volatility_cache = VolatilityProfileAnalyzerCache(logger=self._logger)
        self._discovery_manager = DiscoveryCacheManager(logger=self._logger)
        self._region_extractor = ContinuousRegionExtractor()

    @abstractmethod
    def _get_strategy(self) -> GenerationStrategy:
        """
        The concrete generation strategy (mode) this splitter implements.

        Returns:
            GenerationStrategy value
        """
        ...

    @abstractmethod
    def _build_windows(
        self,
        regions: List[Dict],
        periods: List[VolatilityPeriod]
    ) -> List[GeneratedWindow]:
        """
        Cut continuous regions into windows (the strategy-specific step).

        Args:
            regions: Continuous data regions
            periods: Volatility periods within the requested range

        Returns:
            List of generated windows
        """
        ...

    def split(
        self,
        broker_type: str,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        count_max: Optional[int] = None,
    ) -> WindowSet:
        """
        Generate a WindowSet for the given symbol and time range.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start_time: Profile start time (UTC, required)
            end_time: Profile end time (UTC, required)
            count_max: Unused (profile splitters cover the full range)

        Returns:
            WindowSet with windows and provenance metadata
        """
        if start_time is None or end_time is None:
            raise ValueError(
                'Profile splitters require start_time and end_time')

        strategy = self._get_strategy()
        mode = strategy.value

        self._logger.info(
            f"Generating profile: {broker_type}/{symbol} "
            f"{start_time.strftime('%Y-%m-%d')} → {end_time.strftime('%Y-%m-%d')} "
            f"[{mode}]"
        )

        # Get continuous data regions (gap-aware, clipped to the requested range)
        regions = self._extract_regions(broker_type, symbol, start_time, end_time)

        if not regions:
            # Provide helpful diagnostics about actual data range
            report = self._coverage_cache.get_report(broker_type, symbol)
            if report and report.start_time and report.end_time:
                raise ValueError(
                    f"No continuous data regions found for {broker_type}/{symbol} "
                    f"in [{start_time} → {end_time}]\n"
                    f"Available data: {report.start_time.strftime('%Y-%m-%d %H:%M')} → "
                    f"{report.end_time.strftime('%Y-%m-%d %H:%M')} UTC (no overlap with requested range)"
                )
            raise ValueError(
                f"No continuous data regions found for {broker_type}/{symbol} "
                f"in [{start_time} → {end_time}]"
            )

        # Get volatility periods for the time range
        vol_profile = self._volatility_cache.get_profile(broker_type, symbol)
        if vol_profile is None:
            raise ValueError(
                f"No volatility profile available for {broker_type}/{symbol}. "
                f"Run discovery cache rebuild first."
            )

        # Filter periods to requested time range
        periods = [
            p for p in vol_profile.periods
            if p.end_time > start_time and p.start_time < end_time
        ]

        if not periods:
            raise ValueError(
                f"No volatility periods found for {broker_type}/{symbol} "
                f"in [{start_time} → {end_time}]"
            )

        # Strategy-specific window building
        windows = self._build_windows(regions, periods)

        # Get discovery fingerprints
        fingerprints = self._discovery_manager.get_fingerprints(broker_type, symbol)
        clean_fingerprints = {k: v for k, v in fingerprints.items() if v is not None}

        split_config = WindowSplitConfig(
            min_block_hours=self._config.min_block_hours,
            max_block_hours=self._config.max_block_hours,
            atr_percentile_threshold=self._config.atr_percentile_threshold,
            split_algorithm=self._config.split_algorithm,
        )

        window_set = WindowSet(
            symbol=symbol,
            broker_type=broker_type,
            strategy=strategy,
            windows=windows,
            generated_at=datetime.now(timezone.utc),
            mode=mode,
            split_config=split_config,
            discovery_fingerprints=clean_fingerprints,
        )

        self._print_generation_summary(window_set, mode)

        return window_set

    # =========================================================================
    # SHARED HELPERS
    # =========================================================================

    def _create_window(
        self,
        index: int,
        start: datetime,
        end: datetime,
        split_reason: str,
        atr: float,
        regime: VolatilityRegime,
        session: TradingSession,
        estimated_ticks: int,
        distance_to_next_block_hours: Optional[float] = None,
    ) -> GeneratedWindow:
        """
        Create a single GeneratedWindow.

        Args:
            index: Window index
            start: Window start time
            end: Window end time
            split_reason: Why the split occurred
            atr: ATR value at split point
            regime: Volatility regime at split
            session: Trading session
            estimated_ticks: Estimated tick count
            distance_to_next_block_hours: Gap to the next window (optional)

        Returns:
            GeneratedWindow instance
        """
        return GeneratedWindow(
            block_index=index,
            start_time=start,
            end_time=end,
            regime=regime,
            session=session,
            estimated_ticks=estimated_ticks,
            atr=round(atr, 6),
            split_reason=split_reason,
            distance_to_next_block_hours=distance_to_next_block_hours,
        )

    def _extract_regions(
        self,
        broker_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict]:
        """
        Extract continuous data regions within the requested time range.

        Uses the same gap policy as the blocks splitter — weekends, holidays, seamless
        and short gaps are all treated as continuous. Only moderate / large gaps cause
        region splits.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start_time: Requested start time
            end_time: Requested end time

        Returns:
            List of region dicts clipped to the requested range
        """
        report = self._coverage_cache.get_report(broker_type, symbol)
        if report is None:
            return []

        return self._region_extractor.extract(report, start_time, end_time)

    def _print_generation_summary(
        self,
        window_set: WindowSet,
        mode: str
    ) -> None:
        """
        Print profile generation summary.

        Args:
            window_set: Generated window set
            mode: Generation mode
        """
        windows = window_set.windows

        print('\n' + '=' * 60)
        print('  Profile Generation Summary')
        print('=' * 60)
        print(f"  Symbol:       {window_set.symbol}")
        print(f"  Broker:       {window_set.broker_type}")
        print(f"  Mode:         {mode}")
        print(f"  Blocks:       {window_set.block_count}")
        print(f"  Coverage:     {window_set.total_coverage_hours:.1f}h")

        if windows:
            first_start = min(w.start_time for w in windows)
            last_end = max(w.end_time for w in windows)
            print(
                f"  Time range:   {first_start.strftime('%Y-%m-%d %H:%M')} → "
                f"{last_end.strftime('%Y-%m-%d %H:%M')}"
            )

            # Block size stats
            durations = [w.block_duration_hours for w in windows]
            print(f"  Block sizes:  {min(durations):.1f}h — {max(durations):.1f}h "
                  f"(avg {sum(durations)/len(durations):.1f}h)")

            # Split reason breakdown
            reasons: Dict[str, int] = {}
            for w in windows:
                reasons[w.split_reason] = reasons.get(w.split_reason, 0) + 1
            reason_str = ', '.join(f"{k}: {v}" for k, v in sorted(reasons.items()))
            print(f"  Split reasons: {reason_str}")

            # Regime distribution
            regimes: Dict[str, int] = {}
            for w in windows:
                r_name = w.regime.value
                regimes[r_name] = regimes.get(r_name, 0) + 1
            regime_str = ', '.join(f"{k}: {v}" for k, v in sorted(regimes.items()))
            print(f"  Regimes:      {regime_str}")

        # Fingerprints
        if window_set.discovery_fingerprints:
            print(f"  Fingerprints: {len(window_set.discovery_fingerprints)} discovery caches")
        else:
            print('  Fingerprints: (none)')

        print('=' * 60 + '\n')
