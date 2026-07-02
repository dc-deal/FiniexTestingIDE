"""
FiniexTestingIDE - AutoTrader Sentiment Feed Preparation (#431)
Resolves the profile's mock sentiment feed and injects it into SIGNAL workers.

Mirrors process_startup_preparation._inject_signal_providers for the live session.
"""

from datetime import datetime
from pathlib import Path
from typing import Tuple

import pandas as pd

from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.exceptions.signal_data_errors import SignalDataUnavailableError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.signal_data.signal_parquet_reader import load_signal_series_from_parquet
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.utils.time_utils import ensure_utc_aware
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker


def setup_sentiment_feed(
    config: AutoTraderConfig,
    workers: list,
    logger: ScenarioLogger
) -> None:
    """
    Resolve and inject the mock sentiment feed into SIGNAL workers (#431).

    Mirrors process_startup_preparation._inject_signal_providers for the live
    session: resolves the profile's sentiment_source (signal index or explicit
    parquet override) against the mock tick session's time range, loads the
    SignalSeries via the shared parquet reader (#429), and injects one
    SignalDataProvider per SIGNAL-worker source. Validation errors raise at
    startup (§ error model: ABORT) — never at the first tick.

    Args:
        config: AutoTrader configuration
        workers: Created worker instances (mixed INDICATOR/SIGNAL)
        logger: ScenarioLogger instance
    """
    feed = config.sentiment_source
    signal_workers = [w for w in workers if isinstance(w, AbstractSignalWorker)]

    # Early exit: no feed configured, no SIGNAL worker → nothing to do
    if not feed.type and not signal_workers:
        return

    if signal_workers and not feed.type:
        names = ', '.join(f"'{w.name}'" for w in signal_workers)
        raise ValueError(
            f"Configuration error: SIGNAL worker(s) {names} require a "
            f"'sentiment_source' block in profile '{config.name}'.\n"
            f"Add to profile:\n"
            f'  "sentiment_source": {{ "type": "mock", "data_sentiment_type": "<pipeline_id>" }}'
        )

    if feed.type and not signal_workers:
        logger.warning(
            f"⚠️ Profile '{config.name}' configures a 'sentiment_source' but the "
            f"strategy has no SIGNAL worker — the feed stays unused (dead config)."
        )
        return

    if feed.type != 'mock':
        raise ValueError(
            f"Unknown sentiment source type: '{feed.type}'. Supported: 'mock' "
            f"(file-backed replay). Live sentiment feeds are not available yet."
        )

    if config.tick_source.type != 'mock':
        raise ValueError(
            f"Configuration error: 'sentiment_source' requires a mock tick source — "
            f"recorded sentiment cannot be replayed against live ticks "
            f"(tick_source.type='{config.tick_source.type}')."
        )

    # Session window = the mock tick parquet's time range
    window_start, window_end = _read_correlation_window(config.tick_source.parquet_path)

    # Resolution: data_sentiment_type via signal index (primary), parquet_path override (dev)
    if feed.data_sentiment_type:
        index_manager = SignalIndexManager(logger)
        index_manager.build_index()  # Auto-loads or rebuilds
        files = index_manager.get_relevant_files(
            feed.data_sentiment_type, config.symbol, window_start, window_end)
        if not files:
            raise SignalDataUnavailableError(
                f"SIGNAL source '{feed.data_sentiment_type}' has no imported data "
                f"for symbol '{config.symbol}' in the mock tick window "
                f"({window_start} → {window_end}). Run the signal import or check "
                f"the profile's 'sentiment_source.data_sentiment_type'."
            )
        feed_label = feed.data_sentiment_type
    elif feed.parquet_path:
        override = Path(feed.parquet_path)
        if not override.exists():
            raise SignalDataUnavailableError(
                f"Sentiment parquet override not found: {override}"
            )
        files = [override]
        feed_label = override.name
    else:
        raise ValueError(
            f"Configuration error: 'sentiment_source' in profile '{config.name}' has "
            f"neither a 'data_sentiment_type' nor a 'parquet_path' configured."
        )

    # One provider per distinct SIGNAL-worker source (same seam the sim uses)
    for source in sorted({w.get_signal_source() for w in signal_workers}):
        series = load_signal_series_from_parquet(
            files, source=source, symbol=config.symbol,
            start=window_start, end=window_end)
        provider = SignalDataProvider(series)
        for worker in signal_workers:
            if worker.get_signal_source() == source:
                worker.set_signal_provider(provider)
                logger.debug(
                    f"📡 Injected signal provider '{source}' into worker '{worker.name}'"
                )
        logger.info(
            f"📡 Sentiment feed: {feed_label} → '{source}', "
            f"{len(series.snapshots)} snapshots ({window_start} → {window_end})"
        )


def _read_correlation_window(tick_parquet_path: str) -> Tuple[datetime, datetime]:
    """
    Read the correlation window: the mock tick parquet's time range
    (min/max of the timestamp column) the sentiment archive is resolved against.

    Args:
        tick_parquet_path: Path to the mock tick parquet file

    Returns:
        (start, end) as UTC-aware datetimes
    """
    timestamps = pd.read_parquet(tick_parquet_path, columns=['timestamp'])['timestamp']
    start = ensure_utc_aware(timestamps.min().to_pydatetime())
    end = ensure_utc_aware(timestamps.max().to_pydatetime())
    return start, end
