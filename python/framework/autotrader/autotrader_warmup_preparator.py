"""
FiniexTestingIDE - AutoTrader Warmup Preparator
Loads warmup bars for AutoTrader sessions (mock from parquet, live from API).

Two paths:
- Mock: parquet bar files via BarsIndexManager (same data as backtesting)
- Live: Kraken OHLC REST API (extensible to MT5 via ABC)

Direct Bar object creation — no subprocess serialization round-trip.
"""

from typing import Dict, List, Optional

import pandas as pd

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.connection_errors import ConnectionInadmissibleError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.scenario_requirements import calculate_scenario_requirements
from python.framework.workers.abstract_worker import AbstractWorker


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
        connection_policy: Optional[ConnectionPolicy] = None,
    ) -> None:
        """
        Calculate warmup requirements, load bars, validate, and inject.

        Args:
            config: AutoTrader configuration
            workers: List of worker instances (with get_warmup_requirements())
            bar_controller: BarRenderingController to inject bars into
            connection_policy: Retry ladder for the broker's bar history (#473). Live
                path only; the parquet path reads a local archive
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
        live = config.adapter_type == 'live'
        if live:
            bars_by_tf = self._fetch_bars_from_api(
                config.symbol, warmup_by_tf, connection_policy or ConnectionPolicy()
            )
        else:
            bars_by_tf = self._load_bars_from_parquet(
                broker_type=config.broker_type,
                symbol=config.symbol,
                warmup_by_tf=warmup_by_tf,
            )

        # === Step 4: Validate completeness ===
        self._validate_warmup_bars(bars_by_tf, warmup_by_tf, live)

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

        # Log last warmup bar per timeframe — verifies continuity with first live tick
        for timeframe, bars in bars_by_tf.items():
            if bars:
                last = bars[-1]
                self._logger.verbose(
                    f'📊 Warmup tail {timeframe}: {last.timestamp} | '
                    f'O={last.open:.5f} H={last.high:.5f} '
                    f'L={last.low:.5f} C={last.close:.5f} | '
                    f'Ticks={last.tick_count}'
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
                    f'⚠️  No bar file for {symbol} {timeframe} — '
                    f'warmup skipped for this timeframe'
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
                f'  📊 {timeframe}: {len(bars)}/{warmup_count} bars loaded from parquet'
            )

        return result

    # =========================================================================
    # LIVE PATH — Broker API bar fetching
    # =========================================================================

    def _fetch_bars_from_api(
        self,
        symbol: str,
        warmup_by_tf: Dict[str, int],
        policy: ConnectionPolicy,
    ) -> Dict[str, List[Bar]]:
        """
        Fetch warmup bars from broker API.

        Uses KrakenOhlcBarFetcher for Kraken broker type.
        Extensible to MT5 via ABC pattern (#209).

        The ladder here DEGRADES on give-up even where the broker's policy says abort:
        a short read is reported one level up by _validate_warmup_bars, which knows how
        many bars are missing on which timeframe and can say so. Aborting here would
        replace that with "the endpoint did not answer", which is true and useless.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            warmup_by_tf: Required bars per timeframe
            policy: The broker's connection policy — its numbers, its own give-up rule
                replaced as described above

        Returns:
            Dict[timeframe, List[Bar]]
        """
        fetcher = KrakenOhlcBarFetcher(
            logger=self._logger, request_timeout_s=policy.request_timeout_s)
        ladder = ConnectionLadder(
            name='broker_warmup',
            policy=policy.model_copy(update={'on_give_up': GiveUpAction.DEGRADE}),
            logger=self._logger,
        )
        result: Dict[str, List[Bar]] = {}

        for timeframe, warmup_count in warmup_by_tf.items():
            bars = run_with_ladder(
                lambda tf=timeframe, n=warmup_count: fetcher.fetch_bars(
                    symbol=symbol, timeframe=tf, count=n),
                ladder,
            )
            result[timeframe] = bars or []
            self._logger.debug(
                f'  📊 {timeframe}: {len(result[timeframe])}/{warmup_count} '
                f'bars fetched from API'
            )

        return result


    # =========================================================================
    # DISPLAY LABEL CACHE — built once, read by tick loop + display thread
    # =========================================================================

    def build_display_label_cache(
        self,
        decision_logic: AbstractDecisionLogic,
        workers: List[AbstractWorker],
        sentiment_source: str = '',
    ) -> DisplayLabelCache:
        """
        Build the immutable display label cache from decision logic and
        worker schemas. Called once during startup after warmup injection.

        Decision logic input params with display=True flow into the
        Params: line of the ALGO STATE panel. Worker and decision output
        display_labels shorten raw output keys in the same panel.

        Args:
            decision_logic: Instantiated decision logic (for schema access
                and current param value readback)
            workers: List of instantiated workers for the session
            sentiment_source: Sentiment feed label (#431; '' = no feed)

        Returns:
            Frozen DisplayLabelCache ready to be shared read-only between
            the tick loop and the display thread.
        """
        # Decision logic input params → Params: line (display=True only)
        dl_input_schema = decision_logic.__class__.get_parameter_schema()
        config_param_specs: List[tuple] = []
        for raw_key, param_def in dl_input_schema.items():
            if not param_def.display:
                continue
            display_key = param_def.display_label or raw_key
            config_param_specs.append((raw_key, display_key))

        # Decision logic output labels (only where display_label is set)
        dl_output_schema = decision_logic.__class__.get_output_schema()
        decision_output_labels: Dict[str, str] = {}
        for raw_key, param_def in dl_output_schema.items():
            if param_def.display_label:
                decision_output_labels[raw_key] = param_def.display_label

        # Worker output display keys + labels (per worker instance)
        worker_display_output_keys: Dict[str, tuple] = {}
        worker_output_labels: Dict[str, Dict[str, str]] = {}
        for worker in workers:
            schema = worker.__class__.get_output_schema()
            display_keys = tuple(
                raw_key for raw_key, param_def in schema.items()
                if param_def.display
            )
            if display_keys:
                worker_display_output_keys[worker.name] = display_keys

            labels = {
                raw_key: param_def.display_label
                for raw_key, param_def in schema.items()
                if param_def.display_label
            }
            if labels:
                worker_output_labels[worker.name] = labels

        cache = DisplayLabelCache(
            config_param_specs=tuple(config_param_specs),
            worker_display_output_keys=worker_display_output_keys,
            worker_output_labels=worker_output_labels,
            decision_output_labels=decision_output_labels,
            sentiment_source=sentiment_source,
        )

        self._logger.debug(
            f'🏷️  Display label cache built: '
            f'{len(config_param_specs)} config params, '
            f'{len(worker_display_output_keys)} workers, '
            f'{sum(len(l) for l in worker_output_labels.values())} worker output labels'
        )
        return cache

    def _validate_warmup_bars(
        self,
        bars_by_tf: Dict[str, List[Bar]],
        warmup_by_tf: Dict[str, int],
        live: bool,
    ) -> None:
        """
        Validate that loaded bars meet requirements — warn on replay, REFUSE on live.

        Args:
            bars_by_tf: Loaded bars per timeframe
            warmup_by_tf: Required bars per timeframe
            live: True when the bars came from the broker API

        Raises:
            ConnectionInadmissibleError: live, and the requirement is unmet
        """
        short = {
            timeframe: (len(bars_by_tf.get(timeframe, [])), required)
            for timeframe, required in warmup_by_tf.items()
            if len(bars_by_tf.get(timeframe, [])) < required
        }
        if not short:
            return

        detail = ' · '.join(
            f'{tf}: {actual}/{required}' for tf, (actual, required) in short.items())

        if not live:
            # Mock/replay reads a local archive: a short window is a data question the
            # operator can see and fix, and refusing would block backtest-shaped runs
            # that deliberately start near the edge of their data.
            self._logger.warning(
                f'⚠️  Insufficient warmup bars — {detail} — '
                f'workers may produce unreliable signals until history fills'
            )
            return

        # #473 — live: refuse. A worker with no history still emits a number, that number
        # is wrong, and NOTHING declares it wrong: unlike a stale signal or a stale feed,
        # an empty indicator history has no contract to degrade into. Trading on it is not
        # a reduced run, it is a different one.
        raise ConnectionInadmissibleError(
            f'Warmup requirement unmet: {detail}. The broker\'s bar history could not be '
            f'read, and there is no staleness contract for an empty indicator history — '
            f'refusing to start rather than trading on unreliable worker output.'
        )
