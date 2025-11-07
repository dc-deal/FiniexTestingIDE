"""
FiniexTestingIDE - Process Executor (CORRECTED)
Process-based scenario execution with ProcessPool support
"""
import time
import traceback
from collections import defaultdict
from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.process_data_types import (
    ProcessPreparedDataObjects,
    ProcessProfileData,
    ProcessTickLoopResult,
    ProcessScenarioConfig,
)


def execute_tick_loop(
    config: ProcessScenarioConfig,
    prepared_objects: ProcessPreparedDataObjects
) -> ProcessTickLoopResult:
    """
    Execute tick processing loop.

    MAIN PROCESSING: Iterate through ticks, process each.

    Args:
        config: Scenario configuration
        shared_data: Shared data package
        prepared_objects: Objects from startup_preparation

    Returns:
        Dictionary with loop results
    """
    coordinator = prepared_objects.coordinator
    trade_simulator = prepared_objects.trade_simulator
    bar_rendering_controller = prepared_objects.bar_rendering_controller
    scenario_logger = prepared_objects.scenario_logger
    decision_logic = prepared_objects.decision_logic

    # Get ticks from shared_data (CoW!)
    # ticks = shared_data.ticks[config.symbol]
    # ToDo unoptimized, no CoW -
    ticks = prepared_objects.ticks

    # Performance profiling
    profile_times = defaultdict(float)
    profile_counts = defaultdict(int)
    tick_count = 0

    # === DEBUG: TICK RANGE INFO ===
    scenario_logger.debug(f"🔍 [DEBUG] Tick loop starting")
    scenario_logger.debug(f"  Total ticks: {len(ticks)}")
    scenario_logger.debug(f"  TradeSimulator ID: {id(trade_simulator)}")
    scenario_logger.debug(f"  Portfolio ID: {id(trade_simulator.portfolio)}")
    if len(ticks) > 0:
        first_tick = ticks[0]
        last_tick = ticks[-1]
        scenario_logger.debug(
            f"  First tick: {first_tick.timestamp} | {first_tick.symbol} | bid={first_tick.bid:.5f}")
        scenario_logger.debug(
            f"  Last tick:  {last_tick.timestamp} | {last_tick.symbol} | bid={last_tick.bid:.5f}")

    scenario_logger.info(f"🔄 Starting tick loop ({len(ticks):,} ticks)")

    # === TICK LOOP ===
    for tick in ticks:
        tick_count += 1
        tick_start = time.perf_counter()

        # === 1. Trade Simulator ===
        t5 = time.perf_counter()
        trade_simulator.update_prices(tick)
        t6 = time.perf_counter()
        profile_times['trade_simulator'] += (t6 - t5) * 1000
        profile_counts['trade_simulator'] += 1

        # === 2. Bar Rendering ===
        t3 = time.perf_counter()
        current_bars = bar_rendering_controller.process_tick(tick)
        t4 = time.perf_counter()
        profile_times['bar_rendering'] += (t4 - t3) * 1000
        profile_counts['bar_rendering'] += 1

        # === 3. Bar History Retrieval ===
        t5 = time.perf_counter()
        bar_history = bar_rendering_controller.get_all_bar_history(
            symbol=config.symbol
        )
        t6 = time.perf_counter()
        profile_times['bar_history'] += (t6 - t5) * 1000
        profile_counts['bar_history'] += 1

        # === 4. Worker Processing + Decision ===
        t7 = time.perf_counter()
        decision = coordinator.process_tick(
            tick=tick,
            current_bars=current_bars,
            bar_history=bar_history
        )
        t8 = time.perf_counter()
        profile_times['worker_decision'] += (t8 - t7) * 1000
        profile_counts['worker_decision'] += 1

        # === 5. Order Execution ===
        t9 = time.perf_counter()
        try:
            decision_logic.execute_decision(
                decision, tick
            )
            # self.deps.performance_log.update_live_stats(
            #     scenario_index=self.scenario_index,
            #     ticks_processed=tick_count
            # )
        except Exception as e:
            scenario_logger.error(
                f"Order execution failed: \n{traceback.format_exc()}"
            )
            raise e

        t10 = time.perf_counter()
        profile_times['order_execution'] += (t10 - t9) * 1000
        profile_counts['order_execution'] += 1

        # === 6. Periodic Stats Update ===
        # if tick_count % 500 == 0:
        #     t11 = time.perf_counter()
        #     self.deps.performance_log.update_live_stats(
        #         scenario_index=self.scenario_index,
        #         ticks_processed=tick_count
        #     )
        #     t12 = time.perf_counter()
        #     profile_times['stats_update'] += (t12 - t11) * 1000
        #     profile_counts['stats_update'] += 1

        # Total tick time
        tick_end = time.perf_counter()
        profile_times['total_per_tick'] += (
            tick_end - tick_start) * 1000

    scenario_logger.info(
        f"✅ Tick loop completed: {tick_count:,} ticks")

    # === CLOSE OPEN TRADES ===
    trade_simulator.close_all_remaining_orders()

    # === CLEANUP COORDINATOR ===
    coordinator.cleanup()
    scenario_logger.debug("✅ Coordinator cleanup completed")

    # === GET RESULTS ===
    decision_statistics = decision_logic.get_statistics()
    performance_stats = coordinator.performance_log.get_snapshot()
    portfolio_stats = trade_simulator.portfolio.get_portfolio_statistics()
    execution_stats = trade_simulator.get_execution_stats()
    cost_breakdown = trade_simulator.portfolio.get_cost_breakdown()

    total_profit = portfolio_stats.total_profit
    total_loss = portfolio_stats.total_loss
    total_pnl = total_profit - total_loss
    currency = portfolio_stats.currency
    scenario_logger.debug(
        f"💰 Portfolio P&L: {format_currency_simple(total_pnl, currency)}")

    return ProcessTickLoopResult(
        decision_statistics=decision_statistics,
        performance_stats=performance_stats,
        portfolio_stats=portfolio_stats,
        execution_stats=execution_stats,
        cost_breakdown=cost_breakdown,
        profiling_data=ProcessProfileData(
            profile_times=profile_times, profile_counts=profile_counts)
    )
