"""
FiniexTestingIDE - Tick Source Setup
Builds the profile's tick source (mock replay / live WebSocket) and starts
its feeder thread (threading model 8.a).
"""

import queue
import threading
from pathlib import Path
from typing import Optional

from python.framework.autotrader.tick_sources.kraken_tick_source import KrakenTickSource
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.utils.process_serialization_utils import process_deserialize_ticks_batch


def setup_tick_source(
    config: AutoTraderConfig,
    tick_queue: queue.Queue,
    base_currency: str,
    quote_currency: str,
    logger: ScenarioLogger,
    package: Optional[ProcessDataPackage] = None,
    session_logger: Optional[ScenarioLogger] = None,
    run_dir: Optional[Path] = None,
    run_id: str = '',
) -> tuple:
    """
    Create tick source and start it in a separate thread.

    Threading model 8.a: tick source pushes to queue.Queue,
    main thread pulls via queue.get().

    Args:
        config: AutoTrader configuration
        tick_queue: Thread-safe queue for tick delivery
        base_currency: Symbol base currency from SymbolSpec (e.g., 'BTC', 'DASH')
        quote_currency: Symbol quote currency from SymbolSpec (e.g., 'USD')
        logger: Logger instance
        package: Prepared scenario data package (#438) — the mock source replays its ticks
        session_logger: The session's logger — the live source writes what the operator must
            read there (#609)
        run_dir: The run's directory; the live source records its outages in its io/ folder
        run_id: Written into that record's header

    Returns:
        (tick_source, tick_thread)
    """
    if config.tick_source.type == 'mock':
        # Deserialize the prepared transport ticks into TickData (same path the sim subprocess
        # uses via process_startup_preparation) before replaying them.
        ticks = list(process_deserialize_ticks_batch(
            config.symbol, package.ticks)) if package else []
        tick_source = MockTickSource(
            ticks=ticks,
            symbol=config.symbol,
            tick_queue=tick_queue,
            tick_delay_ms=config.tick_source.tick_delay_ms,
            freeze_after_ticks=config.tick_source.freeze_after_ticks,
            freeze_duration_s=config.tick_source.freeze_duration_s,
        )

    elif config.tick_source.type == 'kraken':
        ws_pair = _resolve_ws_pair(config.symbol, base_currency, quote_currency)
        tick_source = KrakenTickSource(
            symbol=config.symbol,
            ws_pair=ws_pair,
            tick_queue=tick_queue,
            ws_url=config.tick_source.ws_url,
            quote_channel_enabled=config.tick_source.quote_channel_enabled,
            reconnect_initial_delay_s=config.tick_source.reconnect_initial_delay_s,
            reconnect_max_delay_s=config.tick_source.reconnect_max_delay_s,
            backoff_reset_after_s=config.tick_source.backoff_reset_after_s,
            connection_check_interval_s=config.tick_source.connection_check_interval_s,
            connection_dead_s=config.tick_source.connection_dead_s,
            logger=logger,
            session_logger=session_logger,
            record_dir=run_dir / IO_SUBDIR if run_dir is not None else None,
            run_id=run_id,
        )
    else:
        raise ValueError(
            f"Unknown tick source type: '{config.tick_source.type}'. "
            f"Supported: 'mock', 'kraken'."
        )

    tick_thread = threading.Thread(
        target=tick_source.start,
        name='AutoTrader-TickSource',
        daemon=True,
    )
    tick_thread.start()
    logger.info(
        f'📡 Tick source started: {config.tick_source.type} '
        f'({config.symbol})'
    )

    return tick_source, tick_thread


def _resolve_ws_pair(symbol: str, base_currency: str, quote_currency: str) -> str:
    """
    Resolve internal symbol to Kraken WS pair format.

    Args:
        symbol: Internal symbol (e.g., 'BTCUSD')
        base_currency: Symbol base currency from SymbolSpec (e.g., 'BTC', 'DASH')
        quote_currency: Symbol quote currency from SymbolSpec (e.g., 'USD')

    Returns:
        Kraken WS pair (e.g., 'BTC/USD', 'DASH/USD')
    """
    return f'{base_currency}/{quote_currency}'
