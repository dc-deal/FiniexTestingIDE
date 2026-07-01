"""
FiniexTestingIDE - Signal Data Provider
Resolves the point-in-time signal snapshot for a tick (SIGNAL worker input, #141).
"""

from bisect import bisect_right
from datetime import datetime
from typing import List, Optional

from python.framework.types.signal_data_types import (
    ResolvedSignal, SentimentResult, SignalSeries, SignalSnapshot)


class SignalDataProvider:
    """
    Resolves the point-in-time signal snapshot for a (tick timestamp, symbol).

    Backtest-deterministic: the lookup key is the collected_msc receive stamp
    (the no-look-ahead anchor — resolve the most recent snapshot with
    collected_msc <= tick). No live calls. No artificial latency — collected_msc
    already encodes when the signal became available (the live-API refresh path
    rides #375). The thin per-run wrapper over the prepared, reusable SignalSeries.
    """

    def __init__(self, series: SignalSeries):
        """
        Initialize the provider from a prepared signal series.

        Args:
            series: Time-ordered snapshot collection for one source
        """
        self._source = series.source
        snapshots = sorted(series.snapshots, key=lambda s: s.collected_msc)
        self._snapshots: List[SignalSnapshot] = snapshots
        self._msc_keys: List[datetime] = [s.collected_msc for s in snapshots]

    def get_source(self) -> str:
        """Signal source label this provider serves."""
        return self._source

    def nearest(self, timestamp: datetime, symbol: str) -> Optional[ResolvedSignal]:
        """
        Resolve the most recent snapshot with collected_msc <= timestamp.

        Args:
            timestamp: Tick timestamp (UTC, tz-aware)
            symbol: Symbol to extract from the snapshot

        Returns:
            ResolvedSignal for the symbol, or None if no snapshot was collected
            at or before the timestamp (a gap → the worker returns an empty result)
        """
        idx = bisect_right(self._msc_keys, timestamp) - 1
        if idx < 0:
            return None
        snapshot = self._snapshots[idx]
        return ResolvedSignal(
            collected_msc=snapshot.collected_msc,
            result=self._extract_symbol(snapshot, symbol),
        )

    def _extract_symbol(self, snapshot: SignalSnapshot, symbol: str) -> SentimentResult:
        """
        Pick the per-symbol result from a snapshot.

        Args:
            snapshot: The resolved snapshot
            symbol: Symbol to extract

        Returns:
            The symbol's SentimentResult (a neutral HOLD if absent — defensive;
            the producer guarantees every requested symbol is present)
        """
        for result in snapshot.result:
            if result.symbol == symbol:
                return result
        return SentimentResult(
            symbol=symbol, signal='HOLD',
            reasoning='Symbol not present in snapshot',
        )
