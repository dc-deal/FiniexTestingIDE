"""
Market Analyzer
===============
Analyzes bar data for volatility regimes, tick density, and session patterns.
Used by scenario generator to select optimal time periods.

Location: python/framework/discoveries/market_analyzer.py
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import math

import numpy as np
import pandas as pd

from python.configuration.analysis_config_loader import AnalysisConfigLoader
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.types.market_config_types import MarketType
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.utils.activity_volume_provider import get_activity_provider
from python.framework.types.scenario_generator_types import (
    GeneratorConfig,
    PeriodAnalysis,
    SessionSummary,
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.broker_types import SymbolSpecification
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.market_session_utils import get_session_from_utc_hour

vLog = get_global_logger()


class MarketAnalyzer:
    """
    Analyzes market data for scenario generation.

    Performs on-the-fly analysis of bar data to classify time periods
    by volatility regime, tick density, and trading session.

    Filters out synthetic-only periods (weekends, gaps) automatically.
    """

    def __init__(
        self,
        data_dir: str = "./data/processed"
    ):
        """
        Initialize market analyzer.

        Args:
            data_dir: Path to processed data directory
        """
        analysis_config = AnalysisConfigLoader()
        self._config = analysis_config.get_generator_config()
        self._activity_provider = get_activity_provider()

        # Initialize bar index
        self._bar_index = BarsIndexManager()
        self._bar_index.build_index()

        # Initialize tick index for coverage reports
        self._tick_index = TickIndexManager()
        self._tick_index.build_index()

        # Symbol specification cache (lazy loaded per broker_type)
        self._market_symbol_specs: Dict[str, SymbolSpecification] = {}
        self._loaded_broker_types: set = set()

        # MarketConfigManager for broker paths
        self._market_config = MarketConfigManager()

    def get_config(self) -> GeneratorConfig:
        """Get current configuration."""
        return self._config

    def _load_broker_config_for(self, broker_type: str) -> None:
        """
        Load broker configuration for specific broker_type only (lazy).

        Uses MarketConfigManager to get broker_config_path.
        Only loads each broker_type once.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
        """
        if broker_type in self._loaded_broker_types:
            return  # Already loaded

        try:
            broker_path = self._market_config.get_broker_config_path(
                broker_type)
            broker_config = BrokerConfigFactory.build_broker_config(
                broker_path)

            symbols = broker_config.get_all_aviable_symbols()
            for symbol in symbols:
                try:
                    spec = broker_config.get_symbol_specification(symbol)
                    self._market_symbol_specs[symbol] = spec
                except Exception as e:
                    vLog.debug(f"Could not load spec for {symbol}: {e}")

            self._loaded_broker_types.add(broker_type)
            vLog.debug(f"Loaded {len(symbols)} symbols from {broker_type}")

        except Exception as e:
            vLog.warning(
                f"Failed to load broker config for {broker_type}: {e}")

    def _calculate_pips_per_day(
        self,
        symbol: str,
        avg_absolute_atr: float
    ) -> Optional[float]:
        """
        Calculate average pips per day from ATR.

        Args:
            symbol: Trading symbol
            avg_absolute_atr: Average absolute ATR value

        Returns:
            Pips per day or None if symbol spec not found
        """
        if symbol not in self._market_symbol_specs:
            return None

        spec = self._market_symbol_specs[symbol]

        # Pip size: for 5-digit broker, pip = tick_size * 10
        # For 3-digit (JPY pairs), pip = tick_size * 10
        if spec.digits == 5 or spec.digits == 3:
            pip_size = spec.tick_size * 10
        else:
            pip_size = spec.tick_size

        if pip_size <= 0:
            return None

        # Calculate pips per day using sqrt scaling
        timeframe_minutes = TimeframeConfig.get_minutes(
            self._config.analysis.timeframe)
        bars_per_day = (24 * 60) / timeframe_minutes
        daily_atr = avg_absolute_atr * math.sqrt(bars_per_day)

        return daily_atr / pip_size

    # =========================================================================
    # MAIN ANALYSIS
    # =========================================================================

    def analyze_symbol(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> SymbolAnalysis:
        """
        Perform complete market analysis for a symbol.

        Automatically filters out periods without real data (weekends, gaps).

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol (e.g., 'EURUSD')
            timeframe: Timeframe to analyze (default from config)

        Returns:
            SymbolAnalysis with all metrics and period classifications
        """
        tf = timeframe or self._config.analysis.timeframe

        # Lazy load broker config for this broker_type only
        self._load_broker_config_for(broker_type)

        # Get bar file path from index
        bar_file = self._bar_index.get_bar_file(broker_type, symbol, tf)
        if not bar_file:
            raise ValueError(
                f"No bar data found for {broker_type}/{symbol} {tf}")

        # Get index metadata
         # Get index metadata
        index_entry = self._bar_index.index[broker_type][symbol][tf]
        data_source = index_entry.get('broker_type', broker_type)

        # Get market_type from MarketConfigManager (Single Source of Truth)
        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(broker_type)

        vLog.info(
            f"Analyzing {broker_type}/{symbol} {tf} ({market_type.value})")

        # Load and prepare bar data (using refactored helper)
        df = self._load_and_prepare_bars(broker_type, symbol, tf)

        # Group into analysis periods (filters synthetic-only periods)
        periods = self._analyze_periods(df, market_type)

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

        # Calculate ATR% for cross-instrument comparison
        # ATR% = (ATR * sqrt(bars_per_day) / Close) * 100
        # Volatility scales with sqrt of time
        last_close = df['close'].iloc[-1] if len(df) > 0 else 1.0
        avg_absolute_atr = df['atr'].mean() if 'atr' in df.columns else 0.0
        timeframe_minutes = TimeframeConfig.get_minutes(tf)
        bars_per_day = (24 * 60) / timeframe_minutes

        # Calculate total activity using ActivityVolumeProvider
        activity_column = self._activity_provider.get_metric_name(
            market_type)
        total_activity = float(df[activity_column].sum()
                               ) if activity_column in df.columns else 0.0

        atr_percent = (avg_absolute_atr * math.sqrt(bars_per_day) /
                       last_close) * 100 if last_close > 0 else 0.0

        # Calculate pips per day (if symbol spec available)
        avg_pips_per_day = self._calculate_pips_per_day(
            symbol, avg_absolute_atr)

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
            atr_percent=atr_percent,
            total_activity=total_activity,
            avg_pips_per_day=avg_pips_per_day,
            regime_distribution=regime_dist,
            regime_percentages={
                regime: count / len(periods) * 100
                for regime, count in regime_dist.items()
            },
            session_summaries=session_summaries,
            periods=periods
        )

    def get_periods(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> List[PeriodAnalysis]:
        """
        Get gap-filtered periods for scenario generation.

        Returns only periods with real bar data (no synthetic-only periods).
        Periods are regime-classified and gap-aware.

        Public accessor for stress/custom scenario generators.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe (default from config)

        Returns:
            List of PeriodAnalysis with regime classification
        """
        tf = timeframe or self._config.analysis.timeframe

        vLog.debug(f"Extracting periods for {broker_type}/{symbol} {tf}")

        df = self._load_and_prepare_bars(broker_type, symbol, tf)
        # Get market_type from MarketConfigManager (Single Source of Truth)
        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(broker_type)
        periods = self._analyze_periods(df, market_type)

        if not periods:
            raise ValueError(
                f"No valid trading periods found for {broker_type}/{symbol} {tf}. "
                f"All periods may be synthetic-only (weekends/gaps)."
            )

        vLog.debug(f"Found {len(periods)} valid periods (real-bar filtered)")

        return periods

    def get_stress_periods(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None,
        regimes: Optional[List[VolatilityRegime]] = None
    ) -> List[PeriodAnalysis]:
        """
        Get high-volatility periods for stress testing.

        Convenience method that filters for HIGH/VERY_HIGH regimes
        and sorts by tick activity.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe (default from config)
            regimes: Regimes to include (default: [HIGH, VERY_HIGH])

        Returns:
            Filtered periods sorted by tick_count (highest first)
        """
        all_periods = self.get_periods(broker_type, symbol, timeframe)

        if regimes is None:
            regimes = [VolatilityRegime.HIGH, VolatilityRegime.VERY_HIGH]

        stress_periods = [p for p in all_periods if p.regime in regimes]

        # Sort by tick count (highest activity first)
        stress_periods = sorted(
            stress_periods,
            key=lambda p: p.tick_count,
            reverse=True
        )

        vLog.info(
            f"Found {len(stress_periods)} stress periods "
            f"(HIGH/VERY_HIGH) from {len(all_periods)} total"
        )

        return stress_periods

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _load_and_prepare_bars(
        self,
        broker_type: str,
        symbol: str,
        timeframe: str
    ) -> pd.DataFrame:
        """
        Load and prepare bar data for analysis.

        Shared helper for analyze_symbol() and get_periods().

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe string

        Returns:
            Prepared dataframe with ATR calculated
        """
        bar_file = self._bar_index.get_bar_file(broker_type, symbol, timeframe)
        if not bar_file:
            raise ValueError(
                f"No bar data found for {broker_type}/{symbol} {timeframe}")

        df = pd.read_parquet(bar_file)
        df = self._prepare_dataframe(df)
        df = self._calculate_atr(df)

        return df

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
        market_type: MarketType
    ) -> List[PeriodAnalysis]:
        """
        Analyze data grouped by time periods.

        Filters out periods without real data (synthetic-only from weekends/gaps).

        Args:
            df: Prepared bar dataframe
            symbol: Trading symbol
            market_type: MarketType enum for activity calculation 

        Returns:
            List of PeriodAnalysis objects (only valid trading periods)
        """
        granularity = self._config.analysis.regime_granularity_hours

        activity_column = self._activity_provider.get_metric_name(market_type)

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
            activity = float(group[activity_column].sum(
            )) if activity_column in group.columns else 0.0

            if real_bars == 0 or tick_count == 0:
                # Skip synthetic-only periods (weekends, gaps)
                continue

            avg_atr = group['atr'].mean()
            valid_period_data.append({
                'period_start': period_start,
                'group': group,
                'avg_atr': avg_atr,
                'tick_count': tick_count,
                'activity': activity,
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
            activity = period_data['activity']
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
                activity=activity,
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
                total_activity=sum(p.activity for p in session_periods),
                avg_tick_density=np.mean(densities),
                min_tick_density=min(densities),
                max_tick_density=max(densities),
                regime_distribution=regime_dist
            )

        return summaries

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def list_symbols(self, broker_type: Optional[str] = None) -> List[str]:
        """
        List available symbols in bar index.

        Args:
            broker_type: If provided, list symbols for this broker_type only

        Returns:
            Sorted list of symbol names
        """
        return self._bar_index.list_symbols(broker_type)

    def get_available_timeframes(self, broker_type: str, symbol: str) -> List[str]:
        """
        Get available timeframes for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol

        Returns:
            List of timeframe strings
        """
        return self._bar_index.get_available_timeframes(broker_type, symbol)

    def get_index_entry(
        self,
        broker_type: str,
        symbol: str,
        timeframe: str
    ) -> Optional[Dict]:
        """
        Get bar index entry for symbol/timeframe.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe string

        Returns:
            Index entry dict or None
        """
        if broker_type not in self._bar_index.index:
            return None
        if symbol not in self._bar_index.index[broker_type]:
            return None
        return self._bar_index.index[broker_type][symbol].get(timeframe)
