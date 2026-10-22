"""
A run's latest state frames as the API serves them (#611): each unit's latest frame with its age,
the verdict on whether the session still records, its first frame's memory, and — once the session
ended — how it ended, in the classification the warnings-errors section already serves. The run
list carries the same verdict on each row, from the same function.

The age is the one value measured across two processes: the session writes the frame, the API
server reads it, and the wall clock is the one clock both read that also survives a restart of the
machine. A negative age — a clock stepped back — is served as unknown rather than as a number.
"""

from datetime import datetime
from typing import Optional

from python.framework.types.api.recording_state_types import RecordingState
from python.framework.types.api.report_types import RunInfo, WarningsErrorsOutcome
from python.framework.types.api.state_frame_types import (
    FirstFrameRow,
    LatestStateFramesResponse,
    LatestStateFrameUnit,
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
    age_s = _age_of(last.written_at, served_at)
    recording = recording_of(ends, run, thresholds, served_at)
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


def recording_of(
    ends: Optional[StateFrameStreamEnds],
    run: RunInfo,
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> Optional[RecordingState]:
    """
    The verdict on a run's recording — the one the latest-frame route gives, and the one the run
    list carries on the run's row.

    Args:
        ends: The run's stream at its two ends, or None when it records none
        run: The run's row in the run list — where its ticks come from
        thresholds: How many cadences a frame may age
        served_at: The moment of the answer, on the wall clock

    Returns:
        The verdict, or None when the run has no frame yet or is not judged
    """
    if ends is None or ends.last is None:
        return None
    return judge_recording(ends.last.written_for, _age_of(ends.last.written_at, served_at),
                           ends.cadence_s, run.ticks_from, thresholds)


def _age_of(written_at: datetime, served_at: datetime) -> Optional[float]:
    """
    How many seconds ago a frame was written.

    Args:
        written_at: When the frame was written, on the wall clock
        served_at: The moment of the answer, on the wall clock

    Returns:
        The age in seconds, or None for a frame from the clock's future — a clock stepped back
    """
    age = (served_at - written_at).total_seconds()
    return age if age >= 0 else None
