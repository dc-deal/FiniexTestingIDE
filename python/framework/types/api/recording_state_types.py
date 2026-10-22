"""
FiniexTestingIDE - Recording State Types

Whether a session is still recording its state frames (#611). A unit of its own because two served
types carry it — a unit's row on the latest-state-frame route and a run's row on the run list — and
the state-frame types already read the report types.
"""

from enum import StrEnum


class RecordingState(StrEnum):
    """
    Whether a session is still recording its state, as the latest-frame route judges it (#611).

    Judged when an answer is made and never written into a frame, so the frames of a mock session
    and of a live one stay alike.
    """
    CURRENT = 'current'    # the latest frame is younger than the configured number of cadences
    STOPPED = 'stopped'    # older than that and not the last one: stopped without an end
    ENDED = 'ended'        # the latest frame is the one written at the session's end
