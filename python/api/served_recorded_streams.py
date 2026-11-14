"""
A unit's recorded streams as the API serves them (#580): its timeline — each stream with its record
count and the time of its first and latest record, and whether the session still records — and a
window of its records, a page at a time.

A unit records its state frames and, where its workers draw, one indicator series per worker
(#379). The timeline lists each stream as a row, with the stream's own time field, so a client draws
the axis from it; the verdict on the recording comes from the function the latest-frame route serves
it from, so the two cannot disagree. The window merges the records of all the unit's streams by
their time, so one page and one `next_from` serve them all: the limit counts every record served,
and a page never ends inside a millisecond or past the first record a stream left out.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple, Union

from python.api.served_state_frames import recording_of
from python.framework.constants.run_layout_constants import STATE_FRAMES_STREAM
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.types.api.recorded_stream_types import (
    IndicatorSeriesPage,
    IndicatorSeriesStreamOverview,
    RecordedStreamExtent,
    RecordedStreamsWindow,
    RecordWindowBounds,
    StateFramePage,
    StateFrameStreamOverview,
    UnitRecordedStreams,
)
from python.framework.types.api.report_types import RunInfo
from python.framework.types.chart_types.indicator_series_entry_types import (
    IndicatorSeriesEntry,
    IndicatorSeriesRecord,
)
from python.framework.types.config_types.state_frames_api_config_types import StateFramesApiConfig
from python.framework.types.run_stream_types import RecordedStreamKind, StreamDayIndexRow
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.time_utils import floor_to_millisecond
from python.framework.utils.timeframe_config_utils import TimeframeConfig


def describe_unit_recorded_streams(
    frames: Optional[StateFrameStreamOverview],
    series: List[Tuple[str, IndicatorSeriesStreamOverview]],
    unit_name: str,
    run: RunInfo,
    thresholds: StateFramesApiConfig,
    served_at: datetime,
) -> UnitRecordedStreams:
    """
    Serve a unit's timeline.

    Args:
        frames: The unit's state-frame stream at its two ends and by its days, its header read;
            None when it records none
        series: Each worker's indicator series stream by its days, by worker
        unit_name: The unit
        run: The run's row in the run list — where its ticks come from
        thresholds: How many cadences a frame may age
        served_at: The moment of the answer, on the wall clock

    Returns:
        The timeline, one row per recorded stream
    """
    streams: List[RecordedStreamExtent] = []
    recording = None
    if frames is not None:
        ends = frames.ends
        member = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.STATE_FRAMES]
        streams.append(_extent(
            RecordedStreamKind.STATE_FRAMES, STATE_FRAMES_STREAM, member.time_fields[0],
            frames.days, ends.cadence_timeframe, ends.cadence_s, ends.truncated_tail))
        recording = recording_of(ends, run, thresholds, served_at)
    series_member = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.INDICATOR_SERIES]
    for worker, overview in series:
        timeframe = overview.header.cadence_timeframe if overview.header is not None else None
        streams.append(_extent(
            RecordedStreamKind.INDICATOR_SERIES, worker, series_member.time_fields[0],
            overview.days, timeframe,
            TimeframeConfig.get_minutes(timeframe) * 60.0 if timeframe is not None else None,
            overview.truncated_tail))
    return UnitRecordedStreams(
        run_id=run.run_id, unit_name=unit_name, served_at=served_at, recording=recording,
        streams=streams)


def describe_recorded_streams_window(
    frames: Optional[StateFramePage],
    series: List[Tuple[str, IndicatorSeriesPage]],
    run_id: str,
    unit_name: str,
    window_from: Optional[datetime],
    window_to: Optional[datetime],
    limit: int,
    served_at: datetime,
) -> RecordedStreamsWindow:
    """
    Serve a unit's records within a time window — every stream's, merged by their time.

    Args:
        frames: The page of its state frames, None when it records none
        series: The page of each worker's indicator series, by worker
        run_id: The run
        unit_name: The unit
        window_from: The window's start as asked, None when it was left out
        window_to: Its end as asked, None when it was left out
        limit: The most records asked for, counted over every stream
        served_at: The moment of the answer, on the wall clock

    Returns:
        The window answer
    """
    series_pages = dict(series)
    items = [_Item(frame.frame_time, 0, '', index)
             for index, frame in enumerate(frames.frames if frames is not None else [])]
    for worker, page in series:
        items.extend(_Item(record.bar_time, 1, worker, index)
                     for index, record in enumerate(page.records))
    items.sort(key=lambda item: (item.time, item.kind_order, item.source))
    pages: List[Union[StateFramePage, IndicatorSeriesPage]] = (
        ([frames] if frames is not None else []) + [page for _, page in series])
    served, next_from = _cut(items, pages, limit)
    matched = sum(page.matched for page in pages)

    served_frames = [frames.frames[item.index] for item in served if item.kind_order == 0]
    served_bars: Dict[str, List[IndicatorSeriesRecord]] = {worker: [] for worker, _ in series}
    for item in served:
        if item.kind_order == 1:
            served_bars[item.source].append(series_pages[item.source].records[item.index])

    # A bound left out is resolved from the records the window holds and stays None when it holds
    # none: taken from the stream's own ends, it could fall on the wrong side of the bound asked
    if window_from is None and served:
        window_from = floor_to_millisecond(served[0].time)
    newest = [page.newest_time for page in pages if page.matched and page.newest_time is not None]
    if window_to is None and newest:
        # With no end asked, a window that holds a record reaches the stream's newest one
        window_to = floor_to_millisecond(max(newest)) + timedelta(milliseconds=1)
    return RecordedStreamsWindow(
        run_id=run_id, unit_name=unit_name, served_at=served_at,
        window=RecordWindowBounds(window_from=window_from, window_to=window_to),
        matched=matched, returned=len(served), limit=limit, truncated=len(served) < matched,
        next_from=next_from, state_frames=served_frames,
        indicator_series=[entry for worker, page in series
                          for entry in _entries_of(page, served_bars[worker])])


@dataclass(frozen=True)
class _Item:
    """
    One record of one stream, placed on the unit's time axis.

    Args:
        time: The record's time
        kind_order: 0 for a state frame, 1 for an indicator series bar — the order within an instant
        source: The worker of an indicator series, '' for the state frames
        index: The record's position on its stream's page
    """
    time: datetime
    kind_order: int
    source: str
    index: int


def _cut(items: List[_Item], pages: Sequence[Union[StateFramePage, IndicatorSeriesPage]],
         limit: int) -> Tuple[List[_Item], Optional[datetime]]:
    """
    The records the merged page serves, and where the rest begin.

    A stream's own page ends at its first record left out, so the merged page cannot reach past
    that instant without skipping it. Within that, the page holds at most `limit` records and never
    ends inside a millisecond — a millisecond holding more than `limit` is served whole.

    Args:
        items: Every stream's page, merged by time
        pages: The streams' pages, for where each left records out
        limit: The most records served

    Returns:
        (the records served, the instant the rest begin — None when nothing was left out)
    """
    bounds = [page.next_time for page in pages if page.truncated and page.next_time is not None]
    bound = min(bounds) if bounds else None
    eligible = [item for item in items if bound is None or item.time < bound]
    if len(eligible) <= limit:
        return eligible, bound
    cut_ms = floor_to_millisecond(eligible[limit].time)
    served = [item for item in eligible if floor_to_millisecond(item.time) < cut_ms]
    if served:
        return served, cut_ms
    first_ms = floor_to_millisecond(eligible[0].time)
    served = [item for item in eligible if floor_to_millisecond(item.time) == first_ms]
    rest = [item for item in eligible if floor_to_millisecond(item.time) > first_ms]
    return served, (floor_to_millisecond(rest[0].time) if rest else bound)


def _entries_of(page: IndicatorSeriesPage,
                bars: List[IndicatorSeriesRecord]) -> List[IndicatorSeriesEntry]:
    """
    A worker's series as served — one entry per layer, the bars of its own timeframe in parallel
    arrays.

    Args:
        page: The worker's page, for its header
        bars: The bars the merged page serves, oldest first, of every timeframe the worker draws on

    Returns:
        The entries; none while the stream's header is not yet on the disk
    """
    if page.header is None:
        return []
    entries: List[IndicatorSeriesEntry] = []
    for layer in page.header.series:
        own = [bar for bar in bars if bar.timeframe == layer.timeframe]
        entries.append(IndicatorSeriesEntry(
            **layer.model_dump(), times=[bar.bar_time for bar in own],
            data={plot.id: [bar.values.get(plot.id) for bar in own] for plot in layer.plots}))
    return entries


def _extent(kind: RecordedStreamKind, name: str, time_field: str, days: List[StreamDayIndexRow],
            cadence_timeframe: Optional[str], cadence_s: Optional[float],
            truncated_tail: bool) -> RecordedStreamExtent:
    """
    One row of a unit's timeline.

    Args:
        kind: The stream's kind
        name: Which stream of its kind
        time_field: The field its records' time is read from
        days: Its trading days, oldest first
        cadence_timeframe: The cadence it is written on, None for a stream written per event
        cadence_s: The same in seconds
        truncated_tail: Whether a last line cut off mid-write was left out

    Returns:
        The row
    """
    firsts = [day.first_time for day in days if day.first_time is not None]
    lasts = [day.last_time for day in days if day.last_time is not None]
    return RecordedStreamExtent(
        stream_kind=kind, stream_name=name, record_time_field=time_field,
        record_count=sum(day.record_count for day in days),
        first_record_time=firsts[0] if firsts else None,
        last_record_time=lasts[-1] if lasts else None,
        cadence_timeframe=cadence_timeframe, cadence_s=cadence_s, truncated_tail=truncated_tail)
