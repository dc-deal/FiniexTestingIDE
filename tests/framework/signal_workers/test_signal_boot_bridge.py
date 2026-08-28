"""
FiniexTestingIDE - Signal Boot Bridge (#468)

What a live session knows before its first envelope arrives. Without the bridge it knows
nothing: the SIGNAL workers start empty and the first decision waits out a full producer
cadence. On a thirty-day unattended run that is not a corner case — it is every restart.

The distinction the tests are built around is BLIND versus STALE. Knowing something old is
a strictly better input to a staleness contract than knowing nothing, because "old" is a
fact a decision logic can act on. The cursor is the other half: without it the stream can
only ask for the current snapshot, and everything published while we were down is lost.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.transport import signal_boot_bridge
from python.framework.signal_data.transport.signal_boot_bridge import SignalBootBridge
from python.framework.types.signal_data_types import (
    SentimentResult,
    SignalSeries,
    SignalSnapshot,
    SignalStreamCursor,
)

SIGNAL_KIND = 'llm_sentiment'
PIPELINE = 'crypto_sentiment'
SYMBOL = 'BTCUSD'
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def stream_snapshot(hours_ago: float, seq=None, epoch=None) -> SignalSnapshot:
    """One archived snapshot, with or without a stream identity."""
    at = NOW - timedelta(hours=hours_ago)
    return SignalSnapshot(
        collected_msc=at,
        available_msc=at,
        schema_version='2.0',
        pipeline_id=PIPELINE,
        seq=seq,
        stream_epoch=epoch,
        status='success',
        result=[SentimentResult(symbol=SYMBOL, signal='HOLD',
                                sentiment_score=0.1, confidence=0.5)])


class FakeIndex:
    """
    A signal index that reports whichever files the test wants it to.

    Mirrors the real manager's CONTRACT and not just its shape: the index must be built
    (loaded) before it is asked, and asking first answers "nothing" rather than raising.
    The first version of this fake had no `build_index` at all, so the bridge could omit
    the call and every test still passed — while a live session reported "starts blind"
    for an empty index it had never loaded.
    """

    def __init__(self, files):
        self._files = files
        self.calls = []
        self.built = False

    def build_index(self, force_rebuild: bool = False):
        """Load the index, as the real manager's callers all do first."""
        self.built = True

    def get_relevant_files(self, pipeline_id, symbol, start, end):
        """Record the lookup and answer with the scripted files — empty when unbuilt."""
        self.calls.append((pipeline_id, symbol, start, end))
        if not self.built:
            return []
        return self._files


@pytest.fixture
def loaded(monkeypatch):
    """
    Replace the parquet read with a scripted series.

    The bridge's own job is the window, the cursor and the verdict — reading parquet is
    the reader's, and it is pinned in its own suite.
    """
    def install(*snapshots):
        series = SignalSeries(signal_kind=SIGNAL_KIND, snapshots=list(snapshots))
        monkeypatch.setattr(
            signal_boot_bridge, 'load_signal_series_from_parquet',
            lambda *args, **kwargs: series)
        return series
    return install


def mount(index, logger=None):
    """Run the bridge against a fake index, with the producer's real replay window."""
    return SignalBootBridge.mount(
        pipeline_id=PIPELINE, symbol=SYMBOL, signal_kind=SIGNAL_KIND,
        replay_window_hours=24.0, now=NOW,
        logger=logger or MagicMock(), index_manager=index)


class TestWhenThereIsNothingToMount:
    """A blind start is legitimate — it must be reported, not treated as a failure."""

    def test_no_configured_pipeline_mounts_nothing(self):
        result = SignalBootBridge.mount(
            pipeline_id='', symbol=SYMBOL, signal_kind=SIGNAL_KIND,
            replay_window_hours=24.0, now=NOW, logger=MagicMock(),
            index_manager=FakeIndex([]))
        assert result.series.snapshots == []
        assert result.cursor is None

    def test_an_empty_archive_starts_blind_and_says_so(self):
        result = mount(FakeIndex([]))
        assert result.cursor is None
        assert 'blind' in result.reason

    def test_files_that_hold_no_snapshot_for_the_symbol_start_blind(self, loaded):
        loaded()
        result = mount(FakeIndex(['a.parquet']))
        assert result.series.snapshots == []
        assert result.cursor is None
        assert 'blind' in result.reason


class TestTheCursor:
    """The cursor is what turns a restart into a resume."""

    def test_it_is_the_newest_position_in_the_slice(self, loaded):
        loaded(stream_snapshot(6, seq=1041, epoch=1),
               stream_snapshot(4, seq=1042, epoch=1),
               stream_snapshot(2, seq=1043, epoch=1))
        result = mount(FakeIndex(['a.parquet']))
        assert result.cursor == SignalStreamCursor(epoch=1, seq=1043)

    def test_a_pre_stream_archive_yields_no_cursor(self, loaded):
        """
        The first session cannot use `?since` — our archive predates the stream contract
        and carries no position at all. A property to state, not a bug to work around.
        """
        loaded(stream_snapshot(6), stream_snapshot(4), stream_snapshot(2))
        result = mount(FakeIndex(['a.parquet']))
        assert result.series.snapshots
        assert result.cursor is None
        assert 'no cursor' in result.reason

    def test_an_archive_spanning_the_boundary_takes_the_newest_identity(self, loaded):
        """
        An archive written across the contract change holds both shapes. Taking the LAST
        row rather than the newest identity-bearing one would answer None whenever the
        projection's final row happens to predate the field.
        """
        loaded(stream_snapshot(9, seq=1040, epoch=1), stream_snapshot(8), stream_snapshot(7))
        result = mount(FakeIndex(['a.parquet']))
        assert result.cursor == SignalStreamCursor(epoch=1, seq=1040)

    def test_both_halves_are_required_for_a_position(self, loaded):
        """
        A seq belongs to an epoch. Half a cursor would make the producer answer 400, and
        it would deserve to.
        """
        loaded(stream_snapshot(3, seq=1043, epoch=None))
        assert mount(FakeIndex(['a.parquet'])).cursor is None


class TestTheIndexIsLoadedBeforeItIsAsked:
    """
    The bridge builds the index it constructs. Found by running a live session, not by a
    test: the first version queried an unloaded index, got nothing, and reported "no
    archived signals — the session starts blind". Blind is a legitimate answer, so nobody
    would have looked twice at a message that was describing its own omission.
    """

    def test_the_index_is_built_before_the_lookup(self, loaded):
        loaded(stream_snapshot(2, seq=1043, epoch=1))
        index = FakeIndex(['a.parquet'])
        result = mount(index)
        assert index.built, 'the bridge must load the index it is about to query'
        assert result.series.snapshots, 'and then actually see what is in it'

    def test_an_unbuilt_index_would_have_reported_a_blind_start(self, loaded):
        """
        The defect, pinned from the other side: without the build the lookup answers
        nothing and the bridge reports exactly what it reported in the live session.
        """
        loaded(stream_snapshot(2, seq=1043, epoch=1))
        index = FakeIndex(['a.parquet'])
        index.build_index = lambda force_rebuild=False: None   # the omission, restored
        result = mount(index)
        assert result.cursor is None
        assert 'blind' in result.reason


class TestTheWindow:
    """The slice is bounded by what the producer would replay, so the two meet."""

    def test_the_lookup_window_is_the_replay_window(self, loaded):
        loaded(stream_snapshot(2, seq=1043, epoch=1))
        index = FakeIndex(['a.parquet'])
        mount(index)
        _, _, start, end = index.calls[0]
        assert end == NOW
        assert start == NOW - timedelta(hours=24)

    def test_a_cursor_inside_the_window_is_not_flagged(self, loaded):
        loaded(stream_snapshot(2, seq=1043, epoch=1))
        assert mount(FakeIndex(['a.parquet'])).beyond_replay_window is False

    def test_a_cursor_older_than_the_window_is_flagged(self, loaded):
        """
        The index deliberately returns the carrier of the last snapshot at or before the
        window's start, so an old archive still mounts — STALE rather than BLIND. What it
        cannot do is be replayed from, and the operator is told that before it happens.
        """
        loaded(stream_snapshot(40, seq=900, epoch=1))
        result = mount(FakeIndex(['a.parquet']))
        assert result.beyond_replay_window is True
        assert result.cursor == SignalStreamCursor(epoch=1, seq=900)
