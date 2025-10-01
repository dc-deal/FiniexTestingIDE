"""
FiniexTestingIDE - Scenario Config System
Auto-Generator aus Datenmenge
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.types import TestScenario

logger = logging.getLogger(__name__)


class ScenarioGenerator:
    """
    Generates test scenarios automatically from available data
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
            **kwargs: Strategy-specific parameters

        Returns:
            List of generated TestScenario objects
        """
        if strategy == "time_windows":
            return self._generate_time_windows(symbol, **kwargs)
        elif strategy == "volatility":
            return self._generate_volatility_based(symbol, **kwargs)
        elif strategy == "sessions":
            return self._generate_session_based(symbol, **kwargs)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def generate_multi_symbol(
        self,
        symbols: List[str] = None,
        scenarios_per_symbol: int = 3,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for multiple symbols

        Args:
            symbols: List of symbols (None = all available)
            scenarios_per_symbol: Number of scenarios per symbol
            **kwargs: Passed to generation strategy

        Returns:
            List of TestScenario objects
        """

        if symbols is None:
            symbols = self.data_loader.list_available_symbols()

        all_scenarios = []

        for symbol in symbols:
            logger.info(
                f"Generating {scenarios_per_symbol} scenarios for {symbol}")
            scenarios = self._generate_time_windows(
                symbol,
                num_windows=scenarios_per_symbol,
                **kwargs
            )
            all_scenarios.extend(scenarios)

        logger.info(f"Generated {len(all_scenarios)} scenarios total")
        return all_scenarios

    def _generate_time_windows(
        self,
        symbol: str,
        num_windows: int = 5,
        window_days: int = 2,
        ticks_per_window: int = 1000
    ) -> List[TestScenario]:
        """
        Generate scenarios by splitting data into time windows
        """
        # Get available date range
        symbol_info = self.analyzer.get_symbol_info(symbol)

        if "error" in symbol_info:
            logger.error(
                f"Cannot generate for {symbol}: {symbol_info['error']}")
            return []

        start_date = pd.to_datetime(symbol_info["date_range"]["start"])
        end_date = pd.to_datetime(symbol_info["date_range"]["end"])

        total_days = (end_date - start_date).days

        if total_days < window_days * num_windows:
            logger.warning(
                f"Not enough data for {num_windows} windows of {window_days} days. "
                f"Reducing to {total_days // window_days} windows."
            )
            num_windows = max(1, total_days // window_days)

        scenarios = []

        # Split into equal windows
        step = total_days // num_windows

        for i in range(num_windows):
            window_start = start_date + timedelta(days=i * step)
            window_end = window_start + timedelta(days=window_days)

            # Don't exceed available data
            if window_end > end_date:
                window_end = end_date

            scenario = TestScenario(
                symbol=symbol,
                start_date=window_start.strftime("%Y-%m-%d"),
                end_date=window_end.strftime("%Y-%m-%d"),
                max_ticks=ticks_per_window,
                data_mode="realistic",
                strategy_config={
                    "rsi_period": 14,
                    "envelope_period": 20,
                    "envelope_deviation": 0.02,
                    "execution": {
                        "parallel_workers": True,
                        "artificial_load_ms": 5.0,
                    }
                },
                name=f"{symbol}_window_{i+1:02d}"
            )
            scenarios.append(scenario)

        logger.info(
            f"Generated {len(scenarios)} time window scenarios for {symbol}")
        return scenarios

    def _generate_volatility_based(
        self,
        symbol: str,
        high_vol_threshold: float = 0.02,
        low_vol_threshold: float = 0.005,
        max_scenarios: int = 10
    ) -> List[TestScenario]:
        """
        Generate scenarios based on volatility periods

        Requires loading and analyzing data - more expensive!
        """
        logger.info(f"Analyzing volatility for {symbol}...")

        # Load all data
        df = self.data_loader.load_symbol_data(symbol)

        if df.empty:
            logger.error(f"No data for {symbol}")
            return []

        # Calculate rolling volatility (simplified)
        df['returns'] = df['mid'].pct_change()
        df['volatility'] = df['returns'].rolling(window=100).std()

        scenarios = []

        # Find high volatility periods
        high_vol_mask = df['volatility'] > high_vol_threshold
        high_vol_periods = self._find_continuous_periods(df[high_vol_mask])

        for period_df in high_vol_periods[:max_scenarios // 2]:
            start_date = period_df['timestamp'].min()
            end_date = period_df['timestamp'].max()

            scenario = TestScenario(
                symbol=symbol,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                max_ticks=1000,
                data_mode="realistic",
                strategy_config={},
                name=f"{symbol}_high_vol_{len(scenarios)+1}"
            )
            scenarios.append(scenario)

        logger.info(f"Generated {len(scenarios)} volatility-based scenarios")
        return scenarios

    def _generate_session_based(
        self,
        symbol: str,
        sessions: List[str] = None
    ) -> List[TestScenario]:
        """
        Generate scenarios for specific trading sessions
        """
        if sessions is None:
            sessions = ["London", "NewYork", "Tokyo", "Sydney"]

        # This would require session detection in data
        # Simplified implementation
        logger.warning("Session-based generation not fully implemented yet")
        return []

    def _find_continuous_periods(self, df: pd.DataFrame, min_size: int = 500):
        """Helper to find continuous data periods"""
        # Simplified - just return chunks
        periods = []
        chunk_size = min_size

        for i in range(0, len(df), chunk_size):
            chunk = df.iloc[i:i+chunk_size]
            if len(chunk) >= min_size:
                periods.append(chunk)

        return periods
