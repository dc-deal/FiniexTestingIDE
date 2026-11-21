"""
FiniexTestingIDE - Loop Event Dispatcher (#458)

The one place the order of a pass lives: which steps run, in which order, when the loop core is
handed an event. The order IS the behaviour both pipelines are held to — a divergent order is
what produced the #293 bug class — so it is written as code, one entry per kind of pass, and never
as a table.

Each runner keeps its drive and hands each pass to an entry here. The backtest's drive is its
timeline intake (#460), which hands over one instant at a time, and its entry runs the instant in
the phases of `InstantPhase` (#457). The AutoTrader takes ticks off its queue and runs a pass of its
own when none came; its entries keep the session's order of today exactly, until its timeline
driver (#461) gives it the phases too.

A pass ends with its last engine step. What a runner does after it — a state frame, the live
export, the session-end check, its timing — stays in the runner, in its order.
"""

import time
import traceback
from datetime import datetime
from typing import Optional, Tuple

from python.framework.loop.abstract_backtest_pass_hooks import AbstractBacktestPassHooks
from python.framework.loop.abstract_live_pass_hooks import AbstractLivePassHooks
from python.framework.loop.blocks.algo_compute_block import execute_algo_path
from python.framework.loop.blocks.algo_heartbeat_block import run_ghost_pass
from python.framework.loop.blocks.bar_render_block import render_bars_for_tick
from python.framework.loop.loop_context import LoopContext
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.decision_logic_types import DecisionLogicAction
from python.framework.types.loop_types.backtest_pass_kind_types import BacktestPassKind
from python.framework.types.loop_types.live_pass_kind_types import LivePassKind
from python.framework.types.loop_types.loop_event_types import (
    DataSliceEvent,
    LoopEvent,
    TimeEvent,
)
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord
from python.framework.types.loop_types.timer_kind_types import TimerKind


def dispatch_backtest_instant(
    events: Tuple[LoopEvent, ...],
    simulator: AbstractTradeExecutor,
    ctx: LoopContext,
    hooks: AbstractBacktestPassHooks,
    record: LoopPassRecord,
) -> BacktestPassKind:
    """
    One instant of a backtest, in the phases of `InstantPhase` (#457, #460).

    The instant is what the timeline intake handed over: a slice of the ticks that arrived in one
    millisecond, a due time, a heartbeat logic's timer — or several of them at one stamp. The
    clock moves to the stamp once. The world moves first: every tick of the slice reaches the
    broker path, the bars and the equity sample, in order, so a stop inside a sweep triggers.
    Then what came due resolves once, at the instant's prices; the decision events are delivered;
    the algo computes once, on the slice's last tick, when any of its ticks is unclipped (the
    most recent state, as a live algo meets it after a long computation) — or, at a timer, the
    heartbeat logic's decision runs; what it sent is executed and its events delivered.

    Args:
        events: The instant's events, all with one stamp on the arrival axis
        simulator: The scenario's executor — the same object as `ctx.executor`, typed as the
            simulation's, whose instant-level resolution this entry drives
        ctx: The scenario's collaborators
        hooks: The backtest's own steps inside the instant
        record: What the instant leaves for the drive

    Returns:
        What the instant did with the algo
    """
    stamp = events[0].ts_init
    data: Optional[DataSliceEvent] = None
    timer = False
    for event in events:
        if isinstance(event, DataSliceEvent):
            data = event
        elif isinstance(event, TimeEvent):
            timer = True
    profiling_enabled = ctx.profiling_enabled
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts

    # The clock moves to the instant once, and the trading day is checked on it BEFORE anything
    # of the instant applies (#476): the seal reads the account as the previous instant left it,
    # and this instant's fills, trades and valuation open the new day.
    simulator.begin_instant(stamp)
    ctx.booking.check_boundary(simulator.get_current_time(), ctx.seal_source)

    # === MARKET_DATA ===
    # A modify or cancel due by now applies before the first trigger check, so a cancelled order
    # does not fill in this instant (#318). Then every tick of the slice, in arrival order: the
    # broker path sees each — resting orders and the levels positions carry are checked per tick,
    # clipped or not — the bars aggregate each, and the equity is sampled after each (#497).
    # An instant without a tick times its resolutions under a key of its own, so
    # `trade_simulator` stays the cost of the ticks it counts (#460)
    resolution_key = 'trade_simulator' if data is not None else 'instant_resolution'
    if profiling_enabled: started = time.perf_counter()
    simulator.resolve_scheduled(stamp)
    if profiling_enabled:
        profile_times[resolution_key] += (time.perf_counter() - started) * 1000
    if data is not None:
        for tick in data.ticks:
            if profiling_enabled: started = time.perf_counter()
            simulator.apply_tick(tick)
            record.ticks_applied += 1
            if profiling_enabled:
                profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
                profile_counts['trade_simulator'] += 1
                started = time.perf_counter()
            record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
            if profiling_enabled:
                profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
                profile_counts['bar_rendering'] += 1
            hooks.observe_market()
        # #579 part B item 3: which bars the slice closed — read before the algo path consumes
        # them, and only when the scenario records frames or series
        record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                    if ctx.reads_closed_bars else frozenset())

    # === SIGNALS === none on a backtest's timeline yet (#463)

    # === BROKER_RESOLUTIONS === what the latency queue has due by now, at the instant's prices
    if profiling_enabled: started = time.perf_counter()
    simulator.resolve_due(stamp)
    if profiling_enabled:
        profile_times[resolution_key] += (time.perf_counter() - started) * 1000
        if data is None:
            profile_counts['instant_resolution'] += 1

    # === EVENT_DELIVERY === the fills and cancels of this instant reach the hooks before the
    # decision does; a hook that asks for the session end ends it here, before the algo
    _drain(ctx)
    if simulator.is_session_end_requested():
        return BacktestPassKind.NO_PASS

    # === ALGO_PASS → ORDER_EXECUTION → TRAILING_DRAIN ===
    if data is not None:
        slice_ticks = data.ticks
        # A slice of one tick is nearly every instant on data whose ticks rarely share a stamp
        if len(slice_ticks) == 1:
            ticks = 0 if slice_ticks[0].is_clipped else 1
        else:
            ticks = sum(1 for tick in slice_ticks if not tick.is_clipped)
        if ticks == 0:
            return BacktestPassKind.CLIPPED
        tick = data.ticks[-1]
        # #436 + #451: the planned stale windows and the market-data status, before the decision
        # computes — status and edge hook are visible this pass
        hooks.prepare_algo(tick, ticks)
        # 'worker_decision' also covers the (tiny) bar-history retrieval
        if profiling_enabled: started = time.perf_counter()
        decision = execute_algo_path(
            tick=tick,
            current_bars=record.current_bars,
            bar_controller=ctx.bar_controller,
            worker_orchestrator=ctx.orchestrator,
            symbol=ctx.symbol,
            tick_weight=ticks,
        )
        if profiling_enabled:
            profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
            profile_counts['worker_decision'] += 1
            started = time.perf_counter()
        try:
            order_result = ctx.decision_logic.execute_decision(decision, tick)
        except Exception as e:
            raise RuntimeError(
                f'Order execution failed: {e} \n{traceback.format_exc()}')
        if profiling_enabled:
            profile_times['order_execution'] += (time.perf_counter() - started) * 1000
            profile_counts['order_execution'] += 1
        _drain(ctx)
        record.decision = decision
        record.order_result = order_result
        return BacktestPassKind.TICK_PASS

    if timer:
        # #360: the heartbeat logic acts between ticks with the workers' cached results — no bar
        # render and no tick counter
        decision = run_ghost_pass(ctx.orchestrator)
        ghost_order = None
        if decision is not None:
            ghost_order = ctx.decision_logic.execute_decision(decision, tick=None)
        _drain(ctx)
        record.ghost_order = ghost_order
        return BacktestPassKind.GHOST_PASS
    return BacktestPassKind.NO_PASS


def _drain(ctx: LoopContext) -> None:
    """
    Deliver the buffered decision events to the logic's hooks (#348), timed as the backtest's
    'decision_events' step.

    Args:
        ctx: The scenario's collaborators; nothing happens without a decision event channel
    """
    dispatcher = ctx.decision_event_dispatcher
    if dispatcher is None:
        return
    if ctx.profiling_enabled:
        started = time.perf_counter()
        dispatcher.drain()
        ctx.profile_times['decision_events'] += (time.perf_counter() - started) * 1000
        ctx.profile_counts['decision_events'] += 1
        return
    dispatcher.drain()


def dispatch_live_instant(
    events: Tuple[LoopEvent, ...],
    executor: AbstractTradeExecutor,
    ctx: LoopContext,
    hooks: AbstractLivePassHooks,
    record: LoopPassRecord,
    ticks_clipped: int,
) -> LivePassKind:
    """
    One instant of an AutoTrader session, in the phases of `InstantPhase` — the order a backtest
    runs its instants in (#461).

    The instant is what the session's intake handed over: a slice of the ticks that arrived — a
    backlog coalesced into one — the venue's answers resolving, a timer, or several of them at one
    stamp. The clock moves to the stamp once. Every tick reaches the broker path, the market
    status and the bars, in order; the signals are folded in; what the venue answered resolves
    once, after all of them; the decision events are delivered; the algo computes once, on the
    newest tick — or, at the heartbeat, the heartbeat logic's decision runs; what it sent is
    executed and its events delivered. Unlike a backtest's, the pass has no clipping gate — a
    session measures how far its algo falls behind and does not skip it — and the safety layer
    samples the account after the algo.

    Args:
        events: The instant's events, all with one stamp on the receipt axis
        executor: The session's executor, whose instant-level resolution this entry drives
        ctx: The session's collaborators
        hooks: The session's own steps inside the instant
        record: What the instant leaves for the drive; the drive keeps its own tick count
        ticks_clipped: The slice's ticks the algo does not compute on — a backlog's ticks behind
            the newest arrival; the workers count the others, as a backtest counts only its
            unclipped ticks

    Returns:
        What the instant did with the algo
    """
    stamp = events[0].ts_init
    data: Optional[DataSliceEvent] = None
    timers = set()
    for event in events:
        if isinstance(event, DataSliceEvent):
            data = event
        elif isinstance(event, TimeEvent):
            timers.add(event.timer)
    record.ghost_order = None
    record.closed_timeframes = frozenset()

    # The clock moves to the instant once, and the trading day is checked on it BEFORE anything
    # of the instant applies (#476): the seal reads the account as the previous instant left it
    executor.begin_instant(stamp)
    ctx.booking.check_boundary(executor.get_current_time_if_set(), ctx.seal_source)
    hooks.close_sealed_days()

    # === MARKET_DATA === every tick, in arrival order: the broker path, the bars, then the
    # market status and the account value the bars were built on
    if data is not None:
        for tick in data.ticks:
            executor.apply_tick(tick)
            record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
            hooks.observe_market(tick)
        # #579: read before the algo path consumes them — a state frame names the bar the
        # decision just saw close
        record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                    if ctx.reads_closed_bars else frozenset())

    # === SIGNALS === #141 Part 2a: what the signal transport received since the last instant,
    # before the workers run
    hooks.refresh_signals(off_tick=data is None)

    # === BROKER_RESOLUTIONS === what the venue answered by now, after all of the slice's ticks;
    # then the cadence work a timer brought
    executor.resolve_due(stamp)
    if TimerKind.HEARTBEAT in timers:
        # #436: the last real tick's age, BEFORE the events and the ghost pass, so the decision
        # sees the stale status in this very instant
        hooks.evaluate_staleness()
    if TimerKind.RECONCILE in timers:
        hooks.reconcile()

    # === EVENT_DELIVERY === the fills and cancels of this instant reach the hooks before the
    # decision does; a hook that asks for the session end ends it here, before the algo
    _drain(ctx)
    if executor.is_session_end_requested():
        return LivePassKind.NO_PASS

    # === ALGO_PASS → ORDER_EXECUTION → TRAILING_DRAIN ===
    if data is not None:
        tick = data.ticks[-1]
        decision = execute_algo_path(
            tick=tick,
            current_bars=record.current_bars,
            bar_controller=ctx.bar_controller,
            worker_orchestrator=ctx.orchestrator,
            symbol=ctx.symbol,
            tick_weight=len(data.ticks) - ticks_clipped,
        )
        hooks.log_new_maxima()
        # Safety check (circuit breaker), on the account after the algo
        hooks.guard_account()
        if hooks.entries_blocked():
            decision.action = DecisionLogicAction.FLAT
        order_result = ctx.decision_logic.execute_decision(decision, tick)
        hooks.record_order(order_result, decision)
        _drain(ctx)
        record.decision = decision
        record.order_result = order_result
        return LivePassKind.TICK_PASS

    if TimerKind.HEARTBEAT in timers:
        # #360: the heartbeat logic acts between ticks with the workers' cached results — no
        # synthetic tick and no tick state; the safety layer reuses its last evaluated state
        decision = run_ghost_pass(ctx.orchestrator)
        if decision is not None:
            hooks.count_ghost_pass()
            if hooks.entries_blocked():
                decision.action = DecisionLogicAction.FLAT
            ghost_order = ctx.decision_logic.execute_decision(decision, tick=None)
            hooks.record_ghost_order(ghost_order, decision)
            record.ghost_order = ghost_order
        _drain(ctx)
        return LivePassKind.GHOST_PASS
    return LivePassKind.NO_PASS


def dispatch_live_order_wait(
    now: datetime,
    ctx: LoopContext,
    hooks: AbstractLivePassHooks,
    hear: bool,
) -> None:
    """
    One pass of a stop's wait for the orders on their way (#630): what an idle pass does for the
    orders, and nothing for the algo.

    No ghost pass, no signal inbox and no reconcile: the decisions have stopped, and a write is
    never repeated.

    Args:
        now: The pass's instant — the last event plus the time the wait has run
        ctx: The session's collaborators
        hooks: The session's own steps inside the pass
        hear: Whether the logic hears what this pass settles; not after the session-end event
    """
    executor = ctx.executor
    executor.set_current_time(now)
    _pass_day_boundary_off_tick(ctx, hooks)
    executor.heartbeat()
    if hear and ctx.decision_event_dispatcher is not None:
        ctx.decision_event_dispatcher.drain()


def _pass_day_boundary_off_tick(ctx: LoopContext, hooks: AbstractLivePassHooks) -> None:
    """
    The trading day's boundary, for an AutoTrader pass that carries no tick: the idle and the
    wait pass.

    Args:
        ctx: The session's collaborators
        hooks: The session's own steps inside the pass
    """
    # #537 / #476: the boundary is checked HERE too, before anything this pass
    # resolves — a fill the heartbeat drains after the boundary belongs to the day
    # it opened. The seal is stamped with the boundary, whenever it is noticed.
    ctx.booking.check_boundary(ctx.executor.get_current_time_if_set(), ctx.seal_source)
    # The sealed days close, and the log follows the booking period's day (the hook says why)
    hooks.close_sealed_days()
