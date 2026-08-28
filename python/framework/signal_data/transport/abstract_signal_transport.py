"""
FiniexTestingIDE - Abstract Signal Transport
The contract a live signal transport fulfils toward the session (#141 Part 2a, #468).

Deliberately three methods wide. Everything a transport does that matters to the rest of
the system happens through the SignalInbox, which it fills from its own thread and which
the loop drains once per pass — so the session needs to start it, stop it, and be able to
render what it is doing. It never needs to know HOW envelopes arrive.

That is the whole reason the push stream could replace the interim pull path without
touching either: the tick loop and the session had been typed against the concrete poll
source, which made a second transport a change to both. They are typed against this
instead, so the stream arrived and the pull path was deleted (2026-08-28) with neither
noticing. One implementation today; the ABC is what kept the swap cheap.
"""

from abc import ABC, abstractmethod

from python.framework.types.autotrader_types.autotrader_display_types import (
    SignalTransportStats,
)


class AbstractSignalTransport(ABC):
    """
    A live source of signal envelopes, feeding the inbox from its own thread.

    Implementations own their connection, their failure handling and their operator tape.
    What they must NOT own is any decision about the series: ordering, deduplication and
    the resolution gate belong to the provider, which is what lets a SIGNAL worker read a
    live series and a mounted archive without being able to tell them apart.
    """

    @abstractmethod
    def start(self) -> None:
        """Begin receiving on a background thread."""

    @abstractmethod
    def stop(self) -> None:
        """Stop receiving and wait for the thread to finish."""

    @abstractmethod
    def get_transport_stats(self) -> SignalTransportStats:
        """
        Snapshot of the transport for the operator panel.

        Returns:
            The current transport view — read from the loop thread while the transport
            thread writes, so an implementation returns a consistent copy rather than
            live-updating state
        """
