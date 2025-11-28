"""
FiniexTestingIDE - Process Executor
Process-based scenario execution with live update support
"""
import time
import traceback
from collections import defaultdict
from multiprocessing import Queue
from typing import Optional

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.process.process_live_export import process_live_export, process_live_setup
from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import (
    ProcessPreparedDataObjects,
    ProcessProfileData,
    ProcessTickLoopResult,
    ProcessScenarioConfig,
)
from python.framework.utils.process_debug_info_utils import get_tick_range_stats


def execute_tick_loop(
    config: ProcessScenarioConfig,
    prepared_objects: ProcessPreparedDataObjects,
    live_queue: Optional[Queue] = None
) -> ProcessTickLoopResult:
    """
    Execute tick processing loop with live update support.

    MAIN PROCESSING: Iterate through ticks, process each.
    LIVE UPDATES: Send periodic updates via queue (time-based).

    Args:
        config: Scenario configuration
        prepared_objects: Objects from startup_preparation
        live_queue: Queue for live updates (optional)

    Returns:
        ProcessTickLoopResult with loop results
    """
    scenario_logger = prepared_objects.scenario_logger
    try:
        worker_coordinator = prepared_objects.worker_coordinator
        trade_simulator = prepared_objects.trade_simulator
        bar_rendering_controller = prepared_objects.bar_rendering_controller

        decision_logic = prepared_objects.decision_logic
        portfolio = trade_simulator.portfolio

        # Get ticks from prepared_objects
        ticks = prepared_objects.ticks

        # Performance profiling
        profile_times = defaultdict(float)
        profile_counts = defaultdict(int)

        tick_range_stats = get_tick_range_stats(prepared_objects)

        live_setup = process_live_setup(
            scenario_logger, config, ticks, live_queue)
        live_update_count = 0

        scenario_logger.info(
            f"🔄 Starting tick loop ({live_setup.tick_count:,} ticks)")

        tick_loop_error: Exception = None

        # === TICK LOOP ===
        # from now on, log shows ticks.
        scenario_logger.set_tick_loop_started(True)
        for tick_idx, tick in enumerate(ticks):
            scenario_logger.set_current_tick(
                tick_idx + 1, tick)
            tick_start = time.perf_counter()

            # === 1. Trade Simulator ===
            t1 = time.perf_counter()
            trade_simulator.update_prices(tick)
            t2 = time.perf_counter()
            profile_times['trade_simulator'] += (t2 - t1) * 1000
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
            decision = worker_coordinator.process_tick(
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
                decision_logic.execute_decision(decision, tick)
            except Exception as e:
                raise RuntimeError(
                    f"Order execution failed: {e} \n{traceback.format_exc()}")

            t10 = time.perf_counter()
            profile_times['order_execution'] += (t10 - t9) * 1000
            profile_counts['order_execution'] += 1

            # === 6. LIVE UPDATES (Time-based) ===
            t11 = time.perf_counter()
            live_updated = process_live_export(
                live_setup, config, tick_idx, tick, portfolio, worker_coordinator, current_bars)
            if (live_updated):
                live_update_count += 1
            t12 = time.perf_counter()
            profile_times['live_update'] += (t12 - t11) * 1000
            profile_counts['live_update'] += 1

            # Total tick time
            tick_end = time.perf_counter()
            profile_times['total_per_tick'] += (tick_end - tick_start) * 1000

        scenario_logger.set_tick_loop_started(False)
        scenario_logger.info(
            f"✅ Tick loop completed: {live_setup.tick_count:,} ticks")

        # === CLOSE OPEN TRADES ===
        trade_simulator.close_all_remaining_orders()

    except Exception as e:
        # in case of an error, abort & try to collect the rest of data.
        # see below.
        scenario_logger.error(
            f"Error in Tick Loop - Runtime - try to collect statistics now.: {e}")
        tick_loop_error = e

    try:
        # === CLEANUP COORDINATOR ===
        worker_coordinator.cleanup()
        scenario_logger.debug("✅ Coordinator cleanup completed")

        # === GET RESULTS ===
        # Collect statistics from Algorithm section
        decision_statistics = decision_logic.get_statistics()
        worker_statistics = worker_coordinator.get_worker_statistics()
        coordination_statistics = worker_coordinator.get_coordination_statistics()

        # collect stistics from Trader section
        portfolio_stats = trade_simulator.portfolio.get_portfolio_statistics()
        execution_stats = trade_simulator.get_execution_stats()
        cost_breakdown = trade_simulator.portfolio.get_cost_breakdown()

        _print_tick_loop_finishing_log(
            live_update_count, scenario_logger, portfolio_stats
        )

        return ProcessTickLoopResult(
            decision_statistics=decision_statistics,
            worker_statistics=worker_statistics,
            coordination_statistics=coordination_statistics,
            portfolio_stats=portfolio_stats,
            execution_stats=execution_stats,
            cost_breakdown=cost_breakdown,
            profiling_data=ProcessProfileData(
                profile_times=profile_times,
                profile_counts=profile_counts
            ),
            tick_range_stats=tick_range_stats,
            tick_loop_error=tick_loop_error
        )
    except Exception as e:
        # an error here is not possible to handle correctly.
        scenario_logger.error(f"Error in Tick Loop - Statistics & Return: {e}")
        raise e


def _print_tick_loop_finishing_log(
        live_update_count: int,
        scenario_logger: ScenarioLogger,
        portfolio_stats: PortfolioStats):
    """
        Simple finishing log after Tick loop for debug purposes.
    """
    total_profit = portfolio_stats.total_profit
    total_loss = portfolio_stats.total_loss
    total_pnl = total_profit - total_loss
    currency = portfolio_stats.currency
    scenario_logger.debug(
        f"Live update count: {live_update_count}")
    scenario_logger.info(
        f"💰 Portfolio P&L: {format_currency_simple(total_pnl, currency)}")
