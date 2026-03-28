"""
FiniexTestingIDE - Abstract Tick Source
Interface for all tick data sources (mock, WebSocket, REST).
"""

from abc import ABC, abstractmethod
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
