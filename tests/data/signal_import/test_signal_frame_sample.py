"""
FiniexTestingIDE - Frozen Stream-Frame Sample Contract

The producer's committed frame sample is the ground truth for the wire shape #468 will read.
Nothing validated it through the production model, so it was read by eye — and that cost us:
reissue 5 already carried `breaking_episode_start: false` on 2026-08-21, three days before the
field went live, while our declaration typed it as a timestamp. Every live envelope was then
rejected and the rejection was misfiled as the producer's outage.

This parses every `signal` frame of the committed sample through the SAME model the live
transport uses, so the next reissue checks itself instead of being read.
"""

import json
from pathlib import Path

import pytest

from python.framework.types.signal_data_types import SignalSnapshot

SAMPLE = Path('tests/fixtures/signals/signal_stream_frames_reissue7.sse')


def signal_frames() -> list:
    """
    Every `signal` frame's payload from the committed sample.

    Returns:
        The decoded envelopes, in file order
    """
    frames = []
    for block in SAMPLE.read_text(encoding='utf-8').split('\n\n'):
        lines = [line for line in block.splitlines() if line.strip()]
        if not any(line.strip() == 'event: signal' for line in lines):
            continue
        for line in lines:
            if line.startswith('data: '):
                frames.append(json.loads(line[6:]))
    return frames


class TestFrameSample:
    """The sample parses through the production reader, and its shapes hold."""

    def test_the_sample_is_present_and_carries_signal_frames(self):
        assert SAMPLE.exists(), f'the frozen frame sample is missing: {SAMPLE}'
        assert signal_frames(), 'no signal frames in the sample'

    def test_every_signal_frame_parses(self):
        """
        The whole point: the wire sample goes through the live model, unmodified.

        `collected_msc` is supplied here exactly as the transport supplies it — it is absent
        on the wire by contract, so a test that expected it would be testing the wrong shape.
        """
        for frame in signal_frames():
            SignalSnapshot.model_validate({**frame, 'collected_msc': 1756000000000})

    @pytest.mark.parametrize('field,kind', [
        ('breaking_episode_id', (str, type(None))),
        ('breaking_episode_start', bool),
    ])
    def test_the_episode_fields_keep_their_shape(self, field, kind):
        """
        A reissue that changes either shape fails here rather than at the next live session.

        `breaking_episode_start` is a FLAG, not a timestamp — the producer's episode start
        instant lives inside the opaque id, and typing this as a datetime is exactly the
        mistake this file exists to catch.

        The id is `str` **or `None`** on the wire: null means 'no episode', and the empty
        string is not a permitted stand-in for it. Our model normalizes null to `''` so
        consumers ask one question — that normalization is asserted where it belongs, on the
        model, not here on the wire.
        """
        rows = [row for frame in signal_frames() for row in frame.get('result', [])]
        assert rows, 'no per-symbol rows in the sample'
        expected = kind if isinstance(kind, tuple) else (kind,)
        names = ' | '.join(k.__name__ for k in expected)
        for row in rows:
            assert field in row, f'{field} missing from a sample row'
            assert isinstance(row[field], expected), (
                f'{field} is {type(row[field]).__name__}, expected {names}')

    def test_the_sample_id_is_opaque_when_populated(self):
        """
        The opacity rule against whatever the sample actually carries.

        Reissue 6 carries two populated ids, so this asserts. It keeps the skip branch on
        purpose: reissue 5 had none, the loop over an empty set was an assertion that could
        not fail, and a sample that once again lacks one must say so rather than pass
        silently. The rule itself is pinned unconditionally in TestIdOpacity below.
        """
        ids = {row.get('breaking_episode_id') or ''
               for frame in signal_frames() for row in frame.get('result', [])} - {''}
        if not ids:
            pytest.skip('the committed sample carries no populated episode id yet '
                        '(reissue 5 predates it) — TestIdOpacity covers the rule')
        for episode_id in ids:
            assert episode_id.count(':') >= 3, (
                'a populated id carries more colons than its three segments — splitting on '
                "':' is unsafe, which is why the contract calls it opaque")


class TestIdOpacity:
    """
    The opacity rule, pinned against ids the producer published rather than a sample.

    Split out because the sample-driven check can only assert what the sample happens to
    hold, and the shape that matters most — the PRODUCTION form, with spaces and a slash in
    its middle segment — is exactly what a mock fixture does not have. Calibrating path
    safety or column width on the narrow mock form would test the easy case.
    """

    # As published by the producer, 2026-08-25. Their episode key is the retrieval query, so
    # the middle segment is free text from their pipeline config.
    PRODUCTION_ID = (
        'forex_macro_sentiment:US Dollar Canadian Dollar USD/CAD Bank of Canada '
        'BOC:2026-08-23T20:20:14Z')
    # The mock generator keys on the base currency instead — same contract, much narrower
    # string: no spaces, no slash. Pinned so nobody sizes or escapes against this one.
    MOCK_ID = 'crypto_sentiment_mock:BTC:2026-04-30T04:50:17Z'

    def test_splitting_on_a_colon_mis_reads_the_id(self):
        """Three segments by contract, more than three colons in fact."""
        for episode_id in (self.PRODUCTION_ID, self.MOCK_ID):
            assert episode_id.count(':') > 2, episode_id
            assert len(episode_id.split(':')) > 3, (
                f'splitting {episode_id!r} on a colon yields more parts than segments')

    def test_the_production_form_is_not_path_safe(self):
        """
        It carries a space and a slash, so it must be encoded before any path or filename.

        The mock form carries neither — which is why this asserts on the production shape.
        """
        assert ' ' in self.PRODUCTION_ID
        assert '/' in self.PRODUCTION_ID
        assert ' ' not in self.MOCK_ID and '/' not in self.MOCK_ID, (
            'the mock form is deliberately narrower — do not calibrate escaping on it')

    def test_the_length_is_not_predictable_from_the_mock(self):
        """
        Bounded by their query text, not by anything we can size for.

        Measured: the production form is more than twice the mock's, so a column sized on the
        mock would truncate the real thing. Parquet strings are variable-length, so nothing
        truncates today — this pins the reason it must stay that way.
        """
        assert len(self.PRODUCTION_ID) > 2 * len(self.MOCK_ID)
        assert len(self.PRODUCTION_ID) > 64, 'a VARCHAR(64) would have cut this'
