"""
FiniexTestingIDE - AutoTrader Warmup Preparator
Loads warmup bars for AutoTrader sessions (mock from parquet, live from API).

Two paths:
- Mock: parquet bar files via BarsIndexManager (same data as backtesting)
- Live: Kraken OHLC REST API (extensible to MT5 via ABC)

Direct Bar object creation — no subprocess serialization round-trip.
"""

from typing import Dict, List

import pandas as pd

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.scenario_requirements import calculate_scenario_requirements


class AutotraderWarmupPreparator:
    """
    Loads and injects warmup bars for AutoTrader sessions.

    Mock path: reads pre-rendered bar parquet files via BarsIndexManager.
    Live path: fetches bars from broker API (Kraken OHLC).

    Both paths create Bar objects directly and inject via
    bar_renderer.initialize_historical_bars() — no serialization overhead.

    Args:
        logger: ScenarioLogger for status messages
    """

    def __init__(self, logger: ScenarioLogger):
        self._logger = logger

    def prepare_and_inject(
        self,
        config: AutoTraderConfig,
        workers: List,
        bar_controller: BarRenderingController,
    ) -> None:
        """
        Calculate warmup requirements, load bars, validate, and inject.

        Args:
            config: AutoTrader configuration
            workers: List of worker instances (with get_warmup_requirements())
            bar_controller: BarRenderingController to inject bars into
        """
        # === Step 1: Calculate requirements from workers ===
        reqs = calculate_scenario_requirements(workers)
        warmup_by_tf = reqs.warmup_by_timeframe

        if not warmup_by_tf:
            self._logger.debug('⏭️  No warmup requirements from workers')
            return

        self._logger.info(
            f"📊 Warmup requirements: "
            f"{', '.join(f'{tf}:{count}' for tf, count in warmup_by_tf.items())}"
        )

        # === Step 2: Load bars ===
        if config.adapter_type == 'live':
            bars_by_tf = self._fetch_bars_from_api(
                config.symbol, warmup_by_tf
            )
        else:
            bars_by_tf = self._load_bars_from_parquet(
                broker_type=config.broker_type,
                symbol=config.symbol,
                warmup_by_tf=warmup_by_tf,
            )

        # === Step 4: Validate completeness ===
        self._validate_warmup_bars(bars_by_tf, warmup_by_tf)

        # === Step 5: Inject directly into bar renderer ===
        total_bars = 0
        for timeframe, bars in bars_by_tf.items():
            bar_controller.bar_renderer.initialize_historical_bars(
                symbol=config.symbol,
                timeframe=timeframe,
                bars=bars,
            )
            total_bars += len(bars)

        self._logger.info(
            f"✅ Warmup injected: {total_bars} bars "
            f"({', '.join(f'{tf}:{len(bars)}' for tf, bars in bars_by_tf.items())})"
        )

    # =========================================================================
    # MOCK PATH — Parquet bar loading
    # =========================================================================

    def _load_bars_from_parquet(
        self,
        broker_type: str,
        symbol: str,
        warmup_by_tf: Dict[str, int],
    ) -> Dict[str, List[Bar]]:
        """
        Load warmup bars from pre-rendered bar parquet files.

        Takes the last N bars from each parquet file — no time filter.
        Mock sessions use available bar history regardless of tick start time.

        Args:
            broker_type: Broker type identifier (e.g., 'kraken_spot')
            symbol: Trading symbol (e.g., 'BTCUSD')
            warmup_by_tf: Required bars per timeframe

        Returns:
            Dict[timeframe, List[Bar]]
        """
        bar_index = BarsIndexManager(self._logger)
        bar_index.build_index()

        result: Dict[str, List[Bar]] = {}

        for timeframe, warmup_count in warmup_by_tf.items():
            bar_file = bar_index.get_bar_file(broker_type, symbol, timeframe)
            if bar_file is None:
                self._logger.warning(
                    f"⚠️  No bar file for {symbol} {timeframe} — "
                    f"warmup skipped for this timeframe"
                )
                continue

            df = pd.read_parquet(bar_file)

            # Column name fallback (same as SharedDataPreparator)
            if 'timestamp' not in df.columns and 'time' in df.columns:
                df['timestamp'] = df['time']

            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

            # Take last N bars from available history
            warmup_df = df.tail(warmup_count)

            bars = [
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=row['timestamp'].isoformat(),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=float(row['volume']),
                    tick_count=int(row['tick_count']),
                    is_complete=True,
                )
                for _, row in warmup_df.iterrows()
            ]
            result[timeframe] = bars

            self._logger.debug(
                f"  📊 {timeframe}: {len(bars)}/{warmup_count} bars loaded from parquet"
            )

        return result

    # =========================================================================
    # LIVE PATH — Broker API bar fetching
    # =========================================================================

    def _fetch_bars_from_api(
        self,
        symbol: str,
        warmup_by_tf: Dict[str, int],
    ) -> Dict[str, List[Bar]]:
        """
        Fetch warmup bars from broker API.

        Uses KrakenOhlcBarFetcher for Kraken broker type.
        Extensible to MT5 via ABC pattern (#209).

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            warmup_by_tf: Required bars per timeframe

        Returns:
            Dict[timeframe, List[Bar]]
        """
        from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher

        fetcher = KrakenOhlcBarFetcher(logger=self._logger)
        result: Dict[str, List[Bar]] = {}

        for timeframe, warmup_count in warmup_by_tf.items():
            try:
                bars = fetcher.fetch_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    count=warmup_count,
                )
                result[timeframe] = bars
                self._logger.debug(
                    f"  📊 {timeframe}: {len(bars)}/{warmup_count} bars fetched from API"
                )
            except Exception as e:
                self._logger.warning(
                    f"⚠️  API bar fetch failed for {timeframe}: {e}"
                )

        return result


    def _validate_warmup_bars(
        self,
        bars_by_tf: Dict[str, List[Bar]],
        warmup_by_tf: Dict[str, int],
    ) -> None:
        """
        Validate that loaded bars meet requirements. Log warnings for shortfalls.

        Args:
            bars_by_tf: Loaded bars per timeframe
            warmup_by_tf: Required bars per timeframe
        """
        for timeframe, required_count in warmup_by_tf.items():
            actual = len(bars_by_tf.get(timeframe, []))
            if actual < required_count:
                self._logger.warning(
                    f"⚠️  Insufficient warmup bars for {timeframe}: "
                    f"{actual}/{required_count} — "
                    f"workers may produce unreliable signals until history fills"
                )
