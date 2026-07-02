"""
FiniexTestingIDE - Tick Source Setup
Builds the profile's tick source (mock replay / live WebSocket) and starts
its feeder thread (threading model 8.a).
"""

import queue
import threading

from python.framework.autotrader.tick_sources.kraken_tick_source import KrakenTickSource
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig


def setup_tick_source(
    config: AutoTraderConfig,
    tick_queue: queue.Queue,
    base_currency: str,
    quote_currency: str,
    logger: ScenarioLogger
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

    Returns:
        (tick_source, tick_thread)
    """
    if config.tick_source.type == 'mock':
        tick_source = MockTickSource(
            parquet_path=config.tick_source.parquet_path,
            symbol=config.symbol,
            tick_queue=tick_queue,
            max_ticks=config.tick_source.max_ticks,
            tick_delay_ms=config.tick_source.tick_delay_ms,
        )

    elif config.tick_source.type == 'kraken':
        ws_pair = _resolve_ws_pair(config.symbol, base_currency, quote_currency)
        tick_source = KrakenTickSource(
            symbol=config.symbol,
            ws_pair=ws_pair,
            tick_queue=tick_queue,
            ws_url=config.tick_source.ws_url,
            reconnect_initial_delay_s=config.tick_source.reconnect_initial_delay_s,
            reconnect_max_delay_s=config.tick_source.reconnect_max_delay_s,
            connection_check_interval_s=config.tick_source.connection_check_interval_s,
            connection_dead_s=config.tick_source.connection_dead_s,
            logger=logger,
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
        f"📡 Tick source started: {config.tick_source.type} "
        f"({config.symbol})"
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
