"""
Recorded Kraken Answer — the live warmup read, pinned against what the venue actually sent (#588).

`VenueStub` answers in the shape we measured. This file holds two answers the venue really sent,
verbatim, either side of the moment it committed a just-ended minute (recorded by
`python/experiments/venue_probes/probe_kraken_ohlc_commit.py`): the first still names the period
before as its newest committed one, the second names the minute that had just ended. Each goes
through the real fetcher at the instant it arrived. The live adapter release gate checks that the
venue still answers in this shape; when it goes red, the probe records the answers again.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.utils.bar_period import is_complete_at

UTC = timezone.utc
FIXTURE = Path('tests/fixtures/warmup_history/kraken_ohlc_answers.json')
SIDES = ('before_commit', 'after_commit')


@pytest.fixture(scope='module')
def recorded() -> dict:
    """The two recorded answers and when they arrived."""
    return json.loads(FIXTURE.read_text())


def _result(recorded: dict, side: str) -> dict:
    return recorded[side]['answer']['result']


def _rows(result: dict) -> list:
    return next(value for key, value in result.items() if key != 'last')


def _read(monkeypatch, recorded: dict, side: str) -> list:
    """The real fetcher over one recorded answer, at the instant that answer arrived."""
    fetcher = KrakenOhlcBarFetcher()
    monkeypatch.setattr(fetcher, '_fetch_ohlc',
                        lambda pair, interval, since: _result(recorded, side))
    received = datetime.fromisoformat(recorded[side]['received_at'])
    return fetcher.fetch_bars(recorded['pair'], 'M1', 30, received)


class TestTheRecordedShape:
    """What the reader relies on, as the venue actually sent it."""

    @pytest.mark.parametrize('side', SIDES)
    def test_one_pair_key_beside_the_commit_marker(self, recorded, side):
        result = _result(recorded, side)
        assert recorded[side]['answer']['error'] == []
        assert isinstance(result['last'], int)
        assert len([key for key in result if key != 'last']) == 1

    @pytest.mark.parametrize('side', SIDES)
    def test_rows_are_stamped_with_their_opening_minute(self, recorded, side):
        for row in _rows(_result(recorded, side)):
            assert len(row) == 8
            assert isinstance(row[0], int) and row[0] % 60 == 0
            assert all(isinstance(field, str) for field in row[1:7])
            assert isinstance(row[7], int)

    @pytest.mark.parametrize('side', SIDES)
    def test_the_newest_row_stands_after_the_commit_marker(self, recorded, side):
        result = _result(recorded, side)
        assert _rows(result)[-1][0] > result['last']


class TestTheReaderOverTheRecording:
    """Both rules at once: the period has ended, and the venue has committed it."""

    def test_before_the_commit_the_minute_that_just_ended_is_not_taken(
            self, monkeypatch, recorded):
        boundary = datetime.fromisoformat(recorded['boundary'])
        received = datetime.fromisoformat(recorded['before_commit']['received_at'])
        ended = boundary - timedelta(minutes=1)
        assert is_complete_at(ended, 'M1', received), (
            'the recording was taken after the boundary, so time alone calls the minute finished')

        bars = _read(monkeypatch, recorded, 'before_commit')

        assert bars[-1].timestamp == (ended - timedelta(minutes=1)).isoformat()

    def test_after_the_commit_it_is_taken_and_the_new_minute_is_not(self, monkeypatch, recorded):
        boundary = datetime.fromisoformat(recorded['boundary'])

        bars = _read(monkeypatch, recorded, 'after_commit')

        assert bars[-1].timestamp == (boundary - timedelta(minutes=1)).isoformat()
        assert all(bar.timestamp != boundary.isoformat() for bar in bars)

    def test_the_venue_prices_arrive_as_numbers(self, monkeypatch, recorded):
        newest_row = _rows(_result(recorded, 'after_commit'))[-2]
        bar = _read(monkeypatch, recorded, 'after_commit')[-1]
        assert (bar.open, bar.high, bar.low, bar.close) == tuple(float(v) for v in newest_row[1:5])
        assert bar.tick_count == newest_row[7]
        assert datetime.fromisoformat(bar.timestamp).tzinfo == UTC
