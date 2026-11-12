"""
FiniexTestingIDE - Loop Event Dispatcher (#458)

The one place the order of a pass lives: which steps run, in which order, when the loop core is
handed an event. The order IS the behaviour both pipelines are held to — a divergent order is
what produced the #293 bug class — so it is written as code, one entry per kind of pass, and never
as a table.

The backtest keeps its drive (it walks its tick array) and hands each pass to an entry here; the
AutoTrader still orders its passes itself from the shared blocks, and its entries follow. The two
runners order their passes differently today — where the equity is sampled, how often the decision
events drain around a ghost pass — and these entries keep the backtest's order exactly, so every
result stays as it was. The phase order of an instant
(`InstantPhase`, #457) takes over with the timeline drivers (#460, #461), which feed whole instants
and are measured against the parity record.

A pass ends with its last engine step. What a runner does after it — a state frame, the live
export, the session-end check, its timing — stays in the runner, in its order.
"""

import time
import traceback
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.loop.abstract_backtest_pass_hooks import AbstractBacktestPassHooks
from python.framework.loop.blocks.algo_compute_block import execute_algo_path
from python.framework.loop.blocks.algo_heartbeat_block import run_ghost_pass
from python.framework.loop.blocks.bar_render_block import render_bars_for_tick
from python.framework.loop.loop_context import LoopContext
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.reporting.builders.booking_period_builder import PeriodSnapshot
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.loop_types.loop_event_types import DataSliceEvent
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def dispatch_backtest_slice(
    event: DataSliceEvent,
    ctx: LoopContext,
    hooks: AbstractBacktestPassHooks,
    record: LoopPassRecord,
) -> bool:
    """
    A backtest's tick pass, in the order the backtest has always run it.

    The slice holds one tick: the backtest's drive hands each tick over on its own (`slice_of_tick`),
    so its algo computes once per tick as before. Each step is timed under its own profiling key,
    and the keys are the ones the reports and the benchmark read.

    Args:
        event: The tick, as a slice of one
        ctx: The scenario's collaborators
        hooks: The backtest's own steps inside the pass
        record: What the pass leaves for the drive

    Returns:
        True when the pass ran to its end; False when the clipping gate stopped it after the
        broker path and the bars, and the drive goes on to the next tick
    """
    # Exactly one tick, or this raises: a slice of several would need every tick on the broker
    # path and the bars, which this pass does not do (#460 feeds whole instants)
    (tick,) = event.ticks
    executor = ctx.executor
    profiling_enabled = ctx.profiling_enabled
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts

    # #476: the trading day ends BEFORE the tick that crosses its boundary is applied —
    # the seal reads the account as the previous tick left it, stamps the boundary, and
    # this tick's fills, trades and valuation open the new day. The canonical clock moves
    # to the tick's instant first (on_tick keeps it there) and the boundary is checked on
    # it — the clock of the event about to be applied. Exact without heartbeats too: a
    # quiet stretch across the boundary is sealed at the boundary, as a live session's
    # heartbeat seals it. Measured 2026-10-25: 95 ns per tick for the extra set.
    executor.set_current_time(tick.timestamp)
    ctx.booking.check_boundary(executor.get_current_time(), ctx.seal_source)

    # === 1. Trade Executor (BROKER PATH — all ticks) ===
    # Broker sees every tick regardless of algo processing budget.
    # Pending order fills, SL/TP triggers, limit/stop monitoring
    # all operate on the full tick stream.
    if profiling_enabled: started = time.perf_counter()
    executor.on_tick(tick)
    record.ticks_applied += 1
    if profiling_enabled:
        profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
        profile_counts['trade_simulator'] += 1

    # === 2. Bar Rendering (all ticks — shared block, #303) ===
    # Bars must reflect the complete market data stream — including
    # clipped ticks. Clipping simulates "algo was too slow to react",
    # NOT "market data was incomplete". Same ordering as AutoTrader.
    if profiling_enabled: started = time.perf_counter()
    record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
    if profiling_enabled:
        profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
        profile_counts['bar_rendering'] += 1
    # #579 part B item 3: which bars this tick closed — read before the algo path
    # consumes them, and only when the scenario records frames
    record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                if ctx.reads_closed_bars else frozenset())

    # === 2b. Equity sample (#497) — before the clipping gate ===
    hooks.observe_market()

    # === CLIPPING GATE ===
    # Ticks flagged by tick processing budget skip the algo path.
    # The broker and bar rendering already processed them above.
    if tick.is_clipped:
        return False

    # === ALGO PATH (non-clipped ticks only) ===
    # #436 + #451: the planned stale windows and the market-data status, BEFORE the decision
    # computes — status + edge hook are visible this pass.
    hooks.prepare_algo(tick)

    # === 3+4. Bar History + Worker Processing + Decision (shared block, #303) ===
    # 'worker_decision' also covers the (tiny) bar-history retrieval — the former
    # 'bar_history' timer had no report consumer.
    if profiling_enabled: started = time.perf_counter()
    decision = execute_algo_path(
        tick=tick,
        current_bars=record.current_bars,
        bar_controller=ctx.bar_controller,
        worker_orchestrator=ctx.orchestrator,
        symbol=ctx.symbol,
    )
    if profiling_enabled:
        profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
        profile_counts['worker_decision'] += 1

    # === 5. Order Execution ===
    if profiling_enabled: started = time.perf_counter()
    try:
        order_result = ctx.decision_logic.execute_decision(decision, tick)
    except Exception as e:
        raise RuntimeError(
            f'Order execution failed: {e} \n{traceback.format_exc()}')
    if profiling_enabled:
        profile_times['order_execution'] += (time.perf_counter() - started) * 1000
        profile_counts['order_execution'] += 1

    # === 5b. Decision Event Drain (#348) ===
    # Events buffered during on_tick (fills, partial closes, cancels)
    # are delivered to the algo hooks here, before the next tick.
    decision_event_dispatcher = ctx.decision_event_dispatcher
    if decision_event_dispatcher is not None:
        if profiling_enabled: started = time.perf_counter()
        decision_event_dispatcher.drain()
        if profiling_enabled:
            profile_times['decision_events'] += (time.perf_counter() - started) * 1000
            profile_counts['decision_events'] += 1

    record.decision = decision
    record.order_result = order_result
    return True


def dispatch_backtest_heartbeat(
    ghost_msc: int,
    trade_simulator: AbstractTradeExecutor,
    worker_coordinator: WorkerOrchestrator,
    decision_logic: AbstractDecisionLogic,
    decision_event_dispatcher: Optional[DecisionEventDispatcher],
    booking: BookingPeriodRecorder,
    seal_source: Callable[[], Tuple[List[TradeRecord], PeriodSnapshot]],
) -> Optional[AlgoOrderResult]:
    """
    A backtest's ghost pass at one instant between two ticks (#360), in its order.

    Its drive is still the gap walk that generates the ghost instants, which hands each one over
    by its millisecond; the timeline driver (#460) makes them time events. No bar render and no
    tick counter: tick state stays untouched.

    Args:
        ghost_msc: The ghost instant, in UTC epoch milliseconds
        trade_simulator: The executor — the clock and the ghost broker resolution
        worker_coordinator: The orchestrator, serving the workers' cached results to the decision
        decision_logic: The opt-in decision logic, which executes the ghost action
        decision_event_dispatcher: The decision event channel (#348), drained around the ghost
            compute, or None
        booking: The scenario's booking period recorder — the trading-day boundary is checked at
            every ghost instant, because these passes are the only thing that advances the
            canonical clock across a quiet stretch and a fill can resolve at any of them (#537)
        seal_source: What a seal reads at its instant

    Returns:
        What the ghost decision sent, or None
    """
    trade_simulator.set_current_time(
        datetime.fromtimestamp(ghost_msc / 1000.0, tz=timezone.utc))
    # BEFORE the resolutions of this instant, so the boundary is the cut: what closed
    # earlier is sealed into the day that is ending, and a fill resolving AT this instant
    # falls into the day that is opening (the window is end-exclusive).
    booking.check_boundary(trade_simulator.get_current_time(), seal_source)
    trade_simulator.heartbeat()
    if decision_event_dispatcher is not None:
        decision_event_dispatcher.drain()
    decision = run_ghost_pass(worker_coordinator)
    ghost_order = None
    if decision is not None:
        ghost_order = decision_logic.execute_decision(decision, tick=None)
    if decision_event_dispatcher is not None:
        decision_event_dispatcher.drain()
    return ghost_order
