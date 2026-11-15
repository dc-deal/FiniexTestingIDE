"""Probe: what does the backtest's timeline intake cost per tick? (#460)

The question arose in #460, which owns the probe. The timeline driver replaces the backtest's walk
over its tick array with an intake that merges three sources by their stamp — the ticks, grouped
into one slice per arrival millisecond; the simulator's next due time; a heartbeat logic's timers —
and hands the dispatcher one instant at a time. Everything the pass does inside an instant was
measured for #458; this probe measures only what the intake adds: grouping the ticks, asking for the
next due time, and merging. #460 budgets the whole mechanism at 1.5 µs a tick on MT5 data, where
almost no ticks share a stamp and nothing is saved by grouping.

It runs three forms over a synthetic tick array: today's drive, one single-tick slice per tick
(I0); an intake that merges through the event timeline of #457, a heap, refilled after every
instant (I1); and an intake that compares the next data stamp and the next due time directly and
builds the instant itself (I2). Each runs on two arrays — MT5-like, where every tick has its own
arrival millisecond, and Kraken-like, where about half the ticks share one with a neighbour — and
with no order in flight or with one in flight about every fiftieth instant, due 30 ms later. Rounds
alternate the forms in a shuffled order, and the figure for a form is its difference to I0 in the
same round, per tick. A fourth form, I3, is I2 with the arrival stamps read once into a list and a
slice of one tick built without a loop. The figures move with the machine and the code, so it is
run again after either changes.

Touches nothing on disk.

Recorded results:
  2026-11-13 ~09:50 UTC — development container (WSL2, 16 cores), on `line-1-timeline` = `616e0aa7`,
      load average 10.5 to 11.2 from processes outside the container, 15 rounds of 50,000 ticks;
      difference to I0 as minimum · median, ns per tick:
      MT5-like, no order in flight — I1 heap 1,394 · 1,540; I2 direct 635 · 815; I3 direct, fast
      52 · 213. With orders in flight — I1 1,444 · 1,665; I2 734 · 852; I3 136 · 251.
      Kraken-like, no order in flight — I1 510 · 635; I2 131 · 240; I3 −154 · −67. With orders in
      flight — I1 544 · 668; I2 232 · 306; I3 −66 · −16.
      Read: the heap timeline alone costs about 1.5 µs a tick where every tick is its own instant —
      #460's whole budget; comparing two stamps directly costs about 0.2 µs once the arrival stamps
      are read in one pass, and on grouped data the intake is cheaper than today's drive, because it
      makes fewer events. Decision: the intake merges directly, with the stamps read once, and does
      not use the heap; the timeline of #457 stays the live driver's (#461) to weigh.
  2026-11-13 ~21:00 UTC — checked against the built driver, not with this probe: the code before
      #460 (`616e0aa7`) against the built tree, the benchmark's load test cut to two scenarios
      (66,187 MT5 USDJPY ticks of September 2025, ordered by event time and almost never sharing a
      millisecond), runs alternating in both orders, load 5 to 7, per-tick figures from each run's
      profiling. Ten pairs in two series: the time outside the timed steps +2.4 µs (2.2 to 2.7),
      the simulator's step −1.2 µs (a quick answer when nothing is in flight), together +1.25 µs a
      tick; about 0.3 µs of that is the two extra profiling stamps around an instant's resolutions,
      paid only when a run profiles. The algo path moved between +0.4 and +10.7 µs from pair to
      pair; with garbage collection off, one profile per tree shows the same indicator time on both.
      Read: inside #460's 1.5 µs a tick.

Usage:
    python python/experiments/cost_probes/probe_sim_intake_cost.py [--ticks N] [--rounds N]
"""

import argparse
import gc
import os
import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.event_timeline import EventTimeline
from python.framework.loop.tick_slicer import slice_of_tick, tick_event_msc, tick_receipt_msc
from python.framework.types.loop_types.event_kind_types import EventOrdering
from python.framework.types.loop_types.loop_event_types import DataSliceEvent, ResolutionEvent
from python.framework.types.market_types.market_data_types import TickData

_START = datetime(2026, 4, 1, 10, tzinfo=timezone.utc)
_SYMBOL = 'BTCUSD'
_DUE_EVERY_INSTANTS = 50
_DUE_AFTER_MS = 30


class _DueSource:
    """
    A stand-in for the simulator's due times: one order in flight about every fiftieth instant.

    Args:
        active: Whether orders are sent at all
    """

    def __init__(self, active: bool) -> None:
        self._active = active
        self._due: Optional[int] = None
        self._instants = 0

    def peek_next_due_msc(self) -> Optional[int]:
        """
        The next due time, as the simulator would answer it.

        Returns:
            The due stamp, or None when nothing is in flight
        """
        return self._due

    def after_instant(self, stamp: int) -> None:
        """
        What an instant did to the orders: resolve what was due, now and then send one.

        Args:
            stamp: The instant's stamp
        """
        if self._due is not None and stamp >= self._due:
            self._due = None
        self._instants += 1
        if self._active and self._due is None and self._instants % _DUE_EVERY_INSTANTS == 0:
            self._due = stamp + _DUE_AFTER_MS


def _ticks(count: int, shared_share: float, seed: int) -> List[TickData]:
    """
    A synthetic tick array, arrival-ordered.

    Args:
        count: How many ticks
        shared_share: The share of ticks that arrive in the same millisecond as the one before
        seed: The random seed

    Returns:
        The ticks
    """
    rng = random.Random(seed)
    ticks: List[TickData] = []
    msc = int(_START.timestamp() * 1000)
    for _ in range(count):
        if not ticks or rng.random() >= shared_share:
            msc += rng.randint(1, 400)
        ticks.append(TickData(
            timestamp=datetime.fromtimestamp((msc - 20) / 1000, tz=timezone.utc), symbol=_SYMBOL,
            bid=60_000.0, ask=60_001.0, time_msc=msc - 20, collected_msc=msc))
    return ticks


def _today(ticks: List[TickData], due: _DueSource) -> int:
    """
    I0 — today's drive: one single-tick slice per tick, no merge.

    Args:
        ticks: The tick array
        due: Unused; today's drive does not ask for due times

    Returns:
        How many instants were handed over
    """
    sequencer = EventSequencer()
    handed = 0
    for tick in ticks:
        event = slice_of_tick(tick, sequencer)
        handed += len(event.ticks)
    return handed


def _next_slice(ticks: List[TickData], cursor: int, sequencer: EventSequencer
                ) -> Tuple[Optional[DataSliceEvent], int]:
    """
    The ticks that arrived in the next arrival millisecond, as one slice.

    Args:
        ticks: The tick array
        cursor: Where the next slice begins
        sequencer: The run's sequencer

    Returns:
        The slice (None at the end) and where the one after it begins
    """
    if cursor >= len(ticks):
        return None, cursor
    stamp = tick_receipt_msc(ticks[cursor])
    end = cursor + 1
    while end < len(ticks) and tick_receipt_msc(ticks[end]) == stamp:
        end += 1
    group = tuple(ticks[cursor:end])
    return DataSliceEvent(max(tick_event_msc(tick) for tick in group), stamp,
                          sequencer.next_seq(), group), end


def _heap_intake(ticks: List[TickData], due: _DueSource) -> int:
    """
    I1 — the intake through #457's event timeline, refilled after every instant.

    Args:
        ticks: The tick array
        due: The due times

    Returns:
        How many ticks were handed over
    """
    sequencer = EventSequencer()
    timeline = EventTimeline(EventOrdering.BY_TS_INIT)
    data, cursor = _next_slice(ticks, 0, sequencer)
    if data is not None:
        timeline.push(data)
    queued_due: Optional[int] = None
    handed = 0
    while len(timeline):
        next_due = due.peek_next_due_msc()
        if next_due is not None and (queued_due is None or next_due < queued_due):
            timeline.push(ResolutionEvent(next_due, next_due, sequencer.next_seq()))
            queued_due = next_due
        instant = timeline.pop_instant()
        stamp = instant[0].ts_init
        for event in instant:
            if isinstance(event, DataSliceEvent):
                handed += len(event.ticks)
                data, cursor = _next_slice(ticks, cursor, sequencer)
                if data is not None:
                    timeline.push(data)
            else:
                queued_due = None
        due.after_instant(stamp)
    return handed


def _direct_intake(ticks: List[TickData], due: _DueSource) -> int:
    """
    I2 — the intake comparing the next data stamp with the next due time directly.

    Args:
        ticks: The tick array
        due: The due times

    Returns:
        How many ticks were handed over
    """
    sequencer = EventSequencer()
    data, cursor = _next_slice(ticks, 0, sequencer)
    handed = 0
    while data is not None:
        next_due = due.peek_next_due_msc()
        if next_due is not None and next_due < data.ts_init:
            due.after_instant(next_due)
            continue
        if next_due is not None and next_due == data.ts_init:
            instant = (data, ResolutionEvent(next_due, next_due, sequencer.next_seq()))
        else:
            instant = (data,)
        handed += len(instant[0].ticks)
        stamp = data.ts_init
        data, cursor = _next_slice(ticks, cursor, sequencer)
        due.after_instant(stamp)
    return handed


def _direct_fast_intake(ticks: List[TickData], due: _DueSource) -> int:
    """
    I3 — I2 with the arrival stamps read once into a list and a fast path for a slice of one tick.

    Args:
        ticks: The tick array
        due: The due times

    Returns:
        How many ticks were handed over
    """
    sequencer = EventSequencer()
    stamps = [tick.collected_msc if tick.collected_msc > 0 else tick.time_msc for tick in ticks]
    count = len(ticks)
    cursor = 0
    handed = 0
    while cursor < count:
        stamp = stamps[cursor]
        end = cursor + 1
        while end < count and stamps[end] == stamp:
            end += 1
        next_due = due.peek_next_due_msc()
        if next_due is not None and next_due < stamp:
            due.after_instant(next_due)
            continue
        if end == cursor + 1:
            tick = ticks[cursor]
            data = DataSliceEvent(tick.time_msc, stamp, sequencer.next_seq(), (tick,))
        else:
            group = tuple(ticks[cursor:end])
            data = DataSliceEvent(group[-1].time_msc, stamp, sequencer.next_seq(), group)
        if next_due is not None and next_due == stamp:
            instant = (data, ResolutionEvent(next_due, next_due, sequencer.next_seq()))
        else:
            instant = (data,)
        handed += len(instant[0].ticks)
        cursor = end
        due.after_instant(stamp)
    return handed


_FORMS: Dict[str, Callable[[List[TickData], _DueSource], int]] = {
    'I0 today': _today,
    'I1 heap timeline': _heap_intake,
    'I2 direct merge': _direct_intake,
    'I3 direct, fast': _direct_fast_intake,
}


def _time_form(form: Callable[[List[TickData], _DueSource], int], ticks: List[TickData],
               with_due: bool) -> float:
    """
    One timed run of a form.

    Args:
        form: The form
        ticks: The tick array
        with_due: Whether orders are in flight now and then

    Returns:
        Nanoseconds per tick
    """
    due = _DueSource(with_due)
    gc.collect()
    started = time.perf_counter_ns()
    handed = form(ticks, due)
    elapsed = time.perf_counter_ns() - started
    assert handed == len(ticks), f'{handed} of {len(ticks)} ticks handed over'
    return elapsed / len(ticks)


def main() -> None:
    """Run every form on both arrays, with and without orders in flight, and print the differences."""
    parser = argparse.ArgumentParser(description='The timeline intake\'s cost per tick (#460)')
    parser.add_argument('--ticks', type=int, default=50_000)
    parser.add_argument('--rounds', type=int, default=20)
    args = parser.parse_args()

    arrays = {'MT5-like (0 % shared)': _ticks(args.ticks, 0.0, 1),
              'Kraken-like (50 % shared)': _ticks(args.ticks, 0.5, 2)}
    rng = random.Random(7)
    print(f'load average {os.getloadavg()[0]:.1f} · {args.rounds} rounds of {args.ticks:,} ticks')
    for array_name, ticks in arrays.items():
        instants = len({tick.collected_msc for tick in ticks})
        for with_due in (False, True):
            diffs: Dict[str, List[float]] = {name: [] for name in _FORMS if name != 'I0 today'}
            for _ in range(args.rounds):
                order = list(_FORMS)
                rng.shuffle(order)
                timings = {name: _time_form(_FORMS[name], ticks, with_due) for name in order}
                for name in diffs:
                    diffs[name].append(timings[name] - timings['I0 today'])
            print(f'{array_name}, {instants:,} instants, orders in flight: {with_due}')
            for name, values in diffs.items():
                print(f'    {name:18s} vs I0: min {min(values):7.0f} · median '
                      f'{statistics.median(values):7.0f} ns per tick')


if __name__ == '__main__':
    main()
