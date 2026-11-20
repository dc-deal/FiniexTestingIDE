"""Probe: what does the portfolio's lazy marking save per tick, at spot and at margin?

The question arose in #653, which owns the probe. The portfolio marks its open positions lazily —
only when something asks — and at spot nothing asks on an ordinary tick: the account's value comes
from the balances, and a position is marked only when the price passes one of its extremes. With
`verification.portfolio_cache` off, every open position is marked on every tick. The difference is
what the cache saves, and what dropping it would cost.

It drives one portfolio the way the backtest's tick loop does — a new price, then the equity sample
— over synthetic ticks with 1, 5 and 20 open positions, the cache on and off, at spot and at margin,
and reports microseconds per tick. The figure moves with the machine and the code, so it is run
again after either changes. At margin the per-tick sample marks every position anyway, so the cache
saves little there; at spot it is the whole marking.

A second table times the algo mask an algo receives instead of the engine's position: making it,
and reading a stored and a computed field.

Touches nothing on disk apart from the global log header every start of the framework writes.

Recorded results:
  2026-11-05 10:18 UTC — development container (WSL2, 16 cores, load average about 8 from processes
      outside it); 50,000 ticks, the median of three runs, microseconds per tick, cache on → off:
      spot with 1 open position 1.39 → 3.17 (+1.78), with 5 2.42 → 8.55 (+6.13), with 20
      6.79 → 31.06 (+24.27) — about 1.2 µs per open position and tick; margin 2.76 → 2.80, 7.84 →
      7.79, 27.67 → 26.10, no difference, as the sample marks every position there anyway. Against
      the 1.5.0 benchmark's 61,900 ticks a second (about 16 µs a tick), dropping the cache costs a
      spot run about a tenth of its speed per open position. Not yet measured on a server.
  2026-11-05 12:02 UTC — the same container (load average about 7), with the algo masks of #653 and
      the mark at open; the lazy marking, cache on → off, µs a tick: spot 1.32 → 3.66 (1 open),
      2.35 → 11.65 (5), 5.57 → 40.46 (20); margin 3.32 → 3.44, 10.58 → 10.97, 38.45 → 39.95. An
      algo mask, ns: made once per position 395 (spot) and 433 (margin); a stored field read 44 and
      43; the computed price 1,706 and 1,822, the computed P&L 1,624 and 1,869 — a computed field
      runs the full valuation (specification, tick value, the book check). Not yet measured on a
      server.

Usage:
    python python/experiments/cost_probes/probe_portfolio_cache_cost.py [--ticks N] [--repeats N]
"""

import argparse
import math
import statistics
import time
import timeit
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.algo_masks.algo_position import AlgoPosition
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection

_START = datetime(2026, 1, 12, 10, tzinfo=timezone.utc)
_MARKETS = {
    'spot': {'config': 'configs/brokers/kraken/kraken_spot_broker_config.json', 'symbol': 'ETHUSD',
             'price': 2000.0, 'spread': 0.5, 'step': 0.25, 'currency': 'USD'},
    'margin': {'config': 'configs/brokers/mt5/mt5_broker_config.json', 'symbol': 'EURUSD',
               'price': 1.10, 'spread': 0.00012, 'step': 0.00005, 'currency': 'USD'},
}
_POSITION_COUNTS = (1, 5, 20)


class _NullLogger:
    """A logger that drops everything — the measured path logs nothing worth timing."""

    def verbose(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass


def _ticks(market: Dict, count: int) -> List[TickData]:
    """
    Synthetic ticks that wander up and down, so positions pass their extremes now and then.

    Args:
        market: The market's settings
        count: How many ticks

    Returns:
        The ticks, oldest first
    """
    ticks = []
    for index in range(count):
        mid = market['price'] + market['step'] * 40 * math.sin(index / 500) + market['step'] * (index % 7 - 3)
        ticks.append(TickData(timestamp=_START + timedelta(milliseconds=200 * index),
                              symbol=market['symbol'], bid=mid - market['spread'] / 2,
                              ask=mid + market['spread'] / 2))
    return ticks


def _portfolio(market: Dict, spot_mode: bool, positions: int, portfolio_cache: bool,
               clock: List[datetime]) -> PortfolioManager:
    """
    A portfolio holding the given number of open LONG positions.

    Args:
        market: The market's settings
        spot_mode: Whether the account is a spot account
        positions: How many positions it holds
        portfolio_cache: The verification switch
        clock: A one-element list the portfolio's clock reads

    Returns:
        The portfolio
    """
    broker_config = BrokerConfigFactory.build_broker_config(market['config'])
    spec = broker_config.get_symbol_specification(market['symbol'])
    portfolio = PortfolioManager(
        logger=_NullLogger(), initial_balance=100_000.0, account_currency=market['currency'],
        broker_config=broker_config, leverage=100, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode, clock_fn=lambda: clock[0], portfolio_cache=portfolio_cache,
        initial_balances=({market['currency']: 100_000.0, spec.base_currency: 10.0}
                          if spot_mode else None))
    for index in range(positions):
        portfolio.open_positions[f'p{index}'] = Position(
            position_id=f'p{index}', symbol=market['symbol'], direction=OrderDirection.LONG,
            lots=0.01, original_lots=0.01, entry_price=market['price'], entry_time=_START,
            entry_tick_value=1.0, digits=spec.digits, contract_size=spec.contract_size)
    return portfolio


def _microseconds_per_tick(market: Dict, spot_mode: bool, positions: int, portfolio_cache: bool,
                           ticks: List[TickData], repeats: int) -> float:
    """
    The median time per tick of the tick loop's portfolio path: a new price, then the equity sample.

    Args:
        market: The market's settings
        spot_mode: Whether the account is a spot account
        positions: How many positions are open
        portfolio_cache: The verification switch
        ticks: The ticks to feed
        repeats: How many timed runs the median is taken over

    Returns:
        Microseconds per tick
    """
    timings = []
    for _ in range(repeats):
        clock = [_START]
        portfolio = _portfolio(market, spot_mode, positions, portfolio_cache, clock)
        started = time.perf_counter()
        for tick in ticks:
            clock[0] = tick.timestamp
            portfolio.mark_dirty(tick)
            portfolio.sample_equity()
        timings.append((time.perf_counter() - started) / len(ticks) * 1e6)
    return statistics.median(timings)


def _mask_nanoseconds(spot_mode: bool, market: Dict, repeats: int) -> Dict[str, float]:
    """
    What an algo pays for a position's algo mask: making it, and reading a stored and a computed field.

    Args:
        spot_mode: Whether the account is a spot account
        market: The market's settings
        repeats: How many timed runs the minimum is taken over

    Returns:
        Nanoseconds per operation, by operation
    """
    clock = [_START]
    portfolio = _portfolio(market, spot_mode, 1, True, clock)
    tick = _ticks(market, 1)[0]
    portfolio.mark_dirty(tick)
    position = portfolio.open_positions['p0']
    mask = portfolio.get_algo_position('p0')
    number = 100_000
    operations = {
        'make a mask': lambda: AlgoPosition(position, portfolio.value_position),
        'read a stored field': lambda: mask.entry_price,
        'read the computed price': lambda: mask.current_price,
        'read the computed P&L': lambda: mask.unrealized_pnl,
    }
    return {name: min(timeit.repeat(operation, number=number, repeat=repeats)) / number * 1e9
            for name, operation in operations.items()}


def main() -> None:
    """Measure and print the two tables."""
    parser = argparse.ArgumentParser(description='The cost of the portfolio\'s lazy marking per tick')
    parser.add_argument('--ticks', type=int, default=100_000)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    print(f'{"account":8} {"open":>5} {"cache on µs":>12} {"cache off µs":>13} {"difference µs":>14}')
    for name, market in _MARKETS.items():
        ticks = _ticks(market, args.ticks)
        for positions in _POSITION_COUNTS:
            on = _microseconds_per_tick(market, name == 'spot', positions, True, ticks, args.repeats)
            off = _microseconds_per_tick(market, name == 'spot', positions, False, ticks, args.repeats)
            print(f'{name:8} {positions:>5} {on:>12.2f} {off:>13.2f} {off - on:>14.2f}')
    print()
    print(f'{"account":8} {"algo mask":24} {"ns":>8}')
    for name, market in _MARKETS.items():
        for operation, nanoseconds in _mask_nanoseconds(name == 'spot', market, args.repeats).items():
            print(f'{name:8} {operation:24} {nanoseconds:>8.0f}')


if __name__ == '__main__':
    main()
