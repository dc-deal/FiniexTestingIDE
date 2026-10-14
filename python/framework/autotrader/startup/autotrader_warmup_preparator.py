"""
FiniexTestingIDE - AutoTrader Warmup Preparator
Loads warmup bars for AutoTrader sessions (a mock session from its prepared data package, a
live-adapter session from the venue API) and hands them to the session's WarmupHistory.

Two paths:
- Mock: the bars the shared mount prepared — exactly the bars a backtest of the same window warms
  up on, selected by the simulation's own function for the same first tick
- Live adapter: Kraken OHLC REST API (extensible to MT5 via ABC), read at boot and read again
  at the first tick where a period ended in between (WarmupHistory closes that seam)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.bars.warmup_history import WarmupHistory
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.connection_errors import ConnectionInadmissibleError
from python.framework.exceptions.timeframe_errors import UnsupportedTimeframeError
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.utils.connection_ladder import ConnectionLadder
from python.framework.utils.scenario_requirements import calculate_scenario_requirements
from python.framework.validators.warmup_history_validator import (
    describe_shortfalls,
    find_capacity_shortfalls,
    find_warmup_shortfalls,
)
from python.framework.workers.abstract_worker import AbstractWorker


class AutotraderWarmupPreparator:
    """
    Loads and injects warmup bars for AutoTrader sessions.

    Mock path: the warmup bars in the session's prepared data package.
    Live path: fetches bars from broker API (Kraken OHLC).

    Both paths build one WarmupHistory and inject it through the bar controller's checked
    route — the same route the simulation uses.

    Args:
        logger: ScenarioLogger for status messages
        clock: The wall clock the venue read is stamped with — injected by tests; a session
            has no tick yet to set its own clock when the read is made
    """

    def __init__(
        self,
        logger: ScenarioLogger,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._logger = logger
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare_and_inject(
        self,
        config: AutoTraderConfig,
        workers: List,
        bar_controller: BarRenderingController,
        connection_policy: Optional[ConnectionPolicy] = None,
        package: Optional[ProcessDataPackage] = None,
        record_path: Optional[Path] = None,
    ) -> Optional[WarmupHistory]:
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
            record_path: Where the session records the history it started from
                (`io/warmup_history.json`); None records nothing

        Returns:
            The session's warmup history; None when no worker requires bars
        """
        # === Step 1: Calculate requirements from workers ===
        reqs = calculate_scenario_requirements(workers)
        warmup_by_tf = reqs.warmup_by_timeframe

        if not warmup_by_tf:
            self._logger.debug('⏭️  No warmup requirements from workers')
            return None

        self._logger.info(
            f"📊 Warmup requirements: "
            f"{', '.join(f'{tf}:{count}' for tf, count in warmup_by_tf.items())}"
        )

        # === Step 2: Can the renderer hold what the workers require? ===
        self._require_capacity(warmup_by_tf, config.execution.bar_max_history)

        # === Step 3: Load bars ===
        live = config.adapter_type == 'live'
        if live:
            history = self._history_from_venue(
                config.symbol, warmup_by_tf, connection_policy or ConnectionPolicy()
            )
        else:
            history = WarmupHistory.from_package(
                config.symbol, package, self._logger, required_by_timeframe=warmup_by_tf)

        # === Step 4: Judge it — the shortfall rule every path shares ===
        self._judge_history(history, warmup_by_tf, live)
        if live:
            self._warn_tradeless_bars(config.symbol, history)

        # === Step 5: Inject through the checked route; the seam closes at the first tick ===
        history.inject_into(bar_controller, record_path)
        return history

    def _require_capacity(self, warmup_by_tf: Dict[str, int], capacity: int) -> None:
        """
        Refuse a session whose workers require more bars than the renderer keeps.

        Args:
            warmup_by_tf: Required bars per timeframe
            capacity: The profile's execution.bar_max_history

        Returns:
            None
        """
        shortfalls = find_capacity_shortfalls(warmup_by_tf, capacity)
        if not shortfalls:
            return
        raise WarmupHistoryRefusedError(
            f'Warmup larger than the bar history: {describe_shortfalls(shortfalls)} '
            f'(execution.bar_max_history = {capacity}). The renderer keeps at most that many '
            f'bars per timeframe, so the workers would run on a shortened history without '
            f'saying so — raise bar_max_history to at least '
            f'{max(shortfall.required for shortfall in shortfalls)}.'
        )

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

    def _history_from_venue(
        self,
        symbol: str,
        warmup_by_tf: Dict[str, int],
        policy: ConnectionPolicy,
    ) -> WarmupHistory:
        """
        Read the warmup history from the broker API.

        Uses KrakenOhlcBarFetcher for Kraken broker type.
        Extensible to MT5 via ABC pattern (#209).

        The ladder here DEGRADES on give-up even where the broker's policy says abort:
        a short read is reported one level up by _judge_history, which knows how
        many bars are missing on which timeframe and can say so. Aborting here would
        replace that with "the endpoint did not answer", which is true and useless.

        The history is selected for the instant of the read, taken once for every
        timeframe — the wall clock, because no tick has arrived to set the session clock.
        WarmupHistory reads again at the first tick where a period ended since.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSD')
            warmup_by_tf: Required bars per timeframe
            policy: The broker's connection policy — its numbers, its own give-up rule
                replaced as described above

        Returns:
            The venue history
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
        return WarmupHistory.from_venue(
            symbol=symbol,
            required_by_timeframe=warmup_by_tf,
            fetch=lambda timeframe, count, reference: fetcher.fetch_bars(
                symbol=symbol, timeframe=timeframe, count=count, reference_time=reference),
            ladder=ladder,
            reference_time=self._clock(),
            logger=self._logger,
        )

    # =========================================================================
    # DISPLAY LABEL CACHE — built once, read by tick loop + display thread
    # =========================================================================

    def build_display_label_cache(
        self,
        decision_logic: AbstractDecisionLogic,
        workers: List[AbstractWorker],
        data_sentiment_type: str = '',
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
            data_sentiment_type: Sentiment feed label (#431; '' = no feed)

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
            data_sentiment_type=data_sentiment_type,
        )

        self._logger.debug(
            f'🏷️  Display label cache built: '
            f'{len(config_param_specs)} config params, '
            f'{len(worker_display_output_keys)} workers, '
            f'{sum(len(l) for l in worker_output_labels.values())} worker output labels'
        )
        return cache

    def _warn_tradeless_bars(self, symbol: str, history: WarmupHistory) -> None:
        """
        Warn where the venue's warmup holds bars without a single trade.

        Kraken publishes a row for every interval, traded or not; the archive skips a period in
        which no tick arrived. On a thinly traded symbol the live warmup therefore holds flat bars
        a backtest of the same window never has, and its indicators start from other values.
        Measured 2026-10-13 over the last 721 rows: DASHUSD 187 such rows on M5, none on M30 or
        D1; ETHUSD, BTCUSD, SOLUSD and DOTUSD none on any of the three. Said, not corrected:
        dropping them would make the seam unable to tell an empty period from an uncommitted one.

        Args:
            symbol: Trading symbol
            history: The venue history read at boot

        Returns:
            None
        """
        for timeframe, bars in history.get_bars_by_timeframe().items():
            tradeless = sum(1 for bar in bars if bar.tick_count == 0)
            if not tradeless:
                continue
            self._logger.warning(
                f'⚠️  {symbol} {timeframe}: {tradeless} of {len(bars)} warmup bars hold no trade '
                f'— the venue fills quiet periods with flat bars, the archive skips them; a '
                f'backtest of this window warms up on different bars'
            )

    def _judge_history(
        self,
        history: WarmupHistory,
        warmup_by_tf: Dict[str, int],
        live: bool,
    ) -> None:
        """
        Apply the warmup outcome table: refuse what cannot be traded on, warn what can.

        A timeframe the venue could not be read for is refused (#473); so is a timeframe with
        no bars at all, in either path. A history shorter than required because the source
        holds no more is a warning — the archive's first bars, the venue's reach.

        Args:
            history: The loaded warmup history
            warmup_by_tf: Required bars per timeframe
            live: True when the bars came from the broker API

        Returns:
            None
        """
        unreadable = history.get_unreadable_timeframes()
        if unreadable:
            # #473 — live: refuse. A worker with no history still emits a number, that number
            # is wrong, and NOTHING declares it wrong: unlike a stale signal or a stale feed,
            # an empty indicator history has no contract to degrade into. Trading on it is not
            # a reduced run, it is a different one.
            raise ConnectionInadmissibleError(
                f"Warmup requirement unmet: the broker's bar history could not be read for "
                f'{", ".join(unreadable)}, and there is no staleness contract for an empty '
                f'indicator history — refusing to start rather than trading on unreliable '
                f'worker output.'
            )

        shortfalls = find_warmup_shortfalls(history.get_available_by_timeframe(), warmup_by_tf)
        empty = [shortfall for shortfall in shortfalls if shortfall.is_empty()]
        if empty:
            source = 'the venue' if live else 'the archive'
            raise WarmupHistoryRefusedError(
                f'No warmup bars for {describe_shortfalls(empty)}: {source} holds not a single '
                f"bar complete at the session's start, so those workers would start from "
                f'nothing — refusing to start.'
            )
        if shortfalls:
            self._logger.warning(
                f'⚠️  Insufficient warmup bars — {describe_shortfalls(shortfalls)} — the '
                f'source holds no more; workers may produce unreliable signals until history '
                f'fills'
            )
