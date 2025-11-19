"""
Market Analyzer
===============
Analyzes bar data for volatility regimes, tick density, and session patterns.
Used by scenario generator to select optimal time periods.

Location: python/scenario/market_analyzer.py
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json

import numpy as np
import pandas as pd

from python.data_worker.data_loader.parquet_bars_index import ParquetBarsIndexManager
from python.data_worker.data_loader.parquet_index import ParquetIndexManager
from python.framework.utils.activity_volume_provider import get_activity_provider
from python.framework.types.scenario_generator_types import (
    AnalysisConfig,
    GeneratorConfig,
    PeriodAnalysis,
    SessionSummary,
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.components.logger.bootstrap_logger import get_logger
from python.framework.utils.market_session_utils import get_session_from_utc_hour

vLog = get_logger()


class MarketAnalyzer:
    """
    Analyzes market data for scenario generation.

    Performs on-the-fly analysis of bar data to classify time periods
    by volatility regime, tick density, and trading session.

    Filters out synthetic-only periods (weekends, gaps) automatically.
    """

    def __init__(
        self,
        data_dir: str = "./data/processed",
        config_path: Optional[str] = None
    ):
        """
        Initialize market analyzer.

        Args:
            data_dir: Path to processed data directory
            config_path: Path to analysis config JSON (optional)
        """
        self._data_dir = Path(data_dir)
        self._config = self._load_config(config_path)
        self._activity_provider = get_activity_provider()

        # Initialize bar index
        self._bar_index = ParquetBarsIndexManager(self._data_dir)
        self._bar_index.build_index()

        # Initialize tick index for coverage reports
        self._tick_index = ParquetIndexManager(self._data_dir)
        self._tick_index.build_index()

    def _load_config(self, config_path: Optional[str]) -> GeneratorConfig:
        """
        Load configuration from JSON file.

        Args:
            config_path: Path to config file

        Returns:
            GeneratorConfig instance
        """
        default_path = Path("./configs/generator/analysis_config.json")

        if config_path:
            path = Path(config_path)
        elif default_path.exists():
            path = default_path
        else:
            vLog.info("Using default analysis configuration")
            return GeneratorConfig.from_dict({})

        try:
            with open(path, 'r') as f:
                data = json.load(f)
            vLog.info(f"Loaded analysis config from {path}")
            return GeneratorConfig.from_dict(data)
        except Exception as e:
            vLog.warning(f"Failed to load config from {path}: {e}")
            return GeneratorConfig.from_dict({})

    def get_config(self) -> GeneratorConfig:
        """Get current configuration."""
        return self._config

    # =========================================================================
    # MAIN ANALYSIS
    # =========================================================================

    def analyze_symbol(
        self,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> SymbolAnalysis:
        """
        Perform complete market analysis for a symbol.

        Automatically filters out periods without real data (weekends, gaps).

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            timeframe: Timeframe to analyze (default from config)

        Returns:
            SymbolAnalysis with all metrics and period classifications
        """
        tf = timeframe or self._config.analysis.timeframe

        # Get bar file path from index
        bar_file = self._bar_index.get_bar_file(symbol, tf)
        if not bar_file:
            raise ValueError(f"No bar data found for {symbol} {tf}")

        # Get index metadata
        index_entry = self._bar_index.index[symbol][tf]
        market_type = index_entry.get('market_type', 'forex_cfd')
        data_source = index_entry.get('data_source', 'mt5')

        vLog.info(f"Analyzing {symbol} {tf} ({market_type})")

        # Load bar data
        df = pd.read_parquet(bar_file)
        df = self._prepare_dataframe(df)

        # Calculate ATR for all bars
        df = self._calculate_atr(df)

        # Group into analysis periods (filters synthetic-only periods)
        periods = self._analyze_periods(df, symbol)

        if not periods:
            raise ValueError(f"No valid trading periods found for {symbol}")

        # Calculate regime distribution
        regime_dist = self._calculate_regime_distribution(periods)

        # Calculate session summaries
        session_summaries = self._calculate_session_summaries(periods)

        # Overall statistics (only from valid periods)
        start_time = min(p.start_time for p in periods)
        end_time = max(p.end_time for p in periods)
        total_days = (end_time - start_time).days

        atr_values = [p.atr for p in periods if p.atr > 0]

        # Calculate filtered vs total periods for coverage info
        total_bars = len(df)
        real_bars = len(df[df['bar_type'] == 'real'])

        atr_min = min(atr_values) if atr_values else 0.0
        atr_max = max(atr_values) if atr_values else 0.0

        return SymbolAnalysis(
            symbol=symbol,
            timeframe=tf,
            market_type=market_type,
            data_source=data_source,
            start_time=start_time,
            end_time=end_time,
            total_days=total_days,
            total_bars=total_bars,
            total_ticks=int(df['tick_count'].sum()),
            real_bar_ratio=real_bars / total_bars if total_bars > 0 else 0,
            atr_min=atr_min,
            atr_max=atr_max,
            atr_avg=(atr_min + atr_max) / 2,
            atr_std=np.std(atr_values) if atr_values else 0.0,
            regime_distribution=regime_dist,
            regime_percentages={
                regime: count / len(periods) * 100
                for regime, count in regime_dist.items()
            },
            session_summaries=session_summaries,
            periods=periods
        )

    # =========================================================================
    # DATA PREPARATION
    # =========================================================================

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare dataframe for analysis.

        Args:
            df: Raw bar dataframe

        Returns:
            Prepared dataframe with UTC timestamps
        """
        # Ensure timestamp is datetime with UTC
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('UTC')

        # Sort by timestamp
        df = df.sort_values('timestamp').reset_index(drop=True)

        # Ensure required columns
        if 'tick_count' not in df.columns:
            df['tick_count'] = 1
        if 'bar_type' not in df.columns:
            df['bar_type'] = 'real'

        # Add session column
        df['session'] = df['timestamp'].dt.hour.apply(
            get_session_from_utc_hour)

        return df

    # =========================================================================
    # ATR CALCULATION
    # =========================================================================

    def _calculate_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Average True Range for bars.

        Args:
            df: Bar dataframe with OHLC

        Returns:
            Dataframe with 'atr' column added
        """
        period = self._config.analysis.atr_period

        # True Range components
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))

        # True Range = max of the three
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        # ATR = EMA of True Range
        df['atr'] = tr.ewm(span=period, adjust=False).mean()

        return df

    # =========================================================================
    # PERIOD ANALYSIS
    # =========================================================================

    def _analyze_periods(
        self,
        df: pd.DataFrame,
        symbol: str
    ) -> List[PeriodAnalysis]:
        """
        Analyze data grouped by time periods.

        Filters out periods without real data (synthetic-only from weekends/gaps).

        Args:
            df: Prepared bar dataframe
            symbol: Trading symbol

        Returns:
            List of PeriodAnalysis objects (only valid trading periods)
        """
        granularity = self._config.analysis.regime_granularity_hours

        # Group by period (e.g., hourly)
        df['period'] = df['timestamp'].dt.floor(f'{granularity}h')

        periods = []
        grouped = df.groupby('period')

        # First pass: collect all valid periods to calculate avg ATR
        valid_period_data = []

        for period_start, group in grouped:
            if len(group) == 0:
                continue

            # Filter: skip periods without real data
            real_bars = len(group[group['bar_type'] == 'real'])
            tick_count = int(group['tick_count'].sum())

            if real_bars == 0 or tick_count == 0:
                # Skip synthetic-only periods (weekends, gaps)
                continue

            avg_atr = group['atr'].mean()
            valid_period_data.append({
                'period_start': period_start,
                'group': group,
                'avg_atr': avg_atr,
                'tick_count': tick_count,
                'real_bars': real_bars
            })

        if not valid_period_data:
            return []

        # Calculate average ATR for relative classification
        all_atrs = [p['avg_atr'] for p in valid_period_data]
        avg_atr_global = np.mean(all_atrs)

        # Second pass: classify periods
        for period_data in valid_period_data:
            period_start = period_data['period_start']
            group = period_data['group']
            avg_atr = period_data['avg_atr']
            tick_count = period_data['tick_count']
            real_bars = period_data['real_bars']

            period_end = period_start + timedelta(hours=granularity)

            # Get dominant session
            session_str = group['session'].mode().iloc[0]
            session = TradingSession(session_str)

            # Metrics
            tick_density = tick_count / granularity

            # Regime classification (relative to average ATR)
            # Uses relative thresholds for consistent classification across symbols.
            ratio = avg_atr / avg_atr_global
            if avg_atr == 0:
                regime = VolatilityRegime.VERY_LOW
            regime = self._classify_regime(ratio)

            # Calculate percentile for reference
            atr_percentile = self._get_percentile(avg_atr, np.array(all_atrs))

            # Bar statistics
            bar_count = len(group)
            synthetic_bars = len(group[group['bar_type'] == 'synthetic'])

            # Price range
            high = group['high'].max()
            low = group['low'].min()
            range_pips = (high - low) * 10000  # Assuming 4-digit pairs

            periods.append(PeriodAnalysis(
                start_time=period_start.to_pydatetime(),
                end_time=period_end.to_pydatetime(),
                session=session,
                atr=ratio,
                atr_percentile=atr_percentile,
                regime=regime,
                tick_count=tick_count,
                tick_density=tick_density,
                bar_count=bar_count,
                real_bar_count=real_bars,
                synthetic_bar_count=synthetic_bars,
                high=high,
                low=low,
                range_pips=range_pips
            ))

        return periods

    def _get_percentile(self, value: float, all_values: np.ndarray) -> float:
        """
        Get percentile rank of a value.

        Args:
            value: Value to rank
            all_values: All values for comparison

        Returns:
            Percentile (0-100)
        """
        if len(all_values) == 0:
            return 0.0
        return (all_values < value).sum() / len(all_values) * 100

    def _classify_regime(self, ratio: float) -> VolatilityRegime:
        """
        Classify volatility regime based on ratio to average ATR.

        Uses relative thresholds for consistent classification across symbols.

        Returns:
            VolatilityRegime enum
        """

        # Relative thresholds
        thresholds = self._config.analysis.regime_thresholds

        if ratio < thresholds[0]:
            return VolatilityRegime.VERY_LOW
        elif ratio < thresholds[1]:
            return VolatilityRegime.LOW
        elif ratio < thresholds[2]:
            return VolatilityRegime.MEDIUM
        elif ratio < thresholds[3]:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.VERY_HIGH

    # =========================================================================
    # AGGREGATIONS
    # =========================================================================

    def _calculate_regime_distribution(
        self,
        periods: List[PeriodAnalysis]
    ) -> Dict[VolatilityRegime, int]:
        """
        Calculate distribution of volatility regimes.

        Args:
            periods: List of period analyses

        Returns:
            Dict mapping regime to count
        """
        distribution = {regime: 0 for regime in VolatilityRegime}

        for period in periods:
            distribution[period.regime] += 1

        return distribution

    def _calculate_session_summaries(
        self,
        periods: List[PeriodAnalysis]
    ) -> Dict[TradingSession, SessionSummary]:
        """
        Calculate summary statistics per trading session.

        Args:
            periods: List of period analyses

        Returns:
            Dict mapping session to summary
        """
        summaries = {}

        for session in TradingSession:
            session_periods = [p for p in periods if p.session == session]

            if not session_periods:
                continue

            atrs = [p.atr for p in session_periods]
            densities = [p.tick_density for p in session_periods]

            # Regime distribution for this session
            regime_dist = {regime: 0 for regime in VolatilityRegime}
            for period in session_periods:
                regime_dist[period.regime] += 1

            summaries[session] = SessionSummary(
                session=session,
                period_count=len(session_periods),
                avg_atr=np.mean(atrs),
                min_atr=min(atrs),
                max_atr=max(atrs),
                total_ticks=sum(p.tick_count for p in session_periods),
                avg_tick_density=np.mean(densities),
                min_tick_density=min(densities),
                max_tick_density=max(densities),
                regime_distribution=regime_dist
            )

        return summaries

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def list_symbols(self) -> List[str]:
        """
        List available symbols in bar index.

        Returns:
            Sorted list of symbol names
        """
        return self._bar_index.list_symbols()

    def get_available_timeframes(self, symbol: str) -> List[str]:
        """
        Get available timeframes for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of timeframe strings
        """
        return self._bar_index.get_available_timeframes(symbol)

    def get_index_entry(self, symbol: str, timeframe: str) -> Optional[Dict]:
        """
        Get bar index entry for symbol/timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe string

        Returns:
            Index entry dict or None
        """
        if symbol in self._bar_index.index:
            return self._bar_index.index[symbol].get(timeframe)
        return None
