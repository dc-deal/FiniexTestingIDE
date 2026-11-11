"""
State frames read back from a run stream — a live session's or a backtest scenario's.

The same rules as the order-event stream's reader: a last line cut off mid-write is left out and
reported, a broken line anywhere else or a header naming an unknown schema is a file this reader
cannot read, and says so.
"""

from typing import List, Tuple

from pydantic import ValidationError

from python.framework.constants.run_layout_constants import STATE_FRAMES_SCHEMA_VERSION
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.divergence_capture_types import RecordedFrames
from python.framework.types.run_stream_types import RunStreamLines
from python.framework.types.store_member_types import RunEntryMemberId

# The record kind a state frame carries in its stream
_FRAME_RECORD_KIND = 'frame'


def state_frames_of(stream: RunStreamLines, name: str,
                    location: str) -> Tuple[List[StateFrame], bool]:
    """
    The state frames of a stream read back.

    Args:
        stream: The stream's records, with its header
        name: What a refusal calls the stream
        location: Where a refusal says it lies

    Returns:
        (the frames in the order they were written, whether the last line was cut off)
    """
    header = stream.header
    if header is not None and header.get('schema_version') not in (
            None, STATE_FRAMES_SCHEMA_VERSION):
        raise ReportArtifactUnreadableError(
            name, location,
            f"schema_version {header.get('schema_version')!r}, this reader knows "
            f'{STATE_FRAMES_SCHEMA_VERSION}')
    frames: List[StateFrame] = []
    truncated = stream.truncated_tail
    last = len(stream.records) - 1
    for index, record in enumerate(stream.records):
        if record.get('record_kind') != _FRAME_RECORD_KIND:
            continue
        try:
            frames.append(StateFrame.model_validate(record))
        except ValidationError as e:
            if index == last and stream.last_unterminated:
                truncated = True
                continue
            raise ReportArtifactUnreadableError(
                name, location, f'record {index + 1}: {e}') from e
    return frames, truncated


def read_recorded_frames(reader: RunEntryReader, run_id: str, member_id: RunEntryMemberId,
                         **names: str) -> RecordedFrames:
    """
    One run unit's state frames, read whole across its sealed days and its own file.

    Args:
        reader: The run reader
        run_id: The run
        member_id: The frame stream — a session's, or a backtest scenario's
        names: One value per variable part of the stream's path

    Returns:
        The frames; what could not be read is named, never filled in
    """
    try:
        stream = reader.read_stream(run_id, member_id, **names)
    except (ReportArtifactUnreadableError, OSError, ValueError, KeyError) as e:
        return RecordedFrames(frames=[], unreadable=str(e))
    if stream is None:
        return RecordedFrames(frames=[], unreadable='the run records no state frames')
    try:
        frames, truncated = state_frames_of(stream, member_id.value, run_id)
    except ReportArtifactUnreadableError as e:
        return RecordedFrames(frames=[], unreadable=str(e))
    return RecordedFrames(
        frames=frames, unreadable='its last line was cut off mid-write' if truncated else '')
