"""
FiniexTestingIDE - Scenario Config System
Auto-Generator (FIXED: execution_config separation)
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import timedelta
import pandas as pd

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.framework.types import TestScenario
from python.data_worker.data_loader.core import TickDataLoader

logger = logging.getLogger(__name__)


class ScenarioGenerator:
    """
    Generates test scenarios automatically from available data
    NOW WITH: execution_config separation!
    """

    def __init__(self, data_loader: TickDataLoader):
        """
        Args:
            data_loader: TickDataLoader instance
        """
        self.data_loader = data_loader
        self.analyzer = TickDataAnalyzer(self.data_loader)

    def generate_from_symbol(
        self,
        symbol: str,
        strategy: str = "time_windows",
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for a symbol using different strategies

        Args:
            symbol: Trading symbol
            strategy: Generation strategy:
                - "time_windows": Split into time windows
                - "volatility": High/low volatility periods
                - "sessions": Trading sessions (London, NY, etc.)
            strategy_config: Strategy-specific config (overrides defaults)
            execution_config: Execution-specific config (overrides defaults)
            **kwargs: Strategy-specific parameters

        Returns:
            List of generated TestScenario objects
        """
        if strategy == "time_windows":
            return self._generate_time_windows(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        elif strategy == "volatility":
            return self._generate_volatility_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        elif strategy == "sessions":
            return self._generate_session_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def generate_multi_symbol(
        self,
        symbols: List[str] = None,
        scenarios_per_symbol: int = 3,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for multiple symbols

        Args:
            symbols: List of symbols (None = all available)
            scenarios_per_symbol: Number of scenarios per symbol
            strategy_config: Strategy-specific config
            execution_config: Execution-specific config
            **kwargs: Passed to generation strategy

        Returns:
            List of TestScenario objects
        """

        if symbols is None:
            symbols = self.data_loader.list_available_symbols()

        all_scenarios = []

        for symbol in symbols:
            logger.info(
                f"🔨 Generating {scenarios_per_symbol} scenarios for {symbol}")
            scenarios = self._generate_time_windows(
                symbol,
                num_windows=scenarios_per_symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
            all_scenarios.extend(scenarios)

        logger.info(f"✅ Generated {len(all_scenarios)} scenarios total")
        return all_scenarios

    def _generate_time_windows(
        self,
        symbol: str,
        num_windows: int = 5,
        window_days: int = 2,
        ticks_per_window: int = 1000,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios by splitting data into time windows

        Args:
            symbol: Trading symbol
            num_windows: Number of time windows
            window_days: Days per window
            ticks_per_window: Max ticks per window
            strategy_config: Strategy parameters (NEW!)
            execution_config: Execution parameters (NEW!)
        """
        # Get available date range
        symbol_info = self.analyzer.get_symbol_info(symbol)

        if "error" in symbol_info:
            logger.error(
                f"❌ Cannot generate for {symbol}: {symbol_info['error']}")
            return []

        start_date = pd.to_datetime(symbol_info["date_range"]["start"])
        end_date = pd.to_datetime(symbol_info["date_range"]["end"])

        total_days = (end_date - start_date).days

        if total_days < window_days * num_windows:
            logger.warning(
                f"⚠️  Not enough data for {num_windows} windows of {window_days} days. "
                f"Reducing to {total_days // window_days} windows."
            )
            num_windows = max(1, total_days // window_days)

        scenarios = []
        window_duration = timedelta(days=window_days)

        # ✅ Use provided configs or defaults
        default_strategy = {
            "rsi_period": 14,
            "envelope_period": 20,
            "envelope_deviation": 0.02,
        }

        default_execution = {
            "parallel_workers": None,  # Auto-detect
            "worker_parallel_threshold_ms": 1.0,
            "max_parallel_scenarios": 4,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        }

        final_strategy_config = {**default_strategy, **(strategy_config or {})}
        final_execution_config = {
            **default_execution, **(execution_config or {})}

        for i in range(num_windows):
            window_start = start_date + (i * window_duration)
            window_end = window_start + window_duration

            if window_end > end_date:
                window_end = end_date

            scenario = TestScenario(
                symbol=symbol,
                start_date=window_start.strftime("%Y-%m-%d"),
                end_date=window_end.strftime("%Y-%m-%d"),
                max_ticks=ticks_per_window,
                data_mode="realistic",

                # ✅ Separate configs!
                strategy_config=final_strategy_config.copy(),
                execution_config=final_execution_config.copy(),

                name=f"{symbol}_window_{i+1:02d}"
            )
            scenarios.append(scenario)

        logger.info(
            f"✅ Generated {len(scenarios)} time window scenarios for {symbol}")
        return scenarios

    def _generate_volatility_based(
        self,
        symbol: str,
        high_vol_threshold: float = 0.02,
        max_scenarios: int = 10,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on volatility periods

        TODO: Implement volatility detection
        For now, falls back to time_windows
        """
        logger.warning(
            f"⚠️  Volatility-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=max_scenarios,
            strategy_config=strategy_config,
            execution_config=execution_config,
        )

    def _generate_session_based(
        self,
        symbol: str,
        sessions: List[str] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on trading sessions

        TODO: Implement session-based generation
        For now, falls back to time_windows
        """
        logger.warning(
            f"⚠️  Session-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=3,
            strategy_config=strategy_config,
            execution_config=execution_config,
        )
