"""
FiniexTestingIDE - Abstract Tick Source
Interface for all tick data sources (mock, WebSocket, REST).
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from python.framework.types.market_types.market_data_types import TickData


class AbstractTickSource(ABC):
    """
    Abstract interface for tick data sources.

    Tick sources run in a separate thread and push ticks
    via queue.Queue to the main algo thread (Threading model 8.a).

    Implementations:
    - MockTickSource: Parquet replay (replay / realtime mode)
    - KrakenTickSource: WebSocket v2 (future, #232)
    """

    @abstractmethod
    def start(self) -> None:
        """
        Start producing ticks.

        Called from the tick source thread. Implementations should
        push ticks to the queue until stopped or exhausted.
        """

    @abstractmethod
    def stop(self) -> None:
        """
        Signal the tick source to stop producing ticks.

        Must be thread-safe — called from the main thread
        while start() runs in the tick source thread.
        """

    @abstractmethod
    def get_symbol(self) -> str:
        """
        Return the symbol this tick source produces.

        Returns:
            Trading symbol (e.g., 'BTCUSD')
        """

    @abstractmethod
    def is_exhausted(self) -> bool:
        """
        Check if the tick source has no more ticks to produce.

        Returns:
            True if all ticks have been emitted (mock: end of parquet data)
        """

    # === Display Stats (GIL-safe reads for display thread) ===

    def get_last_message_time(self) -> Optional[datetime]:
        """
        Last message time from data source (includes heartbeats).

        Override in subclasses with connection stats (e.g., WebSocket).
        GIL-safe: display thread reads this directly.

        Returns:
            Last message datetime (UTC) or None if not available
        """
        return None

    def get_last_tick_time(self) -> Optional[datetime]:
        """
        Last actual trade tick time (excludes heartbeats/WS-only messages).

        Override in subclasses that distinguish trade ticks from other messages.
        GIL-safe: display thread reads this directly.

        Returns:
            Last trade tick datetime (UTC) or None if no ticks received yet
        """
        return None

    def get_reconnect_count(self) -> int:
        """
        Number of reconnection attempts since session start.

        Override in subclasses with connection recovery logic.

        Returns:
            Reconnect count (0 for sources without reconnect)
        """
        return 0

    def get_ticks_emitted(self) -> int:
        """
        Total ticks pushed to the queue since session start.

        Override in subclasses that track emission count.

        Returns:
            Total ticks emitted (0 if not tracked)
        """
        return 0
