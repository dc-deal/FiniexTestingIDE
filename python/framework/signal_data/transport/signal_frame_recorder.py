"""
FiniexTestingIDE - Signal Frame Recorder
Keeps the RAW frames a stream delivered, beside their parsed form (#468, #466).

Exists for one reason: a certificate proves things about the WIRE that a parsed object can
no longer answer. Parsing is lossy in exactly the three directions the contract cares about
— a field's ABSENCE (`collected_msc` is never on the wire and always on the model), a
field's wire TYPE (`is_breaking: 1` becomes `True` before anyone can object), and a field's
LOCATION (an older line's `metadata.trigger_reason` is normalized to the top level on read).

Optional and off by default. The transport carries it the way it already carries the
observed-series accumulator: a `None` collaborator that a certificate run supplies and a
trading session never does. That is what keeps a proof-of-contract concern out of the hot
path while still letting the proof rest on frames the REAL transport delivered — rather
than on a second SSE client written to watch the first, which would be one more derivation
of a contract this project has already derived twice too often.
"""

from collections import deque
from datetime import datetime
from typing import Deque, List

from python.framework.types.signal_certificate_types import FeedObservation
from python.framework.types.signal_data_types import SignalSnapshot

# How many frames one recording holds. A certificate run reads a handful; the bound is
# there so a recorder left attached to a long session cannot grow without limit.
DEFAULT_CAPACITY = 64


class SignalFrameRecorder:
    """
    Records raw frames with their parsed form, bounded and newest-last.

    Deliberately dumb: it stores and hands back. Every judgement about what a frame proves
    belongs to the validator, and every decision about what reaches a worker belongs to the
    provider — a recorder that filtered would be deciding which evidence the certificate is
    allowed to see.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        """
        Initialize an empty recording.

        Args:
            capacity: How many frames to keep; the oldest are dropped beyond it
        """
        self._observations: Deque[FeedObservation] = deque(maxlen=capacity)
        self._recorded = 0

    def record(self, envelope: dict, snapshot: SignalSnapshot,
               received: datetime, frame_bytes: int) -> None:
        """
        Keep one frame, raw and parsed.

        Args:
            envelope: The decoded payload exactly as it arrived, before the model
            snapshot: The same payload through the production reader
            received: When we read it — our observation (ts_init), never the event's own
                time (§9)
            frame_bytes: Encoded size of the payload as it came off the wire
        """
        self._observations.append(FeedObservation(
            envelope=envelope,
            snapshot=snapshot,
            fetched_at=received,
            frame_bytes=frame_bytes))
        self._recorded += 1

    def get_observations(self) -> List[FeedObservation]:
        """
        The frames kept, oldest first.

        Returns:
            A copy, so a reader cannot mutate the recording it is judging
        """
        return list(self._observations)

    def get_recorded_count(self) -> int:
        """
        How many frames were recorded, including any the bound has since dropped.

        The stored list alone cannot say this, and the difference matters to a certificate:
        a recording at its capacity is a TAIL, and an artifact that presented a tail as the
        whole would be describing less than it claims.

        Returns:
            The total offered to this recorder
        """
        return self._recorded
