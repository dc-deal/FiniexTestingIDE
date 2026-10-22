"""
A run's latest state frames as the API serves them (#611): each unit's latest frame with its age,
the verdict on whether the session still records, its first frame's memory, and — once the session
ended — how it ended, in the classification the warnings-errors section already serves.

The age is the one value measured across two processes: the session writes the frame, the API
server reads it, and the wall clock is the one clock both read that also survives a restart of the
machine. A negative age — a clock stepped back — is served as unknown rather than as a number.
"""

from datetime import datetime
from typing import Optional

from python.framework.types.api.report_types import RunInfo, WarningsErrorsOutcome
from python.framework.types.api.state_frame_types import (
    FirstFrameRow,
    LatestStateFramesResponse,
    LatestStateFrameUnit,
    RecordingState,
    StateFrameStreamEnds,
)
from python.framework.types.config_types.state_frames_api_config_types import StateFramesApiConfig
from python.framework.validators.recording_state_validator import judge_recording


def describe_latest_state_frames(
    ends: StateFrameStreamEnds,
    run: RunInfo,
    outcome: Optional[WarningsErrorsOutcome],
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> LatestStateFramesResponse:
    """
    Serve a unit's latest frame with its age and the verdict on its recording.

    Args:
        ends: The unit's stream at its two ends, its latest frame present
        run: The run's row in the run list — where its ticks come from
        outcome: The run's end classification once its report phase wrote it, else None
        thresholds: How many cadences a frame may age
        served_at: The moment of the answer, on the wall clock

    Returns:
        The latest frames as served, one row per unit
    """
    last = ends.last
    age = (served_at - last.written_at).total_seconds()
    age_s = age if age >= 0 else None
    recording = judge_recording(last.written_for, age_s, ends.cadence_s, run.ticks_from, thresholds)
    first = ends.first
    unit = LatestStateFrameUnit(
        unit_name=ends.unit_name,
        cadence_timeframe=ends.cadence_timeframe,
        cadence_s=ends.cadence_s,
        written_at=last.written_at,
        age_s=age_s,
        recording=recording,
        truncated_tail=ends.truncated_tail,
        first_frame=(FirstFrameRow(written_at=first.written_at, process_memory=first.process_memory)
                     if first is not None else None),
        outcome=outcome if recording == RecordingState.ENDED else None,
        frame=last,
    )
    return LatestStateFramesResponse(served_at=served_at, units=[unit])
