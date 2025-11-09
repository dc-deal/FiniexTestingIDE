from typing import Any, Dict, Tuple
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import ProcessDataPackage, ProcessPreparedDataObjects, ProcessScenarioConfig


def debug_tick_range_info(prepared_objects: ProcessPreparedDataObjects):
    """ 
    DEBUG: TICK RANGE INFO 
    """
    logger = prepared_objects.scenario_logger
    if not logger.should_logLevel(LogLevel.DEBUG):
        return

    trade_simulator = prepared_objects.trade_simulator
    ticks = prepared_objects.ticks

    logger.debug(f"🔍 [DEBUG] Tick loop starting")
    logger.debug(f"  Total ticks: {len(ticks)}")
    logger.debug(f"  TradeSimulator ID: {id(trade_simulator)}")
    logger.debug(f"  Portfolio ID: {id(trade_simulator.portfolio)}")
    if len(ticks) > 0:
        first_tick = ticks[0]
        last_tick = ticks[-1]
        logger.debug(
            f"  First tick: {first_tick.timestamp} | {first_tick.symbol} | bid={first_tick.bid:.5f}")
        logger.debug(
            f"  Last tick:  {last_tick.timestamp} | {last_tick.symbol} | bid={last_tick.bid:.5f}")

    logger.info(f"🔄 Starting tick loop ({len(ticks):,} ticks)")


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
