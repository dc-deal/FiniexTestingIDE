from typing import Any, Dict, Tuple
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.reporting.broker_info_renderer import BrokerInfoRenderer
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import (
    ProcessPreparedDataObjects,
    ProcessScenarioConfig,
    TickRangeStats)


def get_tick_range_stats(prepared_objects: ProcessPreparedDataObjects) -> TickRangeStats:
    """ 
    DEBUG: TICK RANGE INFO 
    """
    logger = prepared_objects.scenario_logger
    trade_simulator = prepared_objects.trade_simulator
    ticks = prepared_objects.ticks

    # === DEBUG: TICK RANGE INFO ===
    logger.debug(f"🔍 Tick loop range info")
    logger.debug(f"  Total ticks: {len(ticks)}")
    logger.debug(f"  TradeSimulator ID: {id(trade_simulator)}")
    logger.debug(f"  Portfolio ID: {id(trade_simulator.portfolio)}")

    # Extract tick time range
    first_tick_time = None
    last_tick_time = None
    tick_timespan_seconds = None
    tick_count = len(ticks)

    if tick_count > 0:
        first_tick = ticks[0]
        last_tick = ticks[-1]
        first_tick_time = first_tick.timestamp
        last_tick_time = last_tick.timestamp

        # Calculate timespan in seconds
        if first_tick_time and last_tick_time:
            tick_timespan_seconds = (
                last_tick_time - first_tick_time).total_seconds()

        logger.debug(
            f"  First tick: {first_tick.timestamp} | {first_tick.symbol} | bid={first_tick.bid:.5f}")
        logger.debug(
            f"  Last tick:  {last_tick.timestamp} | {last_tick.symbol} | bid={last_tick.bid:.5f}")

    return TickRangeStats(
        tick_count=tick_count,
        first_tick_time=first_tick_time,
        last_tick_time=last_tick_time,
        tick_timespan_seconds=tick_timespan_seconds
    )


def debug_warmup_bars_check(warmup_bars: Dict[str, Tuple[Any, ...]],
                            config: ProcessScenarioConfig,
                            logger: ScenarioLogger,
                            bar_rendering_controller: BarRenderingController):
    """
    Check: Wurden die Bars korrekt deserialisiert?
    """
    if not logger.should_logLevel(LogLevel.DEBUG):
        return

    for timeframe in warmup_bars.keys():
        bar_history = bar_rendering_controller.get_bar_history(
            config.symbol, timeframe)
        logger.debug(
            f"📊 Bar History for {timeframe}: {len(bar_history)} bars"
        )
        if len(bar_history) > 0:
            first_bar = bar_history[0]
            last_bar = bar_history[-1]
            logger.debug(
                f"  First: {first_bar.timestamp} (open={first_bar.open})"
            )
            logger.debug(
                f"  Last:  {last_bar.timestamp} (open={last_bar.open})"
            )
        else:
            logger.debug(f"  ❌ EMPTY BAR HISTORY!")


def log_trade_simulator_config(logger: ScenarioLogger, config: ProcessScenarioConfig, trade_simulator: AbstractTradeExecutor):
    # Log broker configuration for transparency
    broker_spec = trade_simulator.broker.get_broker_specification()
    symbol_spec = trade_simulator.broker.get_symbol_specification(
        config.symbol)

    broker_info_text = BrokerInfoRenderer.render_detailed(
        broker_spec=broker_spec,
        symbol_spec=symbol_spec,
        indent=""
    )

    logger.info("\n" + broker_info_text)
    logger.debug("✅ Broker configuration logged")
