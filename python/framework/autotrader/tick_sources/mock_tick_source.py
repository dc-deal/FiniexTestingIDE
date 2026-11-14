"""
FiniexTestingIDE - Mock Tick Source
Replays a pre-loaded tick list (from the prepared scenario data package, #438) for AutoTrader
testing without live data.
"""

import queue
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.types.market_types.market_data_types import TickData


class MockTickSource(AbstractTickSource):
    """
    Mock tick source that replays a pre-loaded tick list.

    The ticks come from the shared scenario data package (#438) — index-resolved for the profile's
    scenario_settings window through the same preparation stack the backtesting batch uses. Emits
    as fast as possible (functional testing); optional per-tick delay for visual debugging.

    Runs in a separate thread. Pushes TickData objects to a queue.Queue that the main algo thread
    consumes (Threading model 8.a).

    Args:
        ticks: Pre-loaded, time-ordered ticks for the session (from the data package)
        symbol: Trading symbol (e.g., 'BTCUSD')
        tick_queue: Thread-safe queue for tick delivery to main thread
        tick_delay_ms: Artificial per-tick delay in ms (0 = full speed)
        freeze_after_ticks: Outage drill (#436): pause emission once after N ticks. 0 = off
        freeze_duration_s: Outage drill (#436): pause duration in wall seconds
    """

    def __init__(
        self,
        ticks: List[TickData],
        symbol: str,
        tick_queue: queue.Queue,
        tick_delay_ms: int = 0,
        freeze_after_ticks: int = 0,
        freeze_duration_s: float = 0.0,
    ):
        self._ticks = ticks
        self._symbol = symbol
        self._tick_queue = tick_queue
        self._tick_delay_s = tick_delay_ms / 1000.0 if tick_delay_ms > 0 else 0.0
        self._freeze_after_ticks = freeze_after_ticks  # 0 = off
        self._freeze_duration_s = freeze_duration_s
        self._running = False
        self._exhausted = False
        self._ticks_emitted: int = 0
        self._freezing = False  # outage drill in progress (#451 episode origin)
        # The last tick put on the queue and the monotonic instant it was put there (#616): one
        # tuple, so the loop's thread reads a consistent pair
        self._last_emitted: Optional[Tuple[datetime, float]] = None

    def start(self) -> None:
        """
        Push the pre-loaded ticks to the queue.

        Called from the tick source thread. Blocks until all ticks are emitted
        or stop() is called.
        """
        self._running = True

        for tick in self._ticks:
            if not self._running:
                break

            # Outage drill (#436): one deliberate mid-replay silence. The loop's
            # heartbeats keep running against the wall clock, so the session-level
            # staleness contract fires organically, then recovers on resume.
            if (
                self._freeze_after_ticks > 0
                and self._ticks_emitted == self._freeze_after_ticks
            ):
                # Declared while silent (#451) so the episode is recorded as injected
                # rather than as a real feed outage.
                self._freezing = True
                time.sleep(self._freeze_duration_s)
                self._freezing = False

            # Throttle for visual debugging
            if self._tick_delay_s > 0:
                time.sleep(self._tick_delay_s)

            self._tick_queue.put(tick)
            self._last_emitted = (tick.timestamp, time.monotonic())
            self._ticks_emitted += 1

        self._exhausted = True
        # Sentinel: signal the consumer that no more ticks will come
        self._tick_queue.put(None)

    def stop(self) -> None:
        """Signal the tick source to stop. Thread-safe."""
        self._running = False

    def get_symbol(self) -> str:
        """Return the symbol this tick source produces."""
        return self._symbol

    def get_injected_outage_label(self) -> str:
        """
        Declare the outage drill while it is running (#451).

        Returns:
            'freeze drill' during the deliberate silence, '' otherwise
        """
        return 'freeze drill' if self._freezing else ''

    def get_idle_pass_time(self) -> datetime:
        """
        The replay's own time for a pass that carries no tick (#616).

        The last tick put on the queue plus the real time since, so a pause in the replay — the
        outage drill, a throttled replay — moves the clock by the pause and never to today. Before
        the first tick it is the first tick's stamp. A pause longer than the replay's own gap to
        its next tick leaves the clock ahead of that tick; the executor's clock only moves
        forward, so it then holds until the replay has caught up. An empty replay has no time of
        its own and keeps the base's answer.

        Returns:
            Timezone-aware UTC datetime
        """
        last_emitted = self._last_emitted
        if last_emitted is not None:
            stamp, emitted_at = last_emitted
            return stamp + timedelta(seconds=time.monotonic() - emitted_at)
        if self._ticks:
            return self._ticks[0].timestamp
        return super().get_idle_pass_time()

    def is_exhausted(self) -> bool:
        """Check if all ticks have been emitted."""
        return self._exhausted

    def get_ticks_emitted(self) -> int:
        """
        Return number of ticks emitted so far.

        Returns:
            Ticks pushed to queue
        """
        return self._ticks_emitted
