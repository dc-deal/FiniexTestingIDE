"""
FiniexTestingIDE - Recording State Validator (#611)

Whether a session is still recording its state, judged from its latest frame. A verdict, which is
why it lives here rather than beside the route that serves it: the route reports the frame and its
age, this decides what they mean.

The age counts only where the session's canonical clock keeps pace with the wall clock — a session
reading the venue, with real money or as a dry run. A replay's frames come at replay speed, as fast
as it can or at a fixed pause per tick, so their age says nothing about whether it still runs: it is
judged only once it has ended.
"""

from typing import Optional

from python.framework.types.api.recording_state_types import RecordingState
from python.framework.types.api.state_frame_types import StateFrameCause
from python.framework.types.config_types.state_frames_api_config_types import StateFramesApiConfig
from python.framework.types.run_kind_types import TicksFrom


def judge_recording(
    written_for: StateFrameCause,
    age_s: Optional[float],
    cadence_s: float,
    ticks_from: Optional[TicksFrom],
    thresholds: StateFramesApiConfig,
) -> Optional[RecordingState]:
    """
    Judge whether the session behind a latest frame still records.

    Args:
        written_for: Why the latest frame was written — the one written at the end ends the record
        age_s: How long ago it was written, None where that is unknown
        cadence_s: The stream's cadence in seconds
        ticks_from: Where the session's ticks come from; only a session reading the venue is
            judged by age
        thresholds: How many cadences a frame may age

    Returns:
        The verdict, or None where none can be made
    """
    if written_for == StateFrameCause.SHUTDOWN:
        return RecordingState.ENDED
    if ticks_from is not TicksFrom.VENUE or age_s is None:
        return None
    if age_s < thresholds.stopped_after_cadences * cadence_s:
        return RecordingState.CURRENT
    return RecordingState.STOPPED
