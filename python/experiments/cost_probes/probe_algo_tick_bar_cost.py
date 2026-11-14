"""Probe: what does it cost to hand the workers ticks and bars they cannot change? (#654)

The question arose in #654, which owns the probe. Every worker and the decision received the one
tick the engine and the bar renderer use, the renderer's own forming bars, and a history list of the
renderer's own closed bars that every worker shared. The issue asked that none of them could change
what the next one reads, and settled the form by a measurement, because it is the hottest path of
both pipelines. On the ways it compared, #654 chose: the tick behind a mask (`AlgoTick`), the closed
bars as frozen `Bar`s in a tuple, and the forming bar behind a mask (`AlgoBar`) made once per bar.

It times making each kind, reading its fields the way a worker does — `tick.price`, the closes of a
history window into a numpy array — the memory a tick takes, since a backtest holds every tick of
its window, and what a pass now adds: finding the tick's mask again and the forming bars' mapping.
The alternatives that were not chosen are declared in this file; the chosen ones are the
framework's own, and the bar before #654 is kept here as `_PlainBar`, so a re-run still compares
against it. The figures move with the machine and the code, so it is run again after either changes.

Touches nothing on disk.

Recorded results:
  2026-11-12 09:01 UTC — development container (WSL2, 16 cores, load average about 8 from processes
      outside it), the median of seven timings, on `dev-1-6-0` = `b52fcedd`, before the build — so
      "today" there is the bar before #654. A tick — ns to make,
      ns to read `price` and `timestamp`, bytes held with its values: today's TickData 490 · 136 ·
      30 · 284; slots only 437 · 103 · 30 · 244; frozen with slots 1,348 · 108 · 30 · 244; a named
      tuple 467 · 142 · 40 · 260; a mask over today's 259 · 138 · 46 · 324 (mask and tick together).
      A bar close, ns: a frozen copy 1,350, a mask 251; the history of 1,000 bars as a list 4,006,
      as a tuple 4,199. A worker reading a window's closes into numpy, ns at 20 · 200 · 1,000 bars:
      today 1,410 · 9,401 · 41,367; frozen copies 1,370 · 8,549 · 43,181; masks 2,462 · 14,603 ·
      68,895. The forming bar, ns per tick for three fields: read directly 66, through a mask kept
      until the bar closes 113, a frozen copy made per tick 1,788. Not yet measured on a server.
      What to set them against: the 1.5.0 benchmark's 61,900 ticks a second add up its parallel
      scenarios, so they are no figure for one tick; one backtest process spent about 0.38 ms on a
      tick there, 0.28 ms of it on the workers and the decision (the report's operation averages).
  2026-11-12 10:35 UTC — the same container (load average about 18), the median of three timings,
      after the build, with the framework's own types. AlgoTick: made in 343 ns, `price` 163 ns,
      `timestamp` 53 ns. A window of closes, ns at 20 · 200 · 1,000 bars: before #654 1,860 · 12,594 ·
      49,299; frozen Bars 1,730 · 9,886 · 43,747; AlgoBar masks 1,819 · 13,596 · 70,217. The forming
      bar, three fields: directly 71 ns, through its AlgoBar 136 ns, a frozen copy per tick 1,889 ns.
      What a pass adds: finding the tick's mask again for execute_decision 75 ns; the forming bars'
      mapping 423 ns with one timeframe, 625 ns with three. A pass with three timeframes thus pays
      about 1 µs more — about 0.3 % of the 0.38 ms a backtest process spends on a tick.
  2026-11-12 11:40 UTC — the whole package against its base, on the load test's own pass (RSI on M5,
      Bollinger on M30, aggressive_trend; 90,000 synthetic ticks, the orchestrator's process_tick,
      blocks of 5,000), five rounds alternating between a tree at `b52fcedd` and one with the package,
      load average 7 to 8: base 89.6 · 84.6 · 87.8 · 99.1 · 81.2 µs a pass (median 87.8), with the
      package 84.9 · 105.5 · 117.6 · 91.6 · 84.1 (median 91.6) — about 3 µs a pass, 3 to 4 % of the
      worker pass and about 1 % of a tick. A profile of the same pass put it on making and finding the
      tick's mask, the forming bars' mapping and the extra layer on a read. The throughput benchmark
      run the same way an hour earlier (load rising from 13 to 31 outside the container) showed
      8 % — within its noise on this laptop, and it says nothing smaller.

Usage:
    python python/experiments/cost_probes/probe_algo_tick_bar_cost.py [--repeats N]
"""

import argparse
import gc
import statistics
import timeit
import tracemalloc
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Callable, Dict, List, Mapping, NamedTuple, Optional, Tuple

import numpy as np

from python.framework.trading_env.algo_masks.algo_bar import AlgoBar
from python.framework.trading_env.algo_masks.algo_tick import AlgoTick
from python.framework.types.market_types.market_data_types import Bar, FormingBar, TickData

_START = datetime(2026, 1, 12, 10, tzinfo=timezone.utc)
_WINDOWS = (20, 200, 1000)


@dataclass(frozen=True, slots=True)
class _FrozenTick:
    """Today's TickData, frozen and with slots."""
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    volume: float = 0.0
    time_msc: int = 0
    collected_msc: int = 0
    is_clipped: bool = False
    last: Optional[float] = None

    @property
    def mid(self) -> float:
        """Mid price."""
        return (self.bid + self.ask) / 2.0

    @property
    def price(self) -> float:
        """The strategy's price."""
        return self.last if self.last is not None else self.mid


@dataclass(slots=True)
class _SlotsTick:
    """Today's TickData with slots, not frozen — what slots alone cost and save."""
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    volume: float = 0.0
    time_msc: int = 0
    collected_msc: int = 0
    is_clipped: bool = False
    last: Optional[float] = None

    @property
    def mid(self) -> float:
        """Mid price."""
        return (self.bid + self.ask) / 2.0

    @property
    def price(self) -> float:
        """The strategy's price."""
        return self.last if self.last is not None else self.mid


class _TupleTick(NamedTuple):
    """Today's TickData as a named tuple — immutable without a frozen dataclass's slow init."""
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    volume: float = 0.0
    time_msc: int = 0
    collected_msc: int = 0
    is_clipped: bool = False
    last: Optional[float] = None

    @property
    def mid(self) -> float:
        """Mid price."""
        return (self.bid + self.ask) / 2.0

    @property
    def price(self) -> float:
        """The strategy's price."""
        return self.last if self.last is not None else self.mid


@dataclass
class _PlainBar:
    """The Bar before #654: a plain dataclass every reader could write to — the baseline."""
    symbol: str
    timeframe: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    tick_count: int = 0
    is_complete: bool = False


def _median_ns(statement: Callable[[], object], number: int, repeats: int) -> float:
    """
    Median nanoseconds of one call.

    Args:
        statement: The call to time
        number: Calls per timing
        repeats: Timings

    Returns:
        The median, in nanoseconds per call
    """
    timings = timeit.repeat(statement, number=number, repeat=repeats)
    return statistics.median(timings) / number * 1e9


def _tick_kwargs(index: int) -> Dict[str, object]:
    """
    One tick's arguments, built the way the backtest's deserialiser builds them — fresh values.

    Args:
        index: The tick's position

    Returns:
        The keyword arguments
    """
    return {'timestamp': _START + timedelta(milliseconds=250 * index), 'symbol': 'ETHUSD',
            'bid': float(2000.0 + index * 0.01), 'ask': float(2000.5 + index * 0.01),
            'volume': float(0.1), 'time_msc': 1_768_212_000_000 + 250 * index,
            'collected_msc': 0, 'is_clipped': False, 'last': None}


def _bytes_per_tick(make: Callable[[int], object], count: int) -> float:
    """
    The memory one tick holds, its field values included, as the backtest holds its window.

    Args:
        make: Builds the tick at a position
        count: Ticks to hold

    Returns:
        Bytes per tick
    """
    gc.collect()
    tracemalloc.start()
    held = [make(index) for index in range(count)]
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del held
    return current / count


def _bar(index: int) -> _PlainBar:
    """
    One closed bar as the renderer archived it before #654.

    Args:
        index: The bar's position

    Returns:
        The bar
    """
    close = 2000.0 + index * 0.1
    return _PlainBar(symbol='ETHUSD', timeframe='M5', timestamp=f'2026-01-12T10:{index % 60:02d}:00+00:00',
               open=close - 0.2, high=close + 0.3, low=close - 0.4, close=close, volume=12.5,
               tick_count=240, is_complete=True)


def _frozen_copy(bar: _PlainBar) -> Bar:
    """
    A frozen copy of a closed bar, as the renderer makes it once, at the close.

    Args:
        bar: A bar

    Returns:
        The copy
    """
    return Bar(bar.symbol, bar.timeframe, bar.timestamp, bar.open, bar.high, bar.low,
               bar.close, bar.volume, bar.tick_count, bar.is_complete)


def _forming(bar: _PlainBar) -> FormingBar:
    """
    A forming bar with a bar's fields, as the renderer builds it.

    Args:
        bar: A bar

    Returns:
        The forming bar
    """
    return FormingBar(bar.symbol, bar.timeframe, bar.timestamp, bar.open, bar.high, bar.low,
                      bar.close, bar.volume, bar.tick_count, bar.is_complete)


def _forming_bars_mapping(masks: Dict[str, AlgoBar],
                          forming_bars: Dict[str, FormingBar]) -> Mapping[str, AlgoBar]:
    """
    What a pass does for the forming bars: the orchestrator's step, written out to be timed.

    Args:
        masks: The masks kept so far, by timeframe
        forming_bars: The renderer's forming bars, by timeframe

    Returns:
        The read-only mapping the workers receive
    """
    current: Dict[str, AlgoBar] = {}
    for timeframe, bar in forming_bars.items():
        mask = masks.get(timeframe)
        if mask is None or not mask.is_mask_of(bar):
            mask = AlgoBar(bar)
            masks[timeframe] = mask
        current[timeframe] = mask
    return MappingProxyType(current)


def _tick_table(repeats: int) -> List[Tuple[str, float, float, float, float]]:
    """
    Making a tick, reading it, and what it holds — per kind.

    Args:
        repeats: Timings per figure

    Returns:
        Rows: kind, make ns, read price ns, read timestamp ns, bytes per tick
    """
    kwargs = _tick_kwargs(1)
    plain = TickData(**kwargs)
    slots = _SlotsTick(**kwargs)
    frozen = _FrozenTick(**kwargs)
    named = _TupleTick(**kwargs)
    mask = AlgoTick(plain)
    rows = []
    for name, make, tick, held in (
            ('TickData today', lambda: TickData(**kwargs), plain,
             lambda i: TickData(**_tick_kwargs(i))),
            ('slots only', lambda: _SlotsTick(**kwargs), slots,
             lambda i: _SlotsTick(**_tick_kwargs(i))),
            ('frozen, slots', lambda: _FrozenTick(**kwargs), frozen,
             lambda i: _FrozenTick(**_tick_kwargs(i))),
            ('named tuple', lambda: _TupleTick(**kwargs), named,
             lambda i: _TupleTick(**_tick_kwargs(i))),
            ('AlgoTick', lambda: AlgoTick(plain), mask,
             lambda i: AlgoTick(TickData(**_tick_kwargs(i))))):
        rows.append((name,
                     _median_ns(make, 200_000, repeats),
                     _median_ns(lambda t=tick: t.price, 1_000_000, repeats),
                     _median_ns(lambda t=tick: t.timestamp, 1_000_000, repeats),
                     _bytes_per_tick(held, 100_000)))
    return rows


def _bar_tables(repeats: int) -> Dict[str, object]:
    """
    Closing a bar, materialising a history, a worker reading a window, and the forming bar.

    Args:
        repeats: Timings per figure

    Returns:
        The figures, keyed by what they time
    """
    bars = [_bar(i) for i in range(1000)]
    kinds = {'before #654': bars, 'frozen Bar': [_frozen_copy(b) for b in bars],
             'AlgoBar': [AlgoBar(_forming(b)) for b in bars]}
    store = deque(bars, maxlen=1000)
    sample = bars[-1]
    figures: Dict[str, object] = {
        'archive frozen copy': _median_ns(lambda: _frozen_copy(sample), 200_000, repeats),
        'mask with its forming bar': _median_ns(lambda: AlgoBar(_forming(sample)), 200_000, repeats),
        'history list(deque 1000)': _median_ns(lambda: list(store), 20_000, repeats),
        'history tuple(deque 1000)': _median_ns(lambda: tuple(store), 20_000, repeats),
    }
    windows = {}
    for name, history in kinds.items():
        as_tuple = tuple(history)
        windows[name] = [
            _median_ns(lambda h=as_tuple, w=w: np.array([b.close for b in h[-w:]]),
                       max(2_000, 200_000 // w), repeats)
            for w in _WINDOWS]
    figures['window'] = windows
    forming = _forming(_bar(5))
    reused = AlgoBar(forming)
    figures['forming plain read 3'] = _median_ns(
        lambda: (forming.close, forming.high, forming.low), 1_000_000, repeats)
    figures['forming mask read 3'] = _median_ns(
        lambda: (reused.close, reused.high, reused.low), 1_000_000, repeats)
    figures['forming copy per tick + read 3'] = _median_ns(
        lambda: (lambda c: (c.close, c.high, c.low))(forming.to_bar()), 200_000, repeats)
    return figures


def _pass_table(repeats: int) -> Dict[str, float]:
    """
    What a pass adds: the tick's mask made and found again, the forming bars' mapping.

    Args:
        repeats: Timings per figure

    Returns:
        ns per pass, keyed by what they time
    """
    tick = TickData(**_tick_kwargs(1))
    kept = AlgoTick(tick)
    figures = {
        'tick mask found again (execute_decision)': _median_ns(
            lambda: kept if kept.is_mask_of(tick) else AlgoTick(tick), 1_000_000, repeats),
    }
    for count in (1, 3):
        forming_bars = {f'M{index + 1}': _forming(_bar(index)) for index in range(count)}
        masks: Dict[str, AlgoBar] = {}
        _forming_bars_mapping(masks, forming_bars)
        figures[f'forming bars mapping, {count} timeframe(s)'] = _median_ns(
            lambda f=forming_bars, m=masks: _forming_bars_mapping(m, f), 500_000, repeats)
    return figures


def main() -> None:
    """Run the probe and print its tables."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repeats', type=int, default=5, help='timings per figure (median taken)')
    args = parser.parse_args()

    print('Ticks — ns to make one, ns per field read, bytes held per tick (values included)')
    print(f"  {'kind':<18}{'make':>8}{'price':>8}{'stamp':>8}{'bytes':>8}")
    for name, make, price, stamp, size in _tick_table(args.repeats):
        print(f'  {name:<18}{make:>8.0f}{price:>8.1f}{stamp:>8.1f}{size:>8.0f}')

    figures = _bar_tables(args.repeats)
    print('\nA bar close — ns, once per closed bar and timeframe')
    for key in ('archive frozen copy', 'mask with its forming bar', 'history list(deque 1000)',
                'history tuple(deque 1000)'):
        print(f'  {key:<28}{figures[key]:>10.0f}')

    print('\nA worker reading the closes of a window into numpy — ns per read')
    print(f"  {'kind':<18}" + ''.join(f'{"w=" + str(w):>10}' for w in _WINDOWS))
    for name, row in figures['window'].items():
        print(f'  {name:<18}' + ''.join(f'{value:>10.0f}' for value in row))

    print('\nThe forming bar — ns per tick, three fields read')
    for key in ('forming plain read 3', 'forming mask read 3', 'forming copy per tick + read 3'):
        print(f'  {key:<32}{figures[key]:>8.1f}')

    print('\nWhat a pass adds — ns per pass (the tick mask itself is made once, see above)')
    for key, value in _pass_table(args.repeats).items():
        print(f'  {key:<42}{value:>8.1f}')


if __name__ == '__main__':
    main()
