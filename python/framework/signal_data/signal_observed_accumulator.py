"""
FiniexTestingIDE - Signal Observed Accumulator
Builds the observed plane of a live signal feed as envelopes arrive (#433 live half).

A simulation reads its signal facts out of a finished archive; a live session has no
archive to read, so the same facts have to be accumulated while they pass through. What
comes out is the identical dataclass, which is what lets one report shape serve both
pipelines instead of the live path growing a second one.

Fed by the transport rather than by the provider: when the SSE stream (#468) replaces the
interim poller it feeds this same accumulator and nothing else moves, and the provider
stays a pure resolver — which is what makes a SIGNAL worker indifferent to where its
snapshots came from.
"""

import threading
from typing import Optional

from python.framework.types.signal_data_types import (
    SignalObservedSeries, SignalSeriesKind, SignalSnapshot)


class SignalObservedAccumulator:
    """
    Accumulates what a live feed's envelopes state about themselves.

    Deliberately holds no snapshots — only their composition. The series itself lives in
    the SignalDataProvider; duplicating it here would put a second, silently diverging
    copy behind a reporting concern.
    """

    def __init__(self, source: str, symbol: str = ''):
        """
        Initialize the accumulator.

        Args:
            source: Signal source identity (the producer's pipeline_id)
            symbol: Trading symbol the session consumes, '' for an envelope-level view
        """
        self._source = source
        self._symbol = symbol
        self._lock = threading.Lock()

        self._count = 0
        self._first = None
        self._last = None
        self._origins = set()
        self._fingerprints = set()
        self._triggers = {}
        self._trigger_unknown = 0
        self._with_identity = 0
        self._seq_first: Optional[int] = None
        self._seq_last: Optional[int] = None
        self._seq_holes = 0
        self._epochs = set()
        # The producer's own reported interval. Preferred over a measured median because a
        # session that received three envelopes has no sample to measure — and the producer
        # states the number authoritatively on its health endpoint.
        self._cadence_seconds = 0.0

    def observe(self, snapshot: SignalSnapshot) -> None:
        """
        Record what one arriving envelope says about itself.

        Called from the transport thread as envelopes are enqueued, hence the lock: the
        report is built from the loop thread at shutdown.

        Args:
            snapshot: The envelope that just arrived
        """
        with self._lock:
            self._count += 1
            stamp = snapshot.get_resolution_key()
            if self._first is None or stamp < self._first:
                self._first = stamp
            if self._last is None or stamp > self._last:
                self._last = stamp

            if snapshot.data_origin:
                self._origins.add(snapshot.data_origin)
            if snapshot.config_fingerprint:
                self._fingerprints.add(snapshot.config_fingerprint)

            trigger = snapshot.trigger_reason
            if trigger:
                self._triggers[trigger] = self._triggers.get(trigger, 0) + 1
            else:
                self._trigger_unknown += 1

            self._observe_position(snapshot)

    def set_cadence_seconds(self, cadence_seconds: float) -> None:
        """
        Record the producer's own reported evaluation interval for this source.

        Args:
            cadence_seconds: Interval in seconds, as the producer reports it
        """
        with self._lock:
            self._cadence_seconds = cadence_seconds

    def get_observed_series(self) -> SignalObservedSeries:
        """
        The feed's observed plane, in the shape an archive also produces.

        Returns:
            The accumulated series, marked as feed-backed
        """
        with self._lock:
            span = (
                (self._seq_first, self._seq_last)
                if self._seq_first is not None and self._seq_last is not None
                else None
            )
            return SignalObservedSeries(
                source=self._source,
                symbol=self._symbol,
                kind=SignalSeriesKind.FEED,
                snapshot_count=self._count,
                start_time=self._first,
                end_time=self._last,
                cadence_seconds=self._cadence_seconds,
                data_origins=set(self._origins),
                config_fingerprints=set(self._fingerprints),
                trigger_reasons=dict(self._triggers),
                trigger_unknown=self._trigger_unknown,
                envelopes_with_stream_identity=self._with_identity,
                seq_span=span,
                seq_holes=self._seq_holes,
                stream_epochs=set(self._epochs),
            )

    def _observe_position(self, snapshot: SignalSnapshot) -> None:
        """
        Track the stream position, counting holes only where they mean something.

        A hole is missing positions WITHIN one epoch. Across an epoch boundary the numbers
        restart, so the distance between them is not a measurement of anything — counting
        it would report a producer restart as lost data.

        Args:
            snapshot: The envelope that just arrived
        """
        seq = snapshot.seq
        epoch = snapshot.stream_epoch
        if seq is None or epoch is None:
            return

        self._with_identity += 1
        same_epoch = epoch in self._epochs
        self._epochs.add(epoch)

        if self._seq_first is None:
            self._seq_first = seq
        if self._seq_last is not None and same_epoch and seq > self._seq_last + 1:
            self._seq_holes += seq - self._seq_last - 1
        self._seq_last = seq
