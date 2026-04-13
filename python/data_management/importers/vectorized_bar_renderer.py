"""
Vectorized Bar Renderer - Fast Batch Bar Generation
===================================================

Renders bars from tick data using pandas vectorized operations.
10-100x faster than tick-by-tick rendering for batch processing.

Key Features:
- Pandas resample() for all timeframes in parallel
- Gaps are skipped (standard broker behavior — no bars for periods without ticks)
- Consistent data types (float64, matching BarRenderer)
"""

from typing import Dict, Optional

import pandas as pd

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.timeframe_config_utils import TimeframeConfig
vLog = get_global_logger()


class VectorizedBarRenderer:
    """
    Fast vectorized bar rendering for batch processing.

    Uses pandas.resample() instead of tick-by-tick loops.
    Perfect for pre-rendering bars from tick data.

    Gaps (periods without ticks) are simply skipped — no synthetic
    fill bars are generated. This matches standard broker behavior
    (MT5, cTrader) and is consistent with the live BarRenderer.
    """

    def __init__(
        self,
        symbol: str,
        broker_type: str,
        log_buffer: Optional[list[str]] = None
    ):
        """
        Initialize renderer for a specific symbol.

        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            log_buffer: Optional buffer for log messages (parallel rendering)
        """
        self.symbol = symbol
        self._broker_type = broker_type
        self._log_buffer = log_buffer
        # Pandas resample() rules for each timeframe
        self._resample_rules = {
            tf: TimeframeConfig.get_resample_rule(tf)
            for tf in TimeframeConfig.sorted()
        }

    def _log(self, level: str, message: str) -> None:
        """
        Route log output to buffer or global logger.

        Args:
            level: Log level ('info', 'debug', 'warning')
            message: Log message
        """
        if self._log_buffer is not None:
            self._log_buffer.append(message)
        elif level == 'debug':
            vLog.debug(message)
        elif level == 'warning':
            vLog.warning(message)
        else:
            vLog.info(message)

    def render_all_timeframes(
        self,
        ticks_df: pd.DataFrame
    ) -> Dict[str, pd.DataFrame]:
        """
        Render bars for all supported timeframes from tick data.

        This is the main entry point - renders all 7 timeframes in one go!

        Args:
            ticks_df: DataFrame with tick data (must have 'timestamp', 'bid', 'ask')

        Returns:
            Dict[timeframe, DataFrame] - Bars for each timeframe
        """
        self._log('info',
            f"🔧 Rendering bars for {self.symbol} from {len(ticks_df):,} ticks")

        # Prepare tick data for resampling
        prepared_df = self._prepare_ticks_for_resampling(ticks_df)

        # Render all timeframes
        all_bars = {}
        for timeframe in self._resample_rules.keys():
            self._log('debug', f"  ├─ Rendering {timeframe}...")
            bars_df = self._render_single_timeframe(prepared_df, timeframe)
            all_bars[timeframe] = bars_df
            self._log('info', f"  ├─ {timeframe}: {len(bars_df):,} bars rendered")

        self._log('info', f"✅ All timeframes rendered for {self.symbol}")
        return all_bars

    def _prepare_ticks_for_resampling(self, ticks_df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare tick DataFrame for pandas resample().

        Steps:
        1. Calculate mid-price from bid/ask
        2. Ensure timestamp is datetime (critical!)
        3. Set timestamp as index
        4. Sort chronologically

        Args:
            ticks_df: Raw tick DataFrame

        Returns:
            Prepared DataFrame ready for resample()
        """
        df = ticks_df.copy()

        # === 0. VALIDATE REQUIRED COLUMNS ===
        if 'volume' not in df.columns:
            raise ValueError(
                "Missing 'volume' column in tick data. "
                "Use read_tick_parquet() to load with normalized columns."
            )

        # === 1. CALCULATE MID-PRICE ===
        # We use (bid + ask) / 2 for bar OHLC
        # This is standard in algo trading - most strategies use mid-price
        df['mid'] = (df['bid'] + df['ask']) / 2.0

        # === 2. ENSURE DATETIME ===
        # CRITICAL: timestamp must be datetime for resample()
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Ensure timezone awareness (UTC)
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')

        # === 3. SET AS INDEX ===
        # resample() requires timestamp as index
        df = df.set_index('timestamp')

        # === 4. SORT ===
        df = df.sort_index()

        return df

    def _render_single_timeframe(
        self,
        prepared_df: pd.DataFrame,
        timeframe: str
    ) -> pd.DataFrame:
        """
        Render bars for a single timeframe using pandas resample().

        Periods without ticks are skipped (no bars generated).
        This matches standard broker behavior.

        Args:
            prepared_df: Tick DataFrame (with mid-price, indexed by timestamp)
            timeframe: Timeframe to render (e.g., 'M5')

        Returns:
            DataFrame with rendered bars (only periods with actual ticks)
        """
        # Get resample rule for this timeframe
        rule = self._resample_rules[timeframe]

        # === PANDAS RESAMPLE MAGIC ===
        # This single operation replaces 10,000 loop iterations!
        # resample() groups ticks by time periods and aggregates them

        # Build aggregation dict
        # volume: actual trade volume (crypto) or 0.0 (forex CFD)
        agg_dict = {
            'mid': ['first', 'max', 'min', 'last'],  # OHLC from mid-price
            'bid': 'count',                          # Tick count
            'volume': 'sum'                          # Trade volume
        }

        bars = prepared_df.resample(rule).agg(agg_dict)

        # Flatten multi-level column names
        bars.columns = ['open', 'high', 'low', 'close', 'tick_count', 'volume']

        # Drop bars with no ticks (NaN rows from gaps)
        real_bars = bars.dropna(subset=['open'])

        # Build proper bar DataFrame
        bar_df = pd.DataFrame({
            'timestamp': real_bars.index,
            'symbol': self.symbol,
            'timeframe': timeframe,
            'open': real_bars['open'].astype('float64'),
            'high': real_bars['high'].astype('float64'),
            'low': real_bars['low'].astype('float64'),
            'close': real_bars['close'].astype('float64'),
            'volume': real_bars['volume'].astype('float64'),
            'tick_count': real_bars['tick_count'].astype('int32'),
        })

        # Reset index to get timestamp as column
        bar_df = bar_df.reset_index(drop=True)

        return bar_df
