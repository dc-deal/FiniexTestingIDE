"""
FiniexTestingIDE - Signal Inbox
Thread-safe hand-off from a live signal transport to the tick loop (#141 Part 2a).

Transport, not core: this exists only because a thread boundary exists. A live stream
receives envelopes on its own thread and deposits them here; the loop drains the inbox at
the top of each pass and merges what it finds into the SignalDataProvider. In simulation
and in an AutoTrader mock session nothing ever fills it, so the drain is always empty and
both pipelines behave exactly as before.
"""

from threading import Lock
from typing import Dict, List

from python.framework.types.signal_data_types import SignalSnapshot


class SignalInbox:
    """
    Bounded-lifetime buffer between a signal transport thread and the loop thread.

    Deliberately dumb: it holds snapshots and hands them over. Ordering, deduplication
    and the resolution gate are the provider's job, so an arrival needs no interpretation
    here — which is what keeps the transport free of decisions about the series.
    """

    def __init__(self):
        """Initialize an empty inbox."""
        self._lock = Lock()
        self._pending: Dict[str, List[SignalSnapshot]] = {}
        self._total_received = 0

    def put(self, signal_kind: str, snapshots: List[SignalSnapshot]) -> None:
        """
        Deposit newly received snapshots for one signal source.

        Called from the transport thread.

        Args:
            signal_kind: Source the snapshots belong to
            snapshots: Received snapshots, in any order
        """
        if not snapshots:
            return
        with self._lock:
            self._pending.setdefault(signal_kind, []).extend(snapshots)
            self._total_received += len(snapshots)

    def drain(self) -> Dict[str, List[SignalSnapshot]]:
        """
        Take everything pending, leaving the inbox empty.

        Called from the loop thread, once per pass.

        Returns:
            Snapshots per signal source; empty when nothing arrived
        """
        with self._lock:
            drained = self._pending
            self._pending = {}
        return drained

    def get_pending_count(self) -> int:
        """Number of snapshots waiting to be drained."""
        with self._lock:
            return sum(len(batch) for batch in self._pending.values())

    def get_total_received(self) -> int:
        """Snapshots deposited over the session's lifetime (diagnostics)."""
        with self._lock:
            return self._total_received
