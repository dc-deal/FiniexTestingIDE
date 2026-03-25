"""
FiniexTestingIDE - Process Executor
Process-based scenario execution with live update support
"""
# ============================================================================
# BARRIER SYNCHRONIZATION IMPORT
# ============================================================================
# CRITICAL: Import from threading, NOT multiprocessing or asyncio!
#
# Why threading?
# - Manager.Barrier() is a proxy to a threading.Barrier object
# - The Manager runs a server process that uses threading internally
# - Therefore, it raises threading.BrokenBarrierError, not multiprocessing
#
# Common mistakes:
# ❌ from asyncio import BrokenBarrierError        # Wrong module
# ✅ from threading import BrokenBarrierError       # Correct!
# ============================================================================
from threading import BrokenBarrierError
import time
import traceback
from collections import defaultdict
from multiprocessing import Queue
from typing import Any, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_live_export import process_live_export, process_live_setup
from python.framework.process.process_live_queue_helper import send_status_update_process
from python.framework.types.trading_env_types.currency_codes import format_currency_simple
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.process.process_block_boundary import build_block_boundary_report
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
        sync_barrier: Barrier for synchronized start (optional)

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

        # Inter-tick interval collection (collected_msc preferred, time_msc fallback)
        inter_tick_intervals: List[float] = []
        prev_interval_msc: int = 0

        tick_range_stats = get_tick_range_stats(prepared_objects)

        live_setup = process_live_setup(
            scenario_logger, config, ticks, live_queue)
        live_update_count = 0

        tick_loop_error: Exception = None
        current_bars = {}
        current_tick = None
        current_index = 0

        # Count algo ticks (non-clipped) for log messages
        algo_tick_count = sum(1 for t in ticks if not t.is_clipped)
        has_clipping = algo_tick_count < len(ticks)

        if has_clipping:
            scenario_logger.info(
                f"🔄 Starting tick loop ({live_setup.tick_count:,} ticks, "
                f"{algo_tick_count:,} algo)")
        else:
            scenario_logger.info(
                f"🔄 Starting tick loop ({live_setup.tick_count:,} ticks)")

        # === TICK LOOP ===
        # from now on, log shows ticks.
        scenario_logger.set_tick_loop_started(True)

        for tick_idx, tick in enumerate(ticks):
            scenario_logger.set_current_tick(
                tick_idx + 1, tick)
            tick_start = time.perf_counter()
            current_tick = tick
            current_index = tick_idx

            # Inter-tick interval: use collected_msc (monotonic) when available,
            # fall back to time_msc for pre-V1.3.0 data (with negative-diff skip)
            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            if prev_interval_msc > 0 and current_msc > 0:
                delta = current_msc - prev_interval_msc
                if tick.collected_msc > 0 or delta >= 0:
                    inter_tick_intervals.append(float(delta))
            prev_interval_msc = current_msc

            # === 1. Trade Executor (BROKER PATH — all ticks) ===
            # Broker sees every tick regardless of algo processing budget.
            # Pending order fills, SL/TP triggers, limit/stop monitoring
            # all operate on the full tick stream.
            t1 = time.perf_counter()
            trade_simulator.on_tick(tick)
            t2 = time.perf_counter()
            profile_times['trade_simulator'] += (t2 - t1) * 1000
            profile_counts['trade_simulator'] += 1

            # === CLIPPING GATE ===
            # Ticks flagged by tick processing budget skip the algo path.
            # The broker already processed them above.
            if tick.is_clipped:
                continue

            # === ALGO PATH (non-clipped ticks only) ===

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
        if has_clipping:
            scenario_logger.info(
                f"✅ Tick loop completed: {live_setup.tick_count:,} ticks "
                f"({algo_tick_count:,} algo)")
        else:
            scenario_logger.info(
                f"✅ Tick loop completed: {live_setup.tick_count:,} ticks")

        # === CLOSE OPEN TRADES ===
        # Use last tick's msc for latency calculation (same fallback as inter-tick interval)
        last_msc = (current_tick.collected_msc if current_tick and current_tick.collected_msc > 0
                     else current_tick.time_msc if current_tick else 0)
        trade_simulator.close_all_remaining_orders(current_msc=last_msc)
        trade_simulator.check_clean_shutdown()
        # update live the last time - to show final balance correctly
        live_updated = process_live_export(
            live_setup, config, current_index, current_tick, portfolio, worker_coordinator, current_bars)

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

        # collect statistics from Trader section
        portfolio_stats = trade_simulator.portfolio.get_portfolio_statistics()
        execution_stats = trade_simulator.get_execution_stats()
        cost_breakdown = trade_simulator.portfolio.get_cost_breakdown()
        trade_history = trade_simulator.get_trade_history()
        order_history = trade_simulator.get_order_history()
        pending_stats = trade_simulator.get_pending_stats()

        # Build block boundary report for Profile Runs
        block_boundary_report = None
        if config.is_profile_run:
            block_boundary_report = build_block_boundary_report(
                trade_history, pending_stats
            )

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
            trade_history=trade_history,
            order_history=order_history,
            pending_stats=pending_stats,
            block_boundary_report=block_boundary_report,
            profiling_data=ProcessProfileData(
                profile_times=profile_times,
                profile_counts=profile_counts,
                inter_tick_intervals_ms=inter_tick_intervals,
                gap_threshold_s=config.inter_tick_gap_threshold_s,
                ticks_total=len(ticks)
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
