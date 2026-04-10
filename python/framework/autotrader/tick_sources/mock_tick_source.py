"""
FiniexTestingIDE - Mock Tick Source
Parquet-based tick replay for AutoTrader testing without live data.
"""

import queue
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.types.market_types.market_data_types import TickData


class MockTickSource(AbstractTickSource):
    """
    Mock tick source that replays ticks from a parquet file.

    Two modes:
    - 'replay' (default): Emit ticks as fast as possible (functional testing)
    - 'realtime': Sleep between ticks based on collected_msc delta (clipping testing)

    Runs in a separate thread. Pushes TickData objects to a queue.Queue
    that the main algo thread consumes (Threading model 8.a).

    Args:
        parquet_path: Path to parquet tick data file
        symbol: Trading symbol (e.g., 'BTCUSD')
        tick_queue: Thread-safe queue for tick delivery to main thread
        mode: 'replay' or 'realtime'
        tick_delay_ms: Artificial per-tick delay in ms (replay mode only, 0 = full speed)
    """

    def __init__(
        self,
        parquet_path: str,
        symbol: str,
        tick_queue: queue.Queue,
        mode: str = 'replay',
        max_ticks: int = 0,
        tick_delay_ms: int = 0,
    ):
        self._parquet_path = parquet_path
        self._symbol = symbol
        self._tick_queue = tick_queue
        self._mode = mode
        self._max_ticks = max_ticks  # 0 = no limit
        self._tick_delay_s = tick_delay_ms / 1000.0 if tick_delay_ms > 0 else 0.0
        self._running = False
        self._exhausted = False
        self._ticks: List[TickData] = []
        self._ticks_emitted: int = 0

    def start(self) -> None:
        """
        Load ticks from parquet and push them to the queue.

        Called from the tick source thread. Blocks until all ticks
        are emitted or stop() is called.
        """
        self._running = True
        self._ticks = self._load_ticks_from_parquet()

        prev_msc: int = 0

        for tick in self._ticks:
            if not self._running:
                break
            if self._max_ticks > 0 and self._ticks_emitted >= self._max_ticks:
                break

            # Realtime mode: sleep based on inter-tick interval
            if self._mode == 'realtime' and prev_msc > 0:
                current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
                if current_msc > prev_msc:
                    delta_s = (current_msc - prev_msc) / 1000.0
                    # Cap sleep to 5s to avoid extremely long waits on data gaps
                    time.sleep(min(delta_s, 5.0))

            # Throttle for visual debugging (replay mode only)
            if self._tick_delay_s > 0 and self._mode == 'replay':
                time.sleep(self._tick_delay_s)

            self._tick_queue.put(tick)
            self._ticks_emitted += 1

            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            if current_msc > 0:
                prev_msc = current_msc

        self._exhausted = True
        # Sentinel: signal the consumer that no more ticks will come
        self._tick_queue.put(None)

    def stop(self) -> None:
        """Signal the tick source to stop. Thread-safe."""
        self._running = False

    def get_symbol(self) -> str:
        """Return the symbol this tick source produces."""
        return self._symbol

    def is_exhausted(self) -> bool:
        """Check if all parquet ticks have been emitted."""
        return self._exhausted

    def get_tick_count(self) -> int:
        """
        Return total number of ticks loaded from parquet.

        Returns:
            Total tick count (available after start() begins)
        """
        return len(self._ticks)

    def get_ticks_emitted(self) -> int:
        """
        Return number of ticks emitted so far.

        Returns:
            Ticks pushed to queue
        """
        return self._ticks_emitted

    def _load_ticks_from_parquet(self) -> List[TickData]:
        """
        Load tick data from parquet file and convert to TickData objects.

        Returns:
            List of TickData objects sorted by time_msc
        """
        path = Path(self._parquet_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Parquet tick data not found: {self._parquet_path}"
            )

        df = pd.read_parquet(path)

        # Ensure time_msc column exists
        if 'time_msc' not in df.columns:
            raise ValueError(
                f"Parquet file missing 'time_msc' column: {self._parquet_path}"
            )

        # Sort by time_msc for correct replay order
        df = df.sort_values('time_msc').reset_index(drop=True)

        ticks = []
        for _, row in df.iterrows():
            time_msc = int(row['time_msc'])
            ts = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)

            tick = TickData(
                timestamp=ts,
                symbol=self._symbol,
                bid=float(row['bid']),
                ask=float(row['ask']),
                volume=float(row.get('volume', 0.0)),
                time_msc=time_msc,
                collected_msc=int(row.get('collected_msc', 0)),
            )
            ticks.append(tick)

        return ticks
