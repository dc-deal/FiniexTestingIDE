"""
FiniexTestingIDE - AutoTrader Warmup Preparator
Loads warmup bars for AutoTrader sessions (a mock session from its prepared data package, a
live-adapter session from the venue API).

Two paths:
- Mock: the bars the shared mount prepared — exactly the bars a backtest of the same window warms
  up on, selected and converted by the simulation's own functions
- Live adapter: Kraken OHLC REST API (extensible to MT5 via ABC)
"""

from typing import Dict, Iterable, List, Optional

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.connection_errors import ConnectionInadmissibleError
from python.framework.exceptions.timeframe_errors import UnsupportedTimeframeError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.market_types.market_data_types import Bar
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.process_serialization_utils import deserialize_bars_batch
from python.framework.utils.scenario_requirements import calculate_scenario_requirements
from python.framework.workers.abstract_worker import AbstractWorker


class AutotraderWarmupPreparator:
    """
    Loads and injects warmup bars for AutoTrader sessions.

    Mock path: the warmup bars in the session's prepared data package.
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
        package: Optional[ProcessDataPackage] = None,
    ) -> None:
        """
        Calculate warmup requirements, load bars, validate, and inject.

        Args:
            config: AutoTrader configuration
            workers: List of worker instances (with get_warmup_requirements())
            bar_controller: BarRenderingController to inject bars into
            connection_policy: Retry ladder for the broker's bar history (#473). Live
                path only; the mock path reads its prepared package
            package: The mock session's prepared data package, whose warmup bars the shared
                mount selected; None on the live path
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
            bars_by_tf = self._bars_from_package(config.symbol, package)

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
    # MOCK PATH — the bars the shared mount prepared
    # =========================================================================

    def _bars_from_package(
        self,
        symbol: str,
        package: Optional[ProcessDataPackage],
    ) -> Dict[str, List[Bar]]:
        """
        The warmup bars the shared mount prepared for the replayed window.

        The same bars a backtest of that window warms up on, chosen by the same rule — the last N
        bars BEFORE the window's start (`SharedDataPreparator.prepare_bars`) — and converted by the
        same deserializer the simulation uses. Reading the bar file here instead would take its
        NEWEST bars whatever the window: a mock session replaying January would warm its
        indicators on September, and start from a state no backtest of January can have.

        Args:
            symbol: The traded symbol
            package: The mock session's prepared data; None yields no bars

        Returns:
            Dict[timeframe, List[Bar]]
        """
        if package is None:
            return {}
        return {
            timeframe: list(deserialize_bars_batch(symbol, bars))
            for (_, timeframe, _), bars in package.bars.items()
        }

    # =========================================================================
    # LIVE PATH — Broker API bar fetching
    # =========================================================================

    def _require_venue_can_serve(self, timeframes: Iterable[str]) -> None:
        """
        Refuse a live-adapter session whose workers need a timeframe the venue cannot warm up from.

        Args:
            timeframes: The timeframes the workers require

        Returns:
            None
        """
        supported = KrakenOhlcBarFetcher.supported_warmup_timeframes()
        unservable = sorted(tf for tf in timeframes if tf not in supported)
        if not unservable:
            return

        raise UnsupportedTimeframeError(
            unservable[0],
            f'The broker cannot serve warmup bars for {unservable}. '
            f'Supported by this venue: {sorted(supported)}. '
            f'A timeframe may exist in the project registry and be renderable from the '
            f'archive without the venue offering it live.',
        )

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
        # The project's timeframe vocabulary is wider than any one venue's: M10 is an
        # archive and analysis timeframe and Kraken publishes no ten-minute interval. Ask
        # once, before the first fetch, so the session refuses naming EVERY unservable
        # timeframe rather than dying on whichever one the loop reached first.
        self._require_venue_can_serve(warmup_by_tf.keys())

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
