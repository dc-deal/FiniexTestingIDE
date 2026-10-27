"""
A unit's recorded streams as the API serves them (#580): its timeline — each stream with its record
count and the time of its first and latest record, and whether the session still records — and a
window of its records, a page at a time.

Today a unit records its state frames, and the timeline lists that one stream. Its row carries the
stream's own time field, so a client draws the axis from it; the verdict on the recording comes from
the function the latest-frame route serves it from, so the two cannot disagree.
"""

from datetime import datetime, timedelta
from typing import Optional

from python.api.served_state_frames import recording_of
from python.framework.constants.run_layout_constants import STATE_FRAMES_STREAM
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.types.api.recorded_stream_types import (
    RecordedStreamExtent,
    RecordedStreamsWindow,
    RecordWindowBounds,
    StateFramePage,
    StateFrameStreamOverview,
    UnitRecordedStreams,
)
from python.framework.types.api.report_types import RunInfo
from python.framework.types.config_types.state_frames_api_config_types import StateFramesApiConfig
from python.framework.types.run_stream_types import RecordedStreamKind
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.time_utils import floor_to_millisecond


def describe_unit_recorded_streams(
    overview: StateFrameStreamOverview,
    run: RunInfo,
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> UnitRecordedStreams:
    """
    Serve a unit's timeline.

    Args:
        overview: The unit's state-frame stream at its two ends and by its days, its header read
        run: The run's row in the run list — where its ticks come from
        thresholds: How many cadences a frame may age
        served_at: The moment of the answer, on the wall clock

    Returns:
        The timeline, one row per recorded stream
    """
    ends = overview.ends
    firsts = [day.first_time for day in overview.days if day.first_time is not None]
    lasts = [day.last_time for day in overview.days if day.last_time is not None]
    member = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.STATE_FRAMES]
    extent = RecordedStreamExtent(
        stream_kind=RecordedStreamKind.STATE_FRAMES,
        stream_name=STATE_FRAMES_STREAM,
        record_time_field=member.time_fields[0],
        record_count=sum(day.record_count for day in overview.days),
        first_record_time=firsts[0] if firsts else None,
        last_record_time=lasts[-1] if lasts else None,
        cadence_timeframe=ends.cadence_timeframe,
        cadence_s=ends.cadence_s,
        truncated_tail=ends.truncated_tail,
    )
    return UnitRecordedStreams(
        run_id=run.run_id, unit_name=ends.unit_name, served_at=served_at,
        recording=recording_of(ends, run, thresholds, served_at), streams=[extent])


def describe_recorded_streams_window(
    page: StateFramePage,
    run_id: str,
    window_from: Optional[datetime],
    window_to: Optional[datetime],
    limit: int,
    served_at: datetime,
) -> RecordedStreamsWindow:
    """
    Serve a unit's records within a time window.

    Args:
        page: The page read, its frames numbered
        run_id: The run
        window_from: The window's start as asked, None when it was left out
        window_to: Its end as asked, None when it was left out
        limit: The most records asked for
        served_at: The moment of the answer, on the wall clock

    Returns:
        The window answer
    """
    # A bound left out is resolved from the records the window holds and stays None when it holds
    # none: taken from the stream's own ends, it could fall on the wrong side of the bound asked
    if window_from is None and page.frames:
        window_from = floor_to_millisecond(page.frames[0].frame_time)
    if window_to is None and page.matched and page.newest_time is not None:
        # With no end asked, a window that holds a record reaches the stream's newest one
        window_to = floor_to_millisecond(page.newest_time) + timedelta(milliseconds=1)
    return RecordedStreamsWindow(
        run_id=run_id, unit_name=page.unit_name, served_at=served_at,
        window=RecordWindowBounds(window_from=window_from, window_to=window_to),
        matched=page.matched, returned=len(page.frames), limit=limit, truncated=page.truncated,
        next_from=page.next_time, state_frames=page.frames)
