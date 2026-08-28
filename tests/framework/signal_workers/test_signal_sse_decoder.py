"""
FiniexTestingIDE - SSE Frame Decoder (#468)

The stream transport's parser, driven by the producer's committed frame sample rather than
by hand-written strings. That file is the ground truth for the wire shape, and reading it by
eye has already cost this project once — reissue 5 carried `breaking_episode_start` as a flag
while our declaration typed it as a timestamp, every live envelope was rejected, and the
rejection was misfiled as the producer's outage.

Two halves. The sample proves the decoder handles real traffic including its documentation
header; the synthetic cases prove the grammar holds where the sample happens to be uniform —
a payload split across reads, a character split across reads, multiple `data:` lines. A
decoder tested only against one well-formed file is tested against the easy case.
"""

import hashlib
import json
from pathlib import Path

import pytest

from python.framework.exceptions.signal_data_errors import (
    SignalStreamFrameTooLargeError,
)
from python.framework.signal_data.transport.signal_sse_decoder import (
    MAX_LINE_BYTES,
    SignalSseDecoder,
)
from python.framework.types.signal_data_types import (
    SignalStreamControlCode,
    SignalStreamEventName,
    StreamControlFrame,
    StreamHeartbeatFrame,
)

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / 'fixtures' / 'signals' / 'signal_stream_frames_reissue7.sse'
)

# Where this fixture came from, pinned so its provenance is CHECKABLE rather than
# remembered. Reissue 6 arrived by hand and nothing recorded which build produced it;
# reissue 7 came from a commit-pinned URL in the producer's public repository, and a
# branch URL was declined on purpose — a fixture fetched from a branch changes under you
# the next time they regenerate, which is the one thing a committed fixture must not do.
SAMPLE_ORIGIN = ('https://raw.githubusercontent.com/dc-deal/FiniexRAGEngine/63f7f21/'
                 'docs/architecture/STREAM_FRAMES_SAMPLE.sse')
SAMPLE_SHA256 = '44e0585109b65d741bbad76b85765bdc04dcd3a6b69ed65fb097e2aa456a8293'


def sample_bytes() -> bytes:
    """
    The committed sample, completed to a dispatchable stream if it is not already.

    A no-op since reissue 7, and kept for what it documents. Reissue 6 ended without the
    blank line that dispatches its last frame, so a spec-conforming parser silently dropped
    the cold-start heartbeat — the very case section 6 exists to demonstrate. The fix
    belonged to the FILE and not to the decoder: on the wire an unterminated frame at
    connection close is discarded on purpose, because a socket dying mid-frame must never
    deliver half an envelope to the inbox. The producer's generator now refuses to write a
    sample that does not end terminated.

    Returns:
        The sample's bytes, terminated
    """
    raw = SAMPLE.read_bytes()
    return raw if raw.endswith(b'\n\n') else raw + b'\n'


def decode_sample() -> list:
    """
    Every frame of the committed sample, decoded in one pass.

    Returns:
        The dispatched frames, in file order
    """
    return SignalSseDecoder().feed(sample_bytes())


def heartbeat_frames() -> list:
    """
    The sample's heartbeat payloads, parsed through the production model.

    Returns:
        One StreamHeartbeatFrame per heartbeat frame, in file order
    """
    return [StreamHeartbeatFrame.model_validate(json.loads(frame.data))
            for frame in decode_sample()
            if frame.event == SignalStreamEventName.HEARTBEAT.value]


def control_frames() -> list:
    """
    The sample's control payloads, parsed through the production model.

    Returns:
        One StreamControlFrame per control frame, in file order
    """
    return [StreamControlFrame.model_validate(json.loads(frame.data))
            for frame in decode_sample()
            if frame.event == SignalStreamEventName.CONTROL.value]


class TestTheFrozenSample:
    """The producer's committed sample decodes, whole and unmodified."""

    def test_the_sample_is_present(self):
        assert SAMPLE.exists(), f'the frozen frame sample is missing: {SAMPLE}'

    def test_the_sample_is_the_one_its_provenance_names(self):
        """
        The fixture's identity, machine-checked instead of remembered.

        Going red here is not a failure to route around: it means the file changed, and a
        frozen sample changing is either a reissue — in which case bump both constants and
        say which commit — or a swap nobody meant. Re-fetch and compare with:

            curl -sL <SAMPLE_ORIGIN> | sha256sum
        """
        digest = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
        assert digest == SAMPLE_SHA256, (
            f'the committed sample is not the one {SAMPLE_ORIGIN} serves.\n'
            f'  expected {SAMPLE_SHA256}\n  actual   {digest}\n'
            f'A reissue? Bump SAMPLE_ORIGIN and SAMPLE_SHA256 together, and pin a COMMIT, '
            f'never a branch.')

    def test_the_documentation_header_dispatches_nothing(self):
        """
        The sample's entire header is SSE comment lines, which is the common case here.

        A decoder that mistook one for a field would turn the producer's prose into frames.
        """
        header = b''.join(
            line + b'\n' for line in SAMPLE.read_bytes().split(b'\n')
            if line.startswith(b':')
        )
        assert SignalSseDecoder().feed(header) == []

    def test_every_frame_carries_a_named_event(self):
        """
        The producer names every event. A dispatched 'message' means the shape changed.
        """
        for frame in decode_sample():
            assert frame.event != SignalStreamEventName.MESSAGE.value, (
                f'unnamed frame: {frame.data[:80]}')

    def test_every_event_name_is_one_the_contract_names(self):
        """
        The contract fixes WHICH names exist, not how many of each a reissue carries.

        Counting them exactly would pin the fixture rather than the contract — a reissue
        that adds a frame would go red for the wrong reason, and a red for the wrong reason
        trains people to update the number instead of reading it.
        """
        legal = {SignalStreamEventName.SIGNAL.value,
                 SignalStreamEventName.HEARTBEAT.value,
                 SignalStreamEventName.CONTROL.value}
        seen = {frame.event for frame in decode_sample()}
        assert seen <= legal, f'unnamed or unknown events: {sorted(seen - legal)}'
        assert seen == legal, (
            f'the sample must demonstrate all three kinds; missing {sorted(legal - seen)}')

    def test_every_payload_is_one_line_of_json(self):
        """
        One `data:` line per frame, by contract — so nothing here should need joining.
        """
        for frame in decode_sample():
            assert '\n' not in frame.data
            json.loads(frame.data)

    def test_the_retry_default_is_read(self):
        """
        Read so it can be reported, never so it can be obeyed — settled cross-repo as a
        default for a client with no policy of its own. Ours governs.
        """
        decoder = SignalSseDecoder()
        decoder.feed(sample_bytes())
        assert decoder.get_retry_ms() == 5000

    def test_the_sample_would_lose_its_last_frame_unterminated(self):
        """
        The sample must not lose a frame to its own ending.

        Reissue 6 did: it stopped after its last `data:` line, so a spec-conforming parser
        dropped the cold-start heartbeat. Reissue 7 terminates, and this asserts the file
        as committed decodes to the same frames as the completed one — which is the honest
        form of the check either way, and it keeps the helper's newline from hiding a
        regression the day a generator changes again.
        """
        raw = SAMPLE.read_bytes()
        decoded_as_committed = len(SignalSseDecoder().feed(raw))
        decoded_terminated = len(SignalSseDecoder().feed(sample_bytes()))
        if raw.endswith(b'\n\n'):
            assert decoded_as_committed == decoded_terminated
            return
        assert decoded_as_committed == decoded_terminated - 1, (
            'the sample is unterminated but its last frame is not the one lost')

    def test_the_sample_survives_being_split_at_every_byte_boundary(self):
        """
        The one property a socket will test in production and a file never will.

        Chunk boundaries fall wherever the network puts them, so the decoder must produce
        the identical frame sequence whether it is fed the file whole or in small pieces.
        """
        raw = sample_bytes()
        whole = SignalSseDecoder().feed(raw)
        for size in (1, 7, 64, 997):
            decoder = SignalSseDecoder()
            pieces = []
            for start in range(0, len(raw), size):
                pieces.extend(decoder.feed(raw[start:start + size]))
            assert pieces == whole, f'chunk size {size} decoded differently'


class TestControlFrames:
    """The five codes, as far as the sample carries them."""

    def test_every_control_frame_names_a_known_code(self):
        """
        An unknown code is contract GROWTH, not a fault — but the sample must not be the
        place one first appears unexplained.
        """
        for control in control_frames():
            assert control.resolve_code() is not None, (
                f'unknown control code: {control.code}')

    def test_the_codes_present_are_a_subset_of_the_five(self):
        """
        Which codes a reissue demonstrates is the sample's business; that every one of them
        is a code we route is the contract's.
        """
        codes = {control.resolve_code() for control in control_frames()}
        assert codes <= set(SignalStreamControlCode), f'unknown codes: {codes}'
        assert SignalStreamControlCode.LIVE in codes, (
            'a sample without control/live never shows the replay boundary')

    def test_replay_truncated_carries_what_a_recovery_needs(self):
        """
        The fields a recovery reads, not the numbers a reissue happens to show.

        `oldest_available_seq` is the load-bearing one: the cursor jumps to just before it
        so the next arrival is contiguous, and without it a truncation would read as a gap
        and start a replay the producer has already refused.
        """
        truncated = [c for c in control_frames()
                     if c.resolve_code() is SignalStreamControlCode.REPLAY_TRUNCATED]
        if not truncated:
            pytest.skip('this reissue demonstrates no replay_truncated frame')
        for frame in truncated:
            assert frame.oldest_available_seq is not None
            assert frame.requested_since is not None
            assert frame.oldest_available_seq > frame.requested_since, (
                'a truncation means they hold LESS history than was asked for')
            assert frame.window_hours is not None

    def test_cursor_ahead_carries_the_head_it_measured_us_against(self):
        """
        Both halves, because the operator message names both: what we asked for and what
        they actually hold. A code that said only "you are ahead" would leave a human with
        nothing to check the local archive against.
        """
        ahead = [c for c in control_frames()
                 if c.resolve_code() is SignalStreamControlCode.CURSOR_AHEAD]
        if not ahead:
            pytest.skip('this reissue demonstrates no cursor_ahead frame')
        for frame in ahead:
            assert frame.requested_since is not None
            assert frame.head_seq is not None
            assert frame.requested_since > frame.head_seq, (
                'ahead means our cursor is PAST their head, not behind it')

    def test_the_cold_start_control_says_nothing_yet_rather_than_nothing(self):
        """
        `head_seq` 0 is "no envelope ever", and it can never collide with a real seq —
        the producer's counter returns seq+1, so the first envelope is 1.

        Found by its own property rather than by position in the file: the last control
        frame happens to be the cold-start one in reissue 6, and a test resting on that
        would silently start asserting something else the moment a reissue reorders.
        """
        cold = [c for c in control_frames()
                if c.resolve_code() is SignalStreamControlCode.LIVE and c.head_seq == 0]
        assert cold, 'the sample no longer demonstrates the cold start (section 6)'
        assert all(c.stream_epoch is not None for c in cold), (
            'every frame carries the epoch it belongs to, cold start included')

    def test_epoch_changed_when_the_sample_carries_it(self):
        """
        Absent from reissue 6, present since reissue 7 — so this now RUNS.

        The skip branch stays rather than being deleted, and that is the point: it is what
        made the gap visible for the days the frame did not exist, instead of the check
        passing silently over nothing — the mistake this suite's sibling made by looping
        over an empty set. A future reissue that drops the frame will say so again.
        """
        rewound = [c for c in control_frames()
                   if c.resolve_code() is SignalStreamControlCode.EPOCH_CHANGED]
        if not rewound:
            pytest.skip('reissue 6 carries no epoch_changed frame — promised for reissue 7')
        for control in rewound:
            assert control.stream_epoch is not None, 'the NEW epoch is mandatory'
            assert control.previous_epoch is not None, 'the epoch we were on is mandatory'
            assert control.stream_epoch != control.previous_epoch
            assert control.head_seq is not None, 'head_seq spares the resync a round trip'


class TestHeartbeatFrames:
    """A keep-alive proves the socket, never the freshness."""

    def test_the_live_heartbeat_carries_a_position_and_a_producer_clock(self):
        """
        Shape, not values: the seq a given reissue happens to sit at is the sample's
        business, and asserting it pins the episode the producer drew from.
        """
        live = [b for b in heartbeat_frames() if b.seq]
        assert live, 'the sample no longer demonstrates a keep-alive on a live stream'
        for beat in live:
            assert beat.stream_epoch is not None
            assert beat.available_msc is not None, (
                'a stream that has published carries its availability instant')
            assert beat.now_msc is not None, (
                'now_msc is the producer clock a consumer measures skew against')

    def test_the_cold_start_heartbeat_has_no_availability_to_report(self):
        """
        `available_msc` is absent because there is nothing yet to be available; `now_msc`
        still proves the producer is alive. Optional here, mandatory on an envelope.
        """
        cold = [b for b in heartbeat_frames() if b.seq == 0]
        assert cold, 'the sample no longer demonstrates the cold-start keep-alive'
        for beat in cold:
            assert beat.available_msc is None, (
                'nothing has been published, so nothing can be available')
            assert beat.now_msc is not None


class TestTheGrammar:
    """Where the sample is uniform, the grammar still has to hold."""

    def test_a_frame_split_across_reads_dispatches_once_and_whole(self):
        decoder = SignalSseDecoder()
        assert decoder.feed(b'event: sig') == []
        assert decoder.feed(b'nal\ndata: {"seq":') == []
        frames = decoder.feed(b'7}\n\n')
        assert len(frames) == 1
        assert frames[0].event == 'signal'
        assert json.loads(frames[0].data) == {'seq': 7}

    def test_a_multibyte_character_split_across_reads_survives(self):
        """
        The producer's episode ids are free text — spaces, slashes, and whatever a headline
        contains. A decoder splitting UTF-8 on a chunk boundary corrupts exactly those.
        """
        payload = json.dumps({'title': 'Bank of Canada — Zinsentscheid €'}).encode('utf-8')
        raw = b'event: signal\ndata: ' + payload + b'\n\n'
        decoder = SignalSseDecoder()
        frames = []
        for index in range(len(raw)):
            frames.extend(decoder.feed(raw[index:index + 1]))
        assert len(frames) == 1
        assert json.loads(frames[0].data)['title'] == 'Bank of Canada — Zinsentscheid €'

    def test_multiple_data_lines_join_with_newlines(self):
        frames = SignalSseDecoder().feed(b'event: control\ndata: one\ndata: two\n\n')
        assert frames[0].data == 'one\ntwo'

    def test_only_one_leading_space_after_the_colon_is_stripped(self):
        frames = SignalSseDecoder().feed(b'event: control\ndata:  padded\n\n')
        assert frames[0].data == ' padded'

    def test_a_field_with_no_value_is_read_as_empty(self):
        frames = SignalSseDecoder().feed(b'event: control\ndata:\n\n')
        assert frames[0].data == ''

    def test_a_blank_line_with_nothing_collected_dispatches_nothing(self):
        assert SignalSseDecoder().feed(b'\n\n\n') == []

    def test_a_stray_blank_line_does_not_rename_the_next_frame(self):
        """
        The event name is cleared by the blank line even when no data was collected — the
        grammar's rule, and without it a keep-alive newline renames whatever comes next.
        """
        decoder = SignalSseDecoder()
        assert decoder.feed(b'event: heartbeat\n\n') == []
        frames = decoder.feed(b'data: {"seq":1}\n\n')
        assert frames[0].event == 'message'

    def test_an_id_line_is_ignored_entirely(self):
        """
        Honouring one would make a conforming client send Last-Event-ID on reconnect — a
        header the producer does not read, because `?since` is the only cursor.
        """
        frames = SignalSseDecoder().feed(b'id: 42\nevent: signal\ndata: {}\n\n')
        assert len(frames) == 1
        assert frames[0].event == 'signal'
        assert frames[0].data == '{}'

    def test_a_leading_byte_order_mark_is_stripped(self):
        """
        The grammar strips it, and it has to: left in place the BOM becomes part of the
        first line's FIELD NAME, so the opening frame silently loses its event name and
        arrives looking like contract growth.
        """
        frames = SignalSseDecoder().feed(
            b'\xef\xbb\xbfevent: signal\ndata: {"seq":1}\n\n')
        assert len(frames) == 1
        assert frames[0].event == 'signal'

    def test_crlf_terminators_decode_the_same_as_lf(self):
        crlf = SignalSseDecoder().feed(b'event: signal\r\ndata: {"seq":1}\r\n\r\n')
        lf = SignalSseDecoder().feed(b'event: signal\ndata: {"seq":1}\n\n')
        assert crlf == lf

    def test_an_unterminated_frame_is_held_rather_than_dispatched(self):
        """
        A socket that dies mid-frame must not deliver half an envelope to the inbox.
        """
        decoder = SignalSseDecoder()
        assert decoder.feed(b'event: signal\ndata: {"seq":1}\n') == []

    def test_a_line_without_an_ending_is_bounded(self):
        """
        An unterminated line is held until its newline arrives, so without a bound a
        producer emitting bytes without one grows the buffer until the process dies — on an
        unattended month-long session, for a reason nothing in the logs would explain.
        """
        decoder = SignalSseDecoder()
        with pytest.raises(SignalStreamFrameTooLargeError):
            for _ in range(MAX_LINE_BYTES // 4096 + 2):
                decoder.feed(b'x' * 4096)

    def test_the_decoder_recovers_after_refusing_an_oversized_line(self):
        """
        Refusing is not the same as dying: the buffer is dropped, so the connection that
        follows starts from a clean decoder rather than from a poisoned one.
        """
        decoder = SignalSseDecoder()
        with pytest.raises(SignalStreamFrameTooLargeError):
            for _ in range(MAX_LINE_BYTES // 4096 + 2):
                decoder.feed(b'x' * 4096)
        frames = decoder.feed(b'event: signal\ndata: {"seq":1}\n\n')
        assert len(frames) == 1
        assert frames[0].event == 'signal'

    @pytest.mark.parametrize('value,expected', [
        (b'5000', 5000),
        (b'0', 0),
        (b'not-a-number', None),
        (b'-1', None),
    ])
    def test_retry_is_read_only_when_it_is_a_number(self, value, expected):
        decoder = SignalSseDecoder()
        decoder.feed(b'retry: ' + value + b'\n\n')
        assert decoder.get_retry_ms() == expected
