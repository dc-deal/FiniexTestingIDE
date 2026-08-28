"""
FiniexTestingIDE - Signal Data Provider
Resolves the point-in-time signal snapshot for a tick (SIGNAL worker input, #141).
"""

from bisect import bisect_right
from datetime import datetime
from typing import List, Optional, Set, Tuple

from python.framework.types.signal_data_types import (
    ResolvedSignal,
    SentimentResult,
    SignalSeries,
    SignalSnapshot,
)


class SignalDataProvider:
    """
    Resolves the point-in-time signal snapshot for a (tick timestamp, symbol).

    Backtest-deterministic: the lookup gate is the snapshot's resolution key — its
    availability instant (available_msc, else collected_msc for the pre-stream era) —
    so a decision only ever sees a snapshot at or after the moment it could really
    have had it. Series ORDER comes from (stream_epoch, seq), never from a clock.

    Appendable: a live stream extends the same series in place (#141 Part 2a), so the
    worker keeps its one collaborator and never learns where a snapshot came from.
    """

    def __init__(self, series: SignalSeries):
        """
        Initialize the provider from a prepared signal series.

        Args:
            series: Snapshot collection for one source
        """
        self._signal_kind = series.signal_kind
        self._snapshots: List[SignalSnapshot] = []
        self._gate_keys: List[datetime] = []
        self._seen: Set[Tuple[int, int]] = set()
        self._availability_clamps = 0
        self._max_clamp_correction_ms = 0.0
        self._reindex(series.snapshots)

    def get_signal_kind(self) -> str:
        """Payload kind this provider serves (e.g. 'llm_sentiment')."""
        return self._signal_kind

    def get_snapshot_count(self) -> int:
        """Number of snapshots currently held."""
        return len(self._snapshots)

    def extend(self, snapshots: List[SignalSnapshot]) -> int:
        """
        Merge newly arrived snapshots into the series (live stream, #141 Part 2a).

        Idempotent: the producer is at-least-once, so a redelivered envelope is dropped
        rather than duplicated. Identity is (stream_epoch, seq) where the stream stamps
        it — seq is unique only WITHIN an epoch, so the pair is the key — and the
        resolution key otherwise.

        Args:
            snapshots: Newly received snapshots, in any order

        Returns:
            How many were actually new
        """
        fresh = [s for s in snapshots if self._identity(s) not in self._seen]
        if not fresh:
            return 0
        self._reindex(self._snapshots + fresh)
        return len(fresh)

    def _identity(self, snapshot: SignalSnapshot) -> Tuple[int, int]:
        """
        Dedupe identity of a snapshot.

        Args:
            snapshot: The snapshot

        Returns:
            (stream_epoch, seq) where the stream stamps them, else a resolution-key pair
        """
        if snapshot.seq is None or snapshot.stream_epoch is None:
            return (-1, int(snapshot.get_resolution_key().timestamp() * 1000))
        return (snapshot.stream_epoch, snapshot.seq)

    def _reindex(self, snapshots: List[SignalSnapshot]) -> None:
        """
        Sort the series and rebuild the lookup index.

        Order is (stream_epoch, seq); the bisect index runs over resolution keys and is
        clamped to be non-decreasing. The clamp matters when a producer-side clock
        correction moves an availability stamp backwards (their RC-8): a snapshot can then
        never become visible EARLIER than the one preceding it in the series, which is the
        only direction that would be look-ahead.

        Args:
            snapshots: All snapshots to index
        """
        ordered = sorted(snapshots, key=lambda s: s.get_order_key())
        gate_keys: List[datetime] = []
        clamps = 0
        worst_ms = 0.0
        for snapshot in ordered:
            key = snapshot.get_resolution_key()
            if gate_keys and key < gate_keys[-1]:
                # Counted, not just corrected. Silently holding the line meant we could
                # never afterwards say whether a producer clock had ever stepped backwards
                # at all — the correction was invisible in exactly the runs where knowing
                # it would matter. Recomputed rather than accumulated, because a re-index
                # walks the whole series again: this is a property of the series as it
                # stands, not a running tally of how often it was rebuilt.
                clamps += 1
                worst_ms = max(worst_ms, (gate_keys[-1] - key).total_seconds() * 1000.0)
                key = gate_keys[-1]
            gate_keys.append(key)
        self._snapshots = ordered
        self._gate_keys = gate_keys
        self._availability_clamps = clamps
        self._max_clamp_correction_ms = worst_ms
        self._seen = {self._identity(s) for s in ordered}

    def get_availability_clamps(self) -> int:
        """
        How many snapshots in this series had their visibility instant held back.

        A producer-side clock correction can move an availability stamp BACKWARDS, and the
        resolution gate refuses to let that make a snapshot visible earlier than the one
        before it — the only direction that would be look-ahead. This is how often that
        refusal was needed.

        Derived here rather than read from the producer's own counter on purpose: the same
        number then comes out of simulation and live over the same archive, it needs no
        parquet column, and it does not depend on the producer continuing to send it.

        Returns:
            The number of clamped snapshots in the current series
        """
        return self._availability_clamps

    def get_max_clamp_correction_ms(self) -> float:
        """
        The largest backwards step the gate absorbed, in milliseconds.

        The count alone cannot be read: one clamp of two milliseconds is jitter, one of
        four hours is a story. This is what separates them.

        Returns:
            The largest correction, or 0.0 when nothing was clamped
        """
        return self._max_clamp_correction_ms

    def nearest(self, timestamp: datetime, symbol: str) -> Optional[ResolvedSignal]:
        """
        Resolve the most recent snapshot available at or before a moment.

        Args:
            timestamp: Moment to resolve at (UTC, tz-aware) — the canonical clock
            symbol: Symbol to extract from the snapshot

        Returns:
            ResolvedSignal for the symbol, or None if nothing was available at or
            before the moment (a gap → the worker returns an empty result)
        """
        idx = bisect_right(self._gate_keys, timestamp) - 1
        if idx < 0:
            return None
        snapshot = self._snapshots[idx]
        return ResolvedSignal(
            collected_msc=snapshot.collected_msc,
            result=self._extract_symbol(snapshot, symbol),
            evidence_as_of=snapshot.get_evidence_as_of(),
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
