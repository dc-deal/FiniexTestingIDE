"""
The contract of an account's ledger at its venue — every movement of a balance, as the venue
booked it.

It belongs to a venue's account side and reads only: its credentials may query the ledger and
the closed orders, never trade. A venue whose ledger cannot be read has no reader, and the seam
that hands readers out answers none for it.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List

from python.framework.types.venue_history_types import VenueLedgerEntry


class AbstractVenueLedgerReader(ABC):
    """Reads the entries of an account's ledger."""

    @abstractmethod
    def read_ledger(self, start: datetime, end: datetime) -> List[VenueLedgerEntry]:
        """
        Every entry of the account's ledger in a stretch of time.

        Args:
            start: The stretch's start, included
            end: Its end, excluded

        Returns:
            The entries in booking order, a trade's leg with the order it belongs to. A failed read
            raises — never an empty list in its place
        """
