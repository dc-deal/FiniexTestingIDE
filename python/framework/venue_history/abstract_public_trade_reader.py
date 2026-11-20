"""
The contract of a venue's public trade list — what a venue published, without an account.

It belongs to a venue's public data half: no credentials, no account. A venue that publishes no
trade list (a forex broker quotes prices and publishes no trades) has no reader, and the seam that
hands readers out answers none for it.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List

from python.framework.types.venue_history_types import VenueTrade


class AbstractPublicTradeReader(ABC):
    """Reads the trades a venue published for a symbol."""

    @abstractmethod
    def read_trades(self, symbol: str, start: datetime, end: datetime) -> List[VenueTrade]:
        """
        Every trade the venue published for a symbol in a stretch of time.

        Args:
            symbol: Our symbol ('ETHUSD')
            start: The stretch's start, included
            end: Its end, excluded

        Returns:
            The trades, each once, in the venue's id order. A failed read raises — never an empty
            list in its place
        """
