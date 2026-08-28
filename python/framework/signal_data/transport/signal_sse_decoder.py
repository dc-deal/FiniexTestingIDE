"""
FiniexTestingIDE - Signal SSE Decoder
Incremental Server-Sent-Events frame decoder for the producer's stream (#468).

Pure and socket-free by design: bytes in, dispatched frames out — no HTTP, no thread, and
no opinion about what a payload means. That is what lets the producer's committed frame
sample drive the parser directly, the same file the contract validator already reads,
instead of a test needing a server standing up to prove that a line was parsed.

Implements the SSE grammar rather than the two lines this producer happens to send. A
payload split across reads, a multi-byte character split across reads and a comment line
are all ordinary traffic on a real socket, and a decoder that assumes one frame per chunk
works until the first slow network and then stops working for reasons nobody can see.
"""

import codecs
from typing import List, Optional

from python.framework.exceptions.signal_data_errors import (
    SignalStreamFrameTooLargeError,
)
from python.framework.types.signal_data_types import (
    SignalStreamEventName,
    SignalStreamFrame,
)

# SSE's default event name for a frame carrying no `event:` line. The producer names every
# event, so a dispatched frame under this name means the shape changed — it is carried to
# the routing site to be reported there rather than guessed at here. Taken from the enum
# rather than written again: the routing site compares against that enum, and a second
# literal is a second thing to keep in step.
DEFAULT_EVENT_NAME = SignalStreamEventName.MESSAGE.value

# The longest single line this decoder will hold before giving up. An unterminated line is
# held until its newline arrives, so without a bound a producer emitting bytes without one
# grows the buffer until the process dies — on an unattended month-long session, for a
# reason nothing in the logs would explain. The producer's own envelopes run to tens of
# kilobytes, so this is two orders of magnitude of headroom, not a tight fit.
MAX_LINE_BYTES = 4 * 1024 * 1024


class SignalSseDecoder:
    """
    Turns a byte stream into dispatched SSE frames.

    Stateful across calls, which is the entire point: a chunk boundary falls wherever the
    network puts it, so the decoder holds the partial line, the partial UTF-8 character and
    the fields collected so far until the blank line that dispatches them.

    Line terminators: LF and CRLF. A lone CR is not treated as a terminator — no server
    emits one, and a trailing CR is genuinely ambiguous mid-stream (line end, or the first
    half of a CRLF split across two reads) without buffering that would delay every frame.
    """

    def __init__(self):
        """Initialize an empty decoder."""
        # A leading byte-order mark is stripped by the grammar itself, and it has to be:
        # left in place it becomes part of the first line's FIELD NAME, so the opening
        # frame silently loses its event name and arrives as contract growth.
        self._utf8 = codecs.getincrementaldecoder('utf-8-sig')()
        self._line_buffer = ''
        self._event = ''
        self._data: List[str] = []
        self._retry_ms: Optional[int] = None

    def feed(self, chunk: bytes) -> List[SignalStreamFrame]:
        """
        Decode one chunk and return every frame it completed.

        Args:
            chunk: Bytes as they arrived — may split a line, or a character

        Returns:
            Frames dispatched by this chunk in arrival order; empty when the chunk carried
            only part of a frame
        """
        self._line_buffer += self._utf8.decode(chunk)
        if len(self._line_buffer) > MAX_LINE_BYTES:
            held = len(self._line_buffer)
            self._reset()
            raise SignalStreamFrameTooLargeError(
                f'a single stream line reached {held} characters with no line ending — '
                f'the reader will not hold more than {MAX_LINE_BYTES}')
        frames: List[SignalStreamFrame] = []
        while '\n' in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split('\n', 1)
            frame = self._consume_line(line.rstrip('\r'))
            if frame is not None:
                frames.append(frame)
        return frames

    def get_retry_ms(self) -> Optional[int]:
        """
        The reconnection delay the producer last suggested in-band.

        Their `retry:` is a default for a client with no policy of its own, settled
        cross-repo as NOT authoritative — ours governs. Read so it can be reported, never
        so it can be obeyed.

        Returns:
            The suggested delay in milliseconds, or None when the stream sent none
        """
        return self._retry_ms

    def _reset(self) -> None:
        """Drop everything collected so far — used when the stream cannot be followed."""
        self._line_buffer = ''
        self._event = ''
        self._data = []

    def _consume_line(self, line: str) -> Optional[SignalStreamFrame]:
        """
        Apply one complete line to the decoder state.

        Args:
            line: One line with its terminator already removed

        Returns:
            The frame this line dispatched, or None when it only collected state
        """
        if not line:
            return self._dispatch()

        # A comment. The producer's sample carries its entire documentation header this
        # way, so this is the common case when the parser is driven from that file.
        if line.startswith(':'):
            return None

        field, _, value = line.partition(':')
        if value.startswith(' '):
            value = value[1:]

        if field == 'event':
            self._event = value
        elif field == 'data':
            self._data.append(value)
        elif field == 'retry':
            self._read_retry(value)
        # `id` is deliberately unhandled. The producer emits none, and honouring one would
        # make a conforming client send Last-Event-ID on reconnect — a header they do not
        # read, because `?since` is the only cursor.
        return None

    def _dispatch(self) -> Optional[SignalStreamFrame]:
        """
        Complete the collected fields into a frame, as the blank line demands.

        A blank line with no data collected dispatches nothing but still clears the event
        name, per the grammar — otherwise a stray blank line would rename the next frame.

        Returns:
            The completed frame, or None when nothing had been collected
        """
        event = self._event or DEFAULT_EVENT_NAME
        data = self._data
        self._event = ''
        self._data = []
        if not data:
            return None
        return SignalStreamFrame(event=event, data='\n'.join(data))

    def _read_retry(self, value: str) -> None:
        """
        Record the producer's suggested reconnection delay when it is a number.

        Args:
            value: The `retry:` field's value as it arrived
        """
        if value.isdigit():
            self._retry_ms = int(value)
