"""
FiniexTestingIDE - Tick Stream Event Types
What the live tick source records about its own connection (#609): every outage with the public
trade ids around it, and every trade it refused. Transport health — no worker reads it, and the
tick a worker sees carries none of it.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class TickStreamLossCause(Enum):
    """How the tick source learned that its connection was lost."""
    # No message for `connection_dead_s` — the source's own check, which aborts the connection.
    SILENCE = 'silence'
    # The library's keepalive ping went unanswered: our side sent close code 1011 and the venue
    # sent nothing. Rare while the silence threshold is shorter than ping interval plus timeout.
    PING_TIMEOUT = 'ping_timeout'
    # The venue sent a close frame — a clean 1000 / 1001 included, which ends the receive loop
    # without an exception.
    CLOSED_BY_VENUE = 'closed_by_venue'
    # The connection dropped with no close frame from either side.
    CONNECTION_ERROR = 'connection_error'
    # Anything else ended the pass — most likely a defect on our side, never a venue answer.
    ERROR = 'error'


class RefusedPublicTradeReason(Enum):
    """Why the parser refused a trade the venue sent instead of turning it into a tick."""
    NO_VENUE_TIME = 'no_venue_time'
    UNREADABLE_VENUE_TIME = 'unreadable_venue_time'
    # Parsed, but naive: read as host-local time it would shift the event by the host's offset.
    VENUE_TIME_WITHOUT_OFFSET = 'venue_time_without_offset'
    # The entry or its list is not the shape the venue documents.
    UNREADABLE_ENTRY = 'unreadable_entry'
    INVALID_PRICE = 'invalid_price'
    INVALID_QUANTITY = 'invalid_quantity'

    def describe(self) -> str:
        """
        What was wrong with the trade, as the operator reads it in the session log.

        Returns:
            One clause naming the defect
        """
        return _REFUSAL_DESCRIPTIONS[self]


_REFUSAL_DESCRIPTIONS: Dict[RefusedPublicTradeReason, str] = {
    RefusedPublicTradeReason.NO_VENUE_TIME: 'it carries no venue time',
    RefusedPublicTradeReason.UNREADABLE_VENUE_TIME: 'its venue time cannot be read',
    RefusedPublicTradeReason.VENUE_TIME_WITHOUT_OFFSET: 'its venue time has no UTC offset',
    RefusedPublicTradeReason.UNREADABLE_ENTRY: 'the entry is not in the shape the venue documents',
    RefusedPublicTradeReason.INVALID_PRICE: 'its price is not a finite positive number',
    RefusedPublicTradeReason.INVALID_QUANTITY: 'its quantity is not a finite positive number',
}


@dataclass
class TickStreamOutage:
    """
    One outage of the tick stream: from the loss of a connection to the first trade after it.

    The first trade, not the next handshake, ends it — what the stream exists for is data, and a
    venue that accepts connections and drops them again before anything arrives is one outage
    with several attempts. The trade ids around it bound what the session never saw: the public
    trade ids between the two, exclusive — an upper bound, because the venue allocates some ids
    that never become public trades, so which of them were trades is read from the venue's record
    after the run (#512).

    Instants (`lost_at`, `restored_at`) are local wall-clock readings — when we noticed, never
    when the venue did anything. Durations come from the monotonic clock; the data gap from the
    two trades' venue times.
    """
    outage_no: int
    symbol: str
    lost_at: datetime
    detected_by: TickStreamLossCause
    # How long the lost connection had delivered: its subscription to its last message.
    delivered_for_s: float
    # Connection attempts after the loss, the one that delivered the first trade included.
    attempts: int = 0
    restored_at: Optional[datetime] = None
    outage_s: Optional[float] = None
    last_public_trade_id: Optional[int] = None
    last_public_trade_time: Optional[datetime] = None
    first_public_trade_id: Optional[int] = None
    first_public_trade_time: Optional[datetime] = None

    def get_data_gap_s(self) -> Optional[float]:
        """
        The span with no trade, measured between the two trades' venue times.

        Returns:
            Seconds between the last trade before the loss and the first after it, or None
            when either side is unknown
        """
        if self.last_public_trade_time is None or self.first_public_trade_time is None:
            return None
        return (self.first_public_trade_time - self.last_public_trade_time).total_seconds()

    def get_unseen_trade_ids(self) -> Optional[int]:
        """
        How many public trade ids lie between the two trades — an upper bound of the trades the
        session missed, because the venue allocates some ids that never become public trades.

        Returns:
            The count, or None when either id is unknown
        """
        if self.last_public_trade_id is None or self.first_public_trade_id is None:
            return None
        return max(self.first_public_trade_id - self.last_public_trade_id - 1, 0)

    def to_record(self) -> Dict[str, Any]:
        """
        The outage as one line of the tick stream record.

        Returns:
            JSON-ready values; None values are left out by the writer
        """
        return {
            'record_kind': 'outage',
            'outage_no': self.outage_no,
            'symbol': self.symbol,
            'lost_at': _instant(self.lost_at),
            'detected_by': self.detected_by.value,
            'delivered_for_s': round(self.delivered_for_s, 3),
            'attempts': self.attempts,
            'restored_at': _instant(self.restored_at),
            'outage_s': None if self.outage_s is None else round(self.outage_s, 3),
            'last_public_trade_id': self.last_public_trade_id,
            'last_public_trade_time': _instant(self.last_public_trade_time),
            'first_public_trade_id': self.first_public_trade_id,
            'first_public_trade_time': _instant(self.first_public_trade_time),
            'data_gap_s': (None if self.get_data_gap_s() is None
                           else round(self.get_data_gap_s(), 3)),
        }


@dataclass
class RefusedPublicTrade:
    """One trade the venue sent that the parser refused — counted, never turned into a tick."""
    symbol: str
    received_at: datetime
    reason: RefusedPublicTradeReason
    # Kept to what is needed to recognise the frame again, never the whole frame.
    raw: str
    public_trade_id: Optional[int] = None

    def to_record(self) -> Dict[str, Any]:
        """
        The refusal as one line of the tick stream record.

        Returns:
            JSON-ready values; None values are left out by the writer
        """
        return {
            'record_kind': 'refused_public_trade',
            'symbol': self.symbol,
            'received_at': _instant(self.received_at),
            'public_trade_id': self.public_trade_id,
            'reason': self.reason.value,
            'raw': self.raw,
        }


@dataclass
class RefusedPublicTradeTotals:
    """
    How many trades the parser refused in one session, per reason — the record's closing line,
    because the record lists only the first refusals of each reason and counts the rest.
    """
    symbol: str
    totals: Dict[RefusedPublicTradeReason, int]
    # How many refusals of one reason the record lists before it only counts them.
    listed_per_reason: int

    def to_record(self) -> Dict[str, Any]:
        """
        The totals as the record's closing line.

        Returns:
            JSON-ready values
        """
        return {
            'record_kind': 'refused_public_trade_totals',
            'symbol': self.symbol,
            'listed_per_reason': self.listed_per_reason,
            'totals': {reason.value: count for reason, count in self.totals.items()},
        }


def _instant(value: Optional[datetime]) -> Optional[str]:
    """
    An instant as ISO-8601 text.

    Args:
        value: A timezone-aware instant, or None

    Returns:
        Its ISO form, or None
    """
    return None if value is None else value.isoformat()
