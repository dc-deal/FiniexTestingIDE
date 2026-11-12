"""Probe: what does sending the backtest's tick pass through the loop core's dispatcher cost? (#458)

The question arose in #458, which owns the probe. The slice moves each runner's pass into one unit
that owns its order: a tick becomes a one-tick data slice (#457's event), the pass reads its
collaborators from a context and writes what the runner reads afterwards into a record, and the
runner's own steps hang on hooks. None of that may change a backtest's result, so all it adds is
cost, and #458 sets the budget: at most 1.0 µs a tick, refused above 1.5 µs. The probe settles the
event's shape before anything is built (#457 kept it frozen unless this measurement says otherwise).

It runs the pass of a backtest tick with stub collaborators — every call the real pass makes, each
doing nothing — and the real bar and algo functions, in six forms: today's inline body (V0), the
planned pass with a frozen event (V1), V1 with the broker feed, the execute and the drain as block
functions of their own (V2), V1 with an event that is not frozen (V3), V1 reached through a router
on the event's kind (V4), V1 without any event (V5), and V3 with the two stamps read inline
rather than through the slicer's helpers (V6). Each runs with the per-step profiling
timers on, as a backtest runs by default, and off. Rounds alternate the forms in a shuffled order,
and the figure for a form is its difference to V0 in the same round. The figures move with the
machine and the code, so it is run again after either changes.

Touches nothing on disk.

Recorded results:
  2026-11-12 ~15:15 UTC — development container (WSL2, 16 cores), on `line-1-loop` = `32ce382f`
      (before B's build), 25 rounds of 20,000 ticks, load average 19.7 rising to 22.8 from processes
      outside the container, so every figure is inflated and only the comparison within a round
      counts. ns per tick, the difference to V0 in the same round as minimum · median:
      profiling on — V1 frozen event 1,252 · 1,830; V2 + block functions 1,160 · 1,971; V3 not
      frozen 337 · 1,092; V6 stamps inline 46 · 1,042; V4 kind router 524 · 1,931; V5 no event
      −513 · 250. Profiling off — V1 1,159 · 1,741; V2 1,445 · 1,877; V3 316 · 922; V6 509 · 905;
      V4 1,119 · 1,891; V5 81 · 273. An earlier run at load 5 to 12.7 (15 rounds) gave the same
      picture: V1 481–1,169 · 1,925–2,008, V3 −919–621 · 897–986, V5 −1,570–−81 · 321–340.
      Read: the seam itself (V5) costs about 0.3 µs; the event is the cost — a frozen one about
      1.2–1.5 µs more, over #458's budget of 1.0 µs, one that is not frozen about 0.6 µs more; the
      slicer's stamp helpers cost nothing measurable (V6 against V3), block functions about 0.1–0.3 µs
      and a router on the kind as much as a frozen event's share. Decision, by the rule #457 set
      (frozen stays only if V1 or V2 stay within 1.0 µs): the events are made without `frozen`.
  Since that decision the framework's events are not frozen, so V1, V2 and V4 build the probe's own
      frozen copy (`_FrozenSlice`, the shape #457 shipped) and V3 the shipped event; the bar and algo
      functions moved unchanged into the loop core's blocks. A re-run compares as the runs above did.
  2026-11-12 ~17:50 UTC — the same container after the B1 build (`line-1-loop`), load average 5.1 to
      5.2, 25 rounds of 20,000 ticks; difference to V0 as minimum · median, ns per tick:
      profiling on — V1 frozen 473 · 1,226; V2 −311 · 1,441; V3 the shipped event −462 · 759; V6
      −750 · 712; V4 67 · 1,433; V5 −1,029 · 170. Profiling off — V1 954 · 1,218; V2 965 · 1,286;
      V3 477 · 711; V6 446 · 553; V4 1,087 · 1,290; V5 −52 · 221. The shipped form stays within
      #458's 1.0 µs by its median; a frozen one would not.

Usage:
    python python/experiments/cost_probes/probe_loop_dispatch_cost.py [--ticks N] [--rounds N]
"""

import argparse
import gc
import os
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, ClassVar, Dict, FrozenSet, List, Optional, Tuple

from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.tick_slicer import tick_event_msc, tick_receipt_msc
from python.framework.loop.blocks.algo_compute_block import execute_algo_path
from python.framework.loop.blocks.bar_render_block import render_bars_for_tick
from python.framework.types.loop_types.event_kind_types import EventKind
from python.framework.types.loop_types.loop_event_types import DataSliceEvent
from python.framework.types.market_types.market_data_types import TickData

_START = datetime(2026, 1, 12, 10, tzinfo=timezone.utc)
_SYMBOL = 'BTCUSD'


@dataclass(frozen=True, slots=True)
class _FrozenSlice:
    """The data slice event as #457 first shipped it, frozen (V1, V2, V4)."""
    KIND: ClassVar[EventKind] = EventKind.DATA_SLICE
    ts_event: int
    ts_init: int
    seq: int
    ticks: Tuple[TickData, ...]

    def __post_init__(self) -> None:
        if not self.ticks:
            raise ValueError('A data slice holds at least one tick')


@dataclass(slots=True)
class _UnfrozenSlice:
    """A local data slice without `frozen`, its stamps read inline (V6)."""
    KIND: ClassVar[EventKind] = EventKind.DATA_SLICE
    ts_event: int
    ts_init: int
    seq: int
    ticks: Tuple[TickData, ...]


class _Executor:
    """The calls the pass makes on the executor, each doing nothing."""

    def __init__(self) -> None:
        self._clock: Optional[datetime] = None

    def set_current_time(self, now: datetime) -> None:
        self._clock = now

    def get_current_time(self) -> Optional[datetime]:
        return self._clock

    def on_tick(self, tick: TickData) -> None:
        self._clock = tick.timestamp


class _Booking:
    def check_boundary(self, clock: Optional[datetime], seal_source: object) -> None:
        pass

    def observe_tick(self, equity: float) -> None:
        pass


class _Portfolio:
    def sample_equity(self) -> float:
        return 10_000.0


class _BarController:
    def process_tick(self, tick: TickData) -> Dict[str, object]:
        return {}

    def get_pending_closed_timeframes(self) -> FrozenSet[str]:
        return frozenset()

    def get_all_bar_history(self, symbol: str) -> Dict[str, tuple]:
        return {}

    def consume_bar_render_state(self) -> None:
        return None


class _Orchestrator:
    def process_tick(self, tick: TickData, current_bars: Dict[str, object],
                     bar_history: Dict[str, tuple], bar_render_state: None) -> str:
        return 'decision'


class _DecisionLogic:
    def execute_decision(self, decision: str, tick: Optional[TickData]) -> None:
        return None


class _EventDispatcher:
    def drain(self) -> None:
        pass


class _StressDriver:
    def on_tick(self, timestamp: datetime) -> None:
        pass


class _Tracker:
    def on_tick(self, timestamp: datetime, status: object, label: str) -> None:
        pass


@dataclass(slots=True)
class _Context:
    """The planned LoopContext, reduced to what the tick pass reads."""
    executor: _Executor
    bar_controller: _BarController
    orchestrator: _Orchestrator
    decision_logic: _DecisionLogic
    decision_event_dispatcher: Optional[_EventDispatcher]
    symbol: str
    booking: _Booking
    seal_source: object
    reads_closed_bars: bool
    profiling_enabled: bool
    profile_times: Dict[str, float]
    profile_counts: Dict[str, int]


@dataclass(slots=True)
class _Record:
    """The planned LoopPassRecord."""
    ticks_applied: int = 0
    current_bars: Optional[Dict[str, object]] = None
    closed_timeframes: FrozenSet[str] = frozenset()
    decision: Optional[str] = None
    order_result: Optional[object] = None


class _Hooks:
    """The planned backtest hooks: the equity sample before the gate, stress and tracker after it."""

    def __init__(self, portfolio: _Portfolio, booking: _Booking, ctx: _Context) -> None:
        self._portfolio = portfolio
        self._booking = booking
        self._ctx = ctx
        self._driver = _StressDriver()
        self._tracker = _Tracker()

    def observe_market(self) -> None:
        ctx = self._ctx
        if ctx.profiling_enabled:
            started = time.perf_counter()
            self._booking.observe_tick(self._portfolio.sample_equity())
            ctx.profile_times['equity_sample'] += (time.perf_counter() - started) * 1000
            ctx.profile_counts['equity_sample'] += 1
        else:
            self._booking.observe_tick(self._portfolio.sample_equity())

    def prepare_algo(self, tick: TickData) -> None:
        self._driver.on_tick(tick.timestamp)
        self._tracker.on_tick(tick.timestamp, None, '')


def _ticks(count: int) -> List[TickData]:
    """Synthetic ticks a few hundred milliseconds apart, none clipped."""
    return [TickData(timestamp=_START + timedelta(milliseconds=250 * index), symbol=_SYMBOL,
                     bid=50_000.0, ask=50_001.0,
                     time_msc=int((_START + timedelta(milliseconds=250 * index)).timestamp() * 1000),
                     collected_msc=int((_START + timedelta(milliseconds=250 * index + 20)).timestamp()
                                       * 1000))
            for index in range(count)]


# === V0 · today's inline pass ===

def _run_inline(ticks: List[TickData], ctx: _Context, portfolio: _Portfolio, hooks: _Hooks) -> None:
    """Today's body of a backtest tick pass, inline in the drive's loop."""
    executor = ctx.executor
    booking = ctx.booking
    bar_controller = ctx.bar_controller
    profiling_enabled = ctx.profiling_enabled
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts
    driver = _StressDriver()
    tracker = _Tracker()
    dispatcher = ctx.decision_event_dispatcher
    decision_logic = ctx.decision_logic
    processed_count = 0
    for tick_idx, tick in enumerate(ticks):
        executor.set_current_time(tick.timestamp)
        booking.check_boundary(executor.get_current_time(), ctx.seal_source)
        if profiling_enabled:
            started = time.perf_counter()
        executor.on_tick(tick)
        processed_count = tick_idx + 1
        if profiling_enabled:
            profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
            profile_counts['trade_simulator'] += 1
            started = time.perf_counter()
        current_bars = render_bars_for_tick(tick, bar_controller)
        if profiling_enabled:
            profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
            profile_counts['bar_rendering'] += 1
        closed = bar_controller.get_pending_closed_timeframes() if ctx.reads_closed_bars else frozenset()
        if profiling_enabled:
            started = time.perf_counter()
        booking.observe_tick(portfolio.sample_equity())
        if profiling_enabled:
            profile_times['equity_sample'] += (time.perf_counter() - started) * 1000
            profile_counts['equity_sample'] += 1
        if tick.is_clipped:
            continue
        driver.on_tick(tick.timestamp)
        tracker.on_tick(tick.timestamp, None, '')
        if profiling_enabled:
            started = time.perf_counter()
        decision = execute_algo_path(tick, current_bars, bar_controller, ctx.orchestrator, ctx.symbol)
        if profiling_enabled:
            profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
            profile_counts['worker_decision'] += 1
            started = time.perf_counter()
        try:
            order_result = decision_logic.execute_decision(decision, tick)
        except Exception as error:
            raise RuntimeError(f'Order execution failed: {error}')
        if profiling_enabled:
            profile_times['order_execution'] += (time.perf_counter() - started) * 1000
            profile_counts['order_execution'] += 1
        if dispatcher is not None:
            if profiling_enabled:
                started = time.perf_counter()
            dispatcher.drain()
            if profiling_enabled:
                profile_times['decision_events'] += (time.perf_counter() - started) * 1000
                profile_counts['decision_events'] += 1


# === V1 · the planned pass, a frozen event ===

def _slice_frozen(tick: TickData, sequencer: EventSequencer) -> _FrozenSlice:
    return _FrozenSlice(tick_event_msc(tick), tick_receipt_msc(tick), sequencer.next_seq(), (tick,))


def _slice_shipped(tick: TickData, sequencer: EventSequencer) -> DataSliceEvent:
    return DataSliceEvent(tick_event_msc(tick), tick_receipt_msc(tick), sequencer.next_seq(), (tick,))


def _slice_unfrozen_inline(tick: TickData, sequencer: EventSequencer) -> _UnfrozenSlice:
    """V3 with the two stamps read inline, for what the stamp helpers cost (V6)."""
    time_msc = tick.time_msc
    collected_msc = tick.collected_msc
    return _UnfrozenSlice(time_msc, collected_msc if collected_msc > 0 else time_msc,
                          sequencer.next_seq(), (tick,))


def _dispatch_slice(event: DataSliceEvent, ctx: _Context, hooks: _Hooks, record: _Record) -> bool:
    """The planned backtest tick pass: today's order, collaborators from the context."""
    tick = event.ticks[0]
    executor = ctx.executor
    profiling_enabled = ctx.profiling_enabled
    executor.set_current_time(tick.timestamp)
    ctx.booking.check_boundary(executor.get_current_time(), ctx.seal_source)
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts
    if profiling_enabled:
        started = time.perf_counter()
    executor.on_tick(tick)
    record.ticks_applied += 1
    if profiling_enabled:
        profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
        profile_counts['trade_simulator'] += 1
        started = time.perf_counter()
    record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
    if profiling_enabled:
        profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
        profile_counts['bar_rendering'] += 1
    record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                if ctx.reads_closed_bars else frozenset())
    hooks.observe_market()
    if tick.is_clipped:
        return False
    hooks.prepare_algo(tick)
    if profiling_enabled:
        started = time.perf_counter()
    decision = execute_algo_path(tick, record.current_bars, ctx.bar_controller, ctx.orchestrator,
                                 ctx.symbol)
    if profiling_enabled:
        profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
        profile_counts['worker_decision'] += 1
        started = time.perf_counter()
    try:
        order_result = ctx.decision_logic.execute_decision(decision, tick)
    except Exception as error:
        raise RuntimeError(f'Order execution failed: {error}')
    if profiling_enabled:
        profile_times['order_execution'] += (time.perf_counter() - started) * 1000
        profile_counts['order_execution'] += 1
    dispatcher = ctx.decision_event_dispatcher
    if dispatcher is not None:
        if profiling_enabled:
            started = time.perf_counter()
        dispatcher.drain()
        if profiling_enabled:
            profile_times['decision_events'] += (time.perf_counter() - started) * 1000
            profile_counts['decision_events'] += 1
    record.decision = decision
    record.order_result = order_result
    return True


def _run_planned(ticks: List[TickData], ctx: _Context, hooks: _Hooks,
                 make_event: Callable[[TickData, EventSequencer], object],
                 dispatch: Callable[..., bool]) -> None:
    """The drive: one event per tick, one dispatch per event."""
    sequencer = EventSequencer()
    record = _Record()
    for tick in ticks:
        if not dispatch(make_event(tick, sequencer), ctx, hooks, record):
            continue


# === V2 · feed, execute and drain as block functions ===

def _broker_feed(ctx: _Context, tick: TickData, record: _Record) -> None:
    ctx.executor.on_tick(tick)
    record.ticks_applied += 1


def _decision_execute(ctx: _Context, decision: str, tick: TickData) -> object:
    try:
        return ctx.decision_logic.execute_decision(decision, tick)
    except Exception as error:
        raise RuntimeError(f'Order execution failed: {error}')


def _event_drain(ctx: _Context) -> None:
    dispatcher = ctx.decision_event_dispatcher
    if dispatcher is not None:
        dispatcher.drain()


def _dispatch_slice_blocks(event: DataSliceEvent, ctx: _Context, hooks: _Hooks,
                           record: _Record) -> bool:
    """V1 with the feed, the execute and the drain as block functions."""
    tick = event.ticks[0]
    executor = ctx.executor
    profiling_enabled = ctx.profiling_enabled
    executor.set_current_time(tick.timestamp)
    ctx.booking.check_boundary(executor.get_current_time(), ctx.seal_source)
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts
    if profiling_enabled:
        started = time.perf_counter()
    _broker_feed(ctx, tick, record)
    if profiling_enabled:
        profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
        profile_counts['trade_simulator'] += 1
        started = time.perf_counter()
    record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
    if profiling_enabled:
        profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
        profile_counts['bar_rendering'] += 1
    record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                if ctx.reads_closed_bars else frozenset())
    hooks.observe_market()
    if tick.is_clipped:
        return False
    hooks.prepare_algo(tick)
    if profiling_enabled:
        started = time.perf_counter()
    decision = execute_algo_path(tick, record.current_bars, ctx.bar_controller, ctx.orchestrator,
                                 ctx.symbol)
    if profiling_enabled:
        profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
        profile_counts['worker_decision'] += 1
        started = time.perf_counter()
    order_result = _decision_execute(ctx, decision, tick)
    if profiling_enabled:
        profile_times['order_execution'] += (time.perf_counter() - started) * 1000
        profile_counts['order_execution'] += 1
    if profiling_enabled:
        started = time.perf_counter()
    _event_drain(ctx)
    if profiling_enabled and ctx.decision_event_dispatcher is not None:
        profile_times['decision_events'] += (time.perf_counter() - started) * 1000
        profile_counts['decision_events'] += 1
    record.decision = decision
    record.order_result = order_result
    return True


# === V4 · reached through a router on the event's kind ===

def _dispatch_routed(event: DataSliceEvent, ctx: _Context, hooks: _Hooks, record: _Record) -> bool:
    if event.KIND is EventKind.DATA_SLICE:
        return _dispatch_slice(event, ctx, hooks, record)
    return True


# === V5 · no event at all ===

def _tick_itself(tick: TickData, sequencer: EventSequencer) -> TickData:
    return tick


def _dispatch_tick(tick: TickData, ctx: _Context, hooks: _Hooks, record: _Record) -> bool:
    """V1's body, handed the tick instead of an event (V5)."""
    executor = ctx.executor
    profiling_enabled = ctx.profiling_enabled
    executor.set_current_time(tick.timestamp)
    ctx.booking.check_boundary(executor.get_current_time(), ctx.seal_source)
    profile_times = ctx.profile_times
    profile_counts = ctx.profile_counts
    if profiling_enabled:
        started = time.perf_counter()
    executor.on_tick(tick)
    record.ticks_applied += 1
    if profiling_enabled:
        profile_times['trade_simulator'] += (time.perf_counter() - started) * 1000
        profile_counts['trade_simulator'] += 1
        started = time.perf_counter()
    record.current_bars = render_bars_for_tick(tick, ctx.bar_controller)
    if profiling_enabled:
        profile_times['bar_rendering'] += (time.perf_counter() - started) * 1000
        profile_counts['bar_rendering'] += 1
    record.closed_timeframes = (ctx.bar_controller.get_pending_closed_timeframes()
                                if ctx.reads_closed_bars else frozenset())
    hooks.observe_market()
    if tick.is_clipped:
        return False
    hooks.prepare_algo(tick)
    if profiling_enabled:
        started = time.perf_counter()
    decision = execute_algo_path(tick, record.current_bars, ctx.bar_controller, ctx.orchestrator,
                                 ctx.symbol)
    if profiling_enabled:
        profile_times['worker_decision'] += (time.perf_counter() - started) * 1000
        profile_counts['worker_decision'] += 1
        started = time.perf_counter()
    try:
        order_result = ctx.decision_logic.execute_decision(decision, tick)
    except Exception as error:
        raise RuntimeError(f'Order execution failed: {error}')
    if profiling_enabled:
        profile_times['order_execution'] += (time.perf_counter() - started) * 1000
        profile_counts['order_execution'] += 1
    dispatcher = ctx.decision_event_dispatcher
    if dispatcher is not None:
        if profiling_enabled:
            started = time.perf_counter()
        dispatcher.drain()
        if profiling_enabled:
            profile_times['decision_events'] += (time.perf_counter() - started) * 1000
            profile_counts['decision_events'] += 1
    record.decision = decision
    record.order_result = order_result
    return True


def _build(profiling_enabled: bool) -> Tuple[_Context, _Portfolio, _Hooks]:
    booking = _Booking()
    ctx = _Context(
        executor=_Executor(), bar_controller=_BarController(), orchestrator=_Orchestrator(),
        decision_logic=_DecisionLogic(), decision_event_dispatcher=_EventDispatcher(),
        symbol=_SYMBOL, booking=booking, seal_source=object(), reads_closed_bars=True,
        profiling_enabled=profiling_enabled, profile_times=defaultdict(float),
        profile_counts=defaultdict(int))
    portfolio = _Portfolio()
    return ctx, portfolio, _Hooks(portfolio, booking, ctx)


def _variants() -> Dict[str, Callable[[List[TickData], _Context, _Portfolio, _Hooks], None]]:
    return {
        'V0 inline (today)': lambda ticks, ctx, portfolio, hooks: _run_inline(ticks, ctx, portfolio,
                                                                             hooks),
        'V1 planned, frozen event': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _slice_frozen, _dispatch_slice),
        'V2 V1 + block functions': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _slice_frozen, _dispatch_slice_blocks),
        'V3 V1, shipped event': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _slice_shipped, _dispatch_slice),
        'V6 V3, stamps read inline': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _slice_unfrozen_inline, _dispatch_slice),
        'V4 V1 via a kind router': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _slice_frozen, _dispatch_routed),
        'V5 V1 without an event': lambda ticks, ctx, portfolio, hooks: _run_planned(
            ticks, ctx, hooks, _tick_itself, _dispatch_tick),
    }


def _ns_per_tick(run: Callable[[List[TickData], _Context, _Portfolio, _Hooks], None],
                 ticks: List[TickData], profiling_enabled: bool) -> float:
    ctx, portfolio, hooks = _build(profiling_enabled)
    gc.collect()
    started = time.perf_counter()
    run(ticks, ctx, portfolio, hooks)
    return (time.perf_counter() - started) * 1e9 / len(ticks)


def main() -> None:
    """Time every form in shuffled, interleaved rounds and print each one's cost against V0."""
    parser = argparse.ArgumentParser(description='Cost of the loop core dispatcher seam (#458)')
    parser.add_argument('--ticks', type=int, default=20_000)
    parser.add_argument('--rounds', type=int, default=15)
    args = parser.parse_args()

    ticks = _ticks(args.ticks)
    variants = _variants()
    shuffler = random.Random(458)
    print(f'load average at start: {os.getloadavg()[0]:.1f}')
    for profiling_enabled in (True, False):
        per_round: Dict[str, List[float]] = {name: [] for name in variants}
        for _ in range(args.rounds):
            order = list(variants)
            shuffler.shuffle(order)
            for name in order:
                per_round[name].append(_ns_per_tick(variants[name], ticks, profiling_enabled))
        base = per_round['V0 inline (today)']
        print(f'\nprofiling {"on" if profiling_enabled else "off"} — ns per tick, '
              f'{args.rounds} rounds of {args.ticks:,} ticks')
        print(f'{"form":28} {"min":>8} {"median":>8} {"Δ V0 min":>9} {"Δ V0 median":>12}')
        for name, values in per_round.items():
            deltas = [value - reference for value, reference in zip(values, base)]
            print(f'{name:28} {min(values):8.0f} {statistics.median(values):8.0f} '
                  f'{min(deltas):9.0f} {statistics.median(deltas):12.0f}')
    print(f'\nload average at end: {os.getloadavg()[0]:.1f}')


if __name__ == '__main__':
    main()
