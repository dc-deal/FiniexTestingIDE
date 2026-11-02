"""
FiniexTestingIDE - Recorded Stream Types

What a unit of a run recorded over time, as the API serves it (#580): its timeline — each stream
with its record count and the time of its first and latest record — and a window of its records, a
page at a time. Today a unit records its state frames; drawings and algo events (#379) and a
strategy's tables (#589) join as streams of their own — rows of the timeline, lists of the window —
without reshaping either.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from python.framework.types.api.recording_state_types import RecordingState
from python.framework.types.api.state_frame_types import StateFrame, StateFrameStreamEnds
from python.framework.types.run_stream_types import RecordedStreamKind, StreamDayIndexRow


class RecordedStreamExtent(BaseModel):
    """
    One stream a unit records, as its timeline lists it: how many records it holds and the time of
    its first and latest one, read without the records.

    `record_count` is 0 for a stream whose header is written and no record yet; both times are then
    None, since no record carries one. `cadence_timeframe` and `cadence_s` name the cadence a stream
    is written on; None says it is written per event, never for state frames.
    """
    stream_kind: RecordedStreamKind
    stream_name: str
    record_time_field: str
    record_count: int
    first_record_time: Optional[datetime] = None
    last_record_time: Optional[datetime] = None
    cadence_timeframe: Optional[str] = None
    cadence_s: Optional[float] = None
    truncated_tail: bool = False


class UnitRecordedStreams(BaseModel):
    """
    A unit's timeline (#580): every stream it records with its extent, and whether the session
    still records — the verdict the latest-frame route gives, from the same function.

    Args:
        run_id: The run
        unit_name: The unit
        served_at: When the answer was made, on the API server's wall clock
        recording: Whether the session still records; None before its first frame, or where it is
            not judged — a replay before its end
        key: What makes one stream's row unique
        streams: One row per stream the unit records
    """
    run_id: str
    unit_name: str
    served_at: datetime
    recording: Optional[RecordingState] = None
    key: List[str] = ['stream_kind', 'stream_name']
    streams: List[RecordedStreamExtent]


class RecordWindowBounds(BaseModel):
    """
    The stretch of time a window answer covers: [from, to) on each record's own time. Served as
    given, or, where a bound was left out, resolved from the records the window holds — `from` the
    first one's millisecond, `to` the millisecond after the newest one's; None when the window
    holds no record to resolve it by.
    """
    model_config = ConfigDict(populate_by_name=True)

    window_from: Optional[datetime] = Field(default=None, alias='from')
    window_to: Optional[datetime] = Field(default=None, alias='to')


class RecordedStateFrame(StateFrame):
    """
    A state frame with its position in the unit's stream (#580): counted from 1 over the sealed
    trading days in order and then the running file, so it identifies the frame in every answer —
    two frames may share an instant — and the newest one equals the timeline's `record_count`.
    """
    record_no: int


class RecordedStreamsWindow(BaseModel):
    """
    A unit's records within a time window, oldest first, at most `limit` of them unless one
    millisecond holds more (#580).

    `matched` counts the window's records before the limit, `returned` the ones served. A page
    never ends inside a millisecond, so a millisecond holding more records than `limit` is served
    whole and `returned` may exceed it. `next_from` is set only when the page left records out —
    `truncated` — and is the millisecond they begin in: the next page is asked from there.

    Args:
        run_id: The run
        unit_name: The unit
        served_at: When the answer was made, on the API server's wall clock
        window: The stretch covered
        matched: How many records the window holds
        returned: How many the page serves
        limit: The most it was asked to serve
        truncated: Whether it left records out
        next_from: Where the records left out begin, or None
        keys: What makes one row of each list unique
        state_frames: The unit's state frames in the window, oldest first
    """
    run_id: str
    unit_name: str
    served_at: datetime
    window: RecordWindowBounds
    matched: int
    returned: int
    limit: int
    truncated: bool
    next_from: Optional[datetime] = None
    keys: Dict[str, List[str]] = {'state_frames': ['record_no']}
    state_frames: List[RecordedStateFrame]


@dataclass(frozen=True)
class StateFrameStreamOverview:
    """
    A unit's state-frame stream at its two ends and by its trading days, from one read of its file
    — what its timeline is served from.

    Args:
        ends: The stream's header facts, its first frame and its latest one
        days: One row per trading day, oldest first, read without the frames
    """
    ends: StateFrameStreamEnds
    days: List[StreamDayIndexRow]


@dataclass(frozen=True)
class StateFramePage:
    """
    One page of a unit's state frames within a time window — what its window is served from.

    Args:
        unit_name: The unit the stream belongs to, from its header; None before the header was
            written
        frames: The page's frames, oldest first, each with its position in the stream
        matched: How many frames the window holds
        truncated: Whether the page left frames out
        next_time: Where the frames left out begin, or None
        newest_time: The time of the stream's newest frame, None when it holds none
    """
    unit_name: Optional[str]
    frames: List[RecordedStateFrame]
    matched: int
    truncated: bool
    next_time: Optional[datetime]
    newest_time: Optional[datetime]
