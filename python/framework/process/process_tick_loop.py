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
from datetime import datetime, timezone
from multiprocessing import Queue
from typing import Any, List, Optional, Tuple

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_live_export import process_live_export, process_live_setup
from python.framework.process.process_live_queue_helper import send_status_update_process
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.trading_env_types.currency_codes import format_currency_simple
from python.framework.types.decision_event_types import SessionEndEvent, SessionEndSeverity
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.process.process_block_boundary import build_block_boundary_report
from python.framework.process.tick_pipeline_core import execute_algo_path, render_bars_for_tick, run_ghost_pass
from python.framework.types.process_data_types import (
    ProcessProfileData,
    ProcessTickLoopResult,
    ProcessScenarioConfig,
)
from python.framework.utils.process_debug_info_utils import get_tick_range_stats
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _run_sim_heartbeats(
    prev_msc: int,
    current_msc: int,
    config: ProcessScenarioConfig,
    trade_simulator: AbstractTradeExecutor,
    worker_coordinator: WorkerOrchestrator,
    decision_logic: AbstractDecisionLogic,
    decision_event_dispatcher: Optional[DecisionEventDispatcher],
) -> bool:
    """
    Drive decision ghost-passes in the simulated-time gap between two data ticks (#360).

    Hard-gated by the caller on decision_logic.wants_heartbeat(). Fires a ghost-pass
    every config.heartbeat_interval_ms of simulated time strictly inside
    (prev_msc, current_msc), resolving broker fills at each ghost moment via
    TradeSimulator.heartbeat() so an opt-in algo reacts between ticks at the same
    relative point as live. No bar render, no tick counter — tick state untouched.

    Correctness gate (#208): no ghost-passes across a gap longer than
    inter_tick_gap_threshold_s — across a data/weekend gap the market says nothing,
    so synthesizing heartbeats there would fabricate activity.

    Args:
        prev_msc: Previous data tick timestamp (ms)
        current_msc: Current data tick timestamp (ms)
        config: Scenario config (heartbeat_interval_ms, inter_tick_gap_threshold_s)
        trade_simulator: Executor (clock injection + ghost broker resolution)
        worker_coordinator: Orchestrator (process_heartbeat → cached worker results)
        decision_logic: Opt-in decision (executes the ghost action)
        decision_event_dispatcher: #348 channel (drained around the ghost compute)

    Returns:
        True if the algo requested session end during a ghost-pass (caller stops)
    """
    interval_ms = config.heartbeat_interval_ms
    gap_ms = current_msc - prev_msc
    if gap_ms <= interval_ms or gap_ms > config.inter_tick_gap_threshold_s * 1000.0:
        return False

    k = 1
    while prev_msc + k * interval_ms < current_msc:
        ghost_msc = prev_msc + k * interval_ms
        trade_simulator.set_current_time(
            datetime.fromtimestamp(ghost_msc / 1000.0, tz=timezone.utc))
        trade_simulator.heartbeat()
        if decision_event_dispatcher is not None:
            decision_event_dispatcher.drain()
        decision = run_ghost_pass(worker_coordinator)
        if decision is not None:
            decision_logic.execute_decision(decision, tick=None)
        if decision_event_dispatcher is not None:
            decision_event_dispatcher.drain()
        if trade_simulator.is_session_end_requested():
            return True
        k += 1
    return False


def execute_tick_loop(
    config: ProcessScenarioConfig,
    worker_coordinator: WorkerOrchestrator,
    trade_simulator: AbstractTradeExecutor,
    bar_rendering_controller: BarRenderingController,
    decision_logic: AbstractDecisionLogic,
    scenario_logger: ScenarioLogger,
    ticks: Tuple[TickData, ...],
    live_queue: Optional[Queue] = None,
    decision_event_dispatcher: Optional[DecisionEventDispatcher] = None
) -> ProcessTickLoopResult:
    """
    Execute tick processing loop with live update support.

    MAIN PROCESSING: Iterate through ticks, process each.
    LIVE UPDATES: Send periodic updates via queue (time-based).

    Args:
        config: Scenario configuration
        worker_coordinator: Orchestrator for worker execution
        trade_simulator: Trade execution engine
        bar_rendering_controller: Bar rendering controller
        decision_logic: Decision logic instance
        scenario_logger: Logger for this scenario
        ticks: Deserialized tick data
        live_queue: Queue for live updates (optional)

    Returns:
        ProcessTickLoopResult with loop results
    """
    try:
        portfolio = trade_simulator.portfolio

        # Performance profiling (Layer B — #137).
        # When disabled, profile dicts stay empty and inter-tick collection skipped.
        profiling_enabled = config.tick_loop_profiling
        profile_times = defaultdict(float) if profiling_enabled else {}
        profile_counts = defaultdict(int) if profiling_enabled else {}

        # Inter-tick interval collection (collected_msc preferred, time_msc fallback)
        inter_tick_intervals: List[float] = []
        prev_interval_msc: int = 0

        tick_range_stats = get_tick_range_stats(scenario_logger, trade_simulator, ticks)

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
            if profiling_enabled: tick_start = time.perf_counter()
            current_tick = tick
            current_index = tick_idx

            # Inter-tick interval: use collected_msc (monotonic) when available,
            # fall back to time_msc for pre-V1.3.0 data (with negative-diff skip)
            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            if profiling_enabled and prev_interval_msc > 0 and current_msc > 0:
                delta = current_msc - prev_interval_msc
                if tick.collected_msc > 0 or delta >= 0:
                    inter_tick_intervals.append(float(delta))

            # #360: drive decision ghost-passes across the simulated gap to the
            # previous tick — opt-in algos only (hard-gated, absent otherwise).
            # prev_interval_msc still holds the previous tick here (updated below).
            if (decision_logic.wants_heartbeat()
                    and config.heartbeat_interval_ms > 0
                    and prev_interval_msc > 0 and current_msc > 0):
                if _run_sim_heartbeats(
                        prev_interval_msc, current_msc, config, trade_simulator,
                        worker_coordinator, decision_logic, decision_event_dispatcher):
                    scenario_logger.info(
                        f"🛑 Session end requested: {trade_simulator.get_session_end_reason()}")
                    break

            prev_interval_msc = current_msc

            # === 1. Trade Executor (BROKER PATH — all ticks) ===
            # Broker sees every tick regardless of algo processing budget.
            # Pending order fills, SL/TP triggers, limit/stop monitoring
            # all operate on the full tick stream.
            if profiling_enabled: t1 = time.perf_counter()
            trade_simulator.on_tick(tick)
            if profiling_enabled:
                profile_times['trade_simulator'] += (time.perf_counter() - t1) * 1000
                profile_counts['trade_simulator'] += 1

            # === 2. Bar Rendering (all ticks — shared core, #303) ===
            # Bars must reflect the complete market data stream — including
            # clipped ticks. Clipping simulates "algo was too slow to react",
            # NOT "market data was incomplete". Same ordering as AutoTrader.
            if profiling_enabled: t3 = time.perf_counter()
            current_bars = render_bars_for_tick(tick, bar_rendering_controller)
            if profiling_enabled:
                profile_times['bar_rendering'] += (time.perf_counter() - t3) * 1000
                profile_counts['bar_rendering'] += 1

            # === CLIPPING GATE ===
            # Ticks flagged by tick processing budget skip the algo path.
            # The broker and bar rendering already processed them above.
            if tick.is_clipped:
                continue

            # === ALGO PATH (non-clipped ticks only) ===

            # === 3+4. Bar History + Worker Processing + Decision (shared core, #303) ===
            # 'worker_decision' now also covers the (tiny) bar-history
            # retrieval — the former 'bar_history' timer had no report consumer.
            if profiling_enabled: t7 = time.perf_counter()
            decision = execute_algo_path(
                tick=tick,
                current_bars=current_bars,
                bar_controller=bar_rendering_controller,
                worker_orchestrator=worker_coordinator,
                symbol=config.symbol,
            )
            if profiling_enabled:
                profile_times['worker_decision'] += (time.perf_counter() - t7) * 1000
                profile_counts['worker_decision'] += 1

            # === 5. Order Execution ===
            if profiling_enabled: t9 = time.perf_counter()
            try:
                decision_logic.execute_decision(decision, tick)
            except Exception as e:
                raise RuntimeError(
                    f"Order execution failed: {e} \n{traceback.format_exc()}")

            if profiling_enabled:
                profile_times['order_execution'] += (time.perf_counter() - t9) * 1000
                profile_counts['order_execution'] += 1

            # === 5b. Decision Event Drain (#348) ===
            # Events buffered during on_tick (fills, partial closes, cancels)
            # are delivered to the algo hooks here, before the next tick.
            if decision_event_dispatcher is not None:
                if profiling_enabled: t13 = time.perf_counter()
                decision_event_dispatcher.drain()
                if profiling_enabled:
                    profile_times['decision_events'] += (time.perf_counter() - t13) * 1000
                    profile_counts['decision_events'] += 1
                if trade_simulator.is_session_end_requested():
                    scenario_logger.info(
                        f"🛑 Session end requested: {trade_simulator.get_session_end_reason()}")
                    break

            # === 6. LIVE UPDATES (Time-based) ===
            if profiling_enabled: t11 = time.perf_counter()
            live_updated = process_live_export(
                live_setup, config, tick_idx, tick, portfolio, worker_coordinator, current_bars)
            if (live_updated):
                live_update_count += 1
            if profiling_enabled:
                profile_times['live_update'] += (time.perf_counter() - t11) * 1000
                profile_counts['live_update'] += 1

            # Total tick time
            if profiling_enabled:
                profile_times['total_per_tick'] += (time.perf_counter() - tick_start) * 1000

        # === Session end event (#348) — emitted once the loop ends (tick
        # exhaustion or session-end request). Delivered before reporting.
        if decision_event_dispatcher is not None:
            if trade_simulator.is_session_end_requested():
                end_reason = trade_simulator.get_session_end_reason()
                end_severity = trade_simulator.get_session_end_severity()
            else:
                end_reason = 'tick source exhausted'
                end_severity = SessionEndSeverity.NORMAL
            decision_event_dispatcher.submit(SessionEndEvent(
                reason=end_reason,
                severity=end_severity,
                tick_time=trade_simulator.get_current_time(),
            ))
            decision_event_dispatcher.drain()

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
        portfolio_stats.symbol = config.symbol
        if current_tick:
            portfolio_stats.last_price = (current_tick.bid + current_tick.ask) / 2
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
