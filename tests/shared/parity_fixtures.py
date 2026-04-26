"""
FiniexTestingIDE - Parity Test Fixtures

Shared helpers for dual-pipeline parity tests (simulation vs. AutoTrader).
Generates deterministic synthetic tick data and converts it into the
input format each pipeline expects.

Synthetic tick generation uses a seeded RNG — identical output on every call,
no real market data committed to the repo.
"""

import queue
import random
from datetime import datetime, timezone
from typing import List, Tuple

from python.framework.types.market_types.market_data_types import TickData


# Fixture parameters — change only together with any committed parquet fixtures
_FIXTURE_SEED = 42
_BASE_TIME = datetime(2026, 1, 24, 10, 0, 0, tzinfo=timezone.utc)
_BASE_PRICE_BTCUSD = 94500.0
_SPREAD_BTCUSD = 7.0
_TICK_INTERVAL_MS = 1000  # 1 tick per second


def make_synthetic_btcusd_ticks(count: int = 1000) -> List[TickData]:
    """
    Generate deterministic synthetic BTCUSD ticks for parity tests.

    Prices walk around the base with bounded random increments.
    RNG is seeded — same count always produces the same sequence.

    Args:
        count: Number of ticks to generate (default 1000)

    Returns:
        List of TickData sorted by time_msc ascending
    """
    rng = random.Random(_FIXTURE_SEED)
    ticks: List[TickData] = []
    price = _BASE_PRICE_BTCUSD
    base_msc = int(_BASE_TIME.timestamp() * 1000)
    price_floor = _BASE_PRICE_BTCUSD * 0.90
    price_ceil = _BASE_PRICE_BTCUSD * 1.10

    for i in range(count):
        price += rng.uniform(-50.0, 50.0)
        price = max(price_floor, min(price_ceil, price))
        bid = round(price, 2)
        ask = round(bid + _SPREAD_BTCUSD, 2)
        volume = round(rng.uniform(0.001, 0.1), 6)
        time_msc = base_msc + i * _TICK_INTERVAL_MS
        ts = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)

        ticks.append(TickData(
            timestamp=ts,
            symbol='BTCUSD',
            bid=bid,
            ask=ask,
            volume=volume,
            time_msc=time_msc,
            collected_msc=time_msc,
        ))

    return ticks


def to_simulation_input(ticks: List[TickData]) -> Tuple[TickData, ...]:
    """
    Convert tick list to simulation input format.

    Args:
        ticks: Tick list

    Returns:
        Immutable tuple as expected by execute_tick_loop()
    """
    return tuple(ticks)


def to_autotrader_queue(ticks: List[TickData]) -> 'queue.Queue[TickData]':
    """
    Convert tick list to a pre-filled Queue with a None sentinel.

    The sentinel signals AutotraderTickLoop.run() that the source is exhausted.

    Args:
        ticks: Tick list to enqueue

    Returns:
        Queue pre-filled with all ticks + None sentinel
    """
    q: queue.Queue = queue.Queue()
    for tick in ticks:
        q.put(tick)
    q.put(None)
    return q
