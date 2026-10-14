"""
FiniexTestingIDE - Kraken OHLC Answer Shape (release gate, #588)

The warmup suite pins the live warmup read against two Kraken OHLC answers recorded verbatim
(`tests/fixtures/warmup_history/kraken_ohlc_answers.json`). A recording ages: the venue can rename
the result key, change a field's type, or stop stamping a row with its opening minute — and every
test reading the recording would stay green. This check asks the venue NOW and holds its answer to
the recording's shape. It runs with the live adapter release gate, which calls the venue anyway,
so the recording's freshness follows the release rhythm instead of a test going red with age.

Red here means the recording no longer describes the venue: record it again with
    python python/experiments/venue_probes/probe_kraken_ohlc_commit.py ETHUSD \\
        --record tests/fixtures/warmup_history/kraken_ohlc_answers.json
and look at what changed before trusting the live warmup read.

Public market data — no credentials, no account, no order. It places nothing and costs nothing.

Run explicitly:
    pytest tests/live_adapters/ -v -m live_adapter
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher

_FIXTURE = Path('tests/fixtures/warmup_history/kraken_ohlc_answers.json')


@pytest.fixture(scope='module')
def recording() -> dict:
    """The recorded answers the warmup suite reads."""
    return json.loads(_FIXTURE.read_text())


@pytest.fixture(scope='module')
def live_answer(recording) -> dict:
    """The venue's answer now for the recording's pair and interval, and its clock after it."""
    response = requests.get(KrakenOhlcBarFetcher.API_BASE + '/0/public/OHLC', params={
        'pair': recording['pair'], 'interval': recording['interval_minutes'],
        'since': int(time.time()) - 20 * 60}, timeout=15)
    response.raise_for_status()
    # The venue's own clock, read after the answer: a stamp compared against it cannot be
    # misjudged by a local clock that drifted
    clock = requests.get(KrakenOhlcBarFetcher.API_BASE + '/0/public/Time', timeout=15)
    clock.raise_for_status()
    return {'answer': response.json(), 'venue_time': clock.json()['result']['unixtime']}


def _recorded_result(recording: dict) -> dict:
    return recording['after_commit']['answer']['result']


def _rows(result: dict) -> list:
    return next(value for key, value in result.items() if key != 'last')


class TestTheVenueStillAnswersAsRecorded:

    def test_no_error_and_the_same_result_keys(self, recording, live_answer):
        answer = live_answer['answer']
        assert answer['error'] == []
        assert sorted(answer['result']) == sorted(_recorded_result(recording)), (
            'the result keys changed — the pair key or the commit marker')

    def test_every_field_keeps_its_type(self, recording, live_answer):
        recorded_types = [type(field) for field in _rows(_recorded_result(recording))[-1]]
        for row in _rows(live_answer['answer']['result']):
            assert [type(field) for field in row] == recorded_types, row

    def test_rows_are_still_stamped_with_their_opening_minute(self, recording, live_answer):
        """
        On the grid, and the newest row opens no later than the venue's clock.

        The grid alone holds for an opening and a closing stamp alike. The newest row is the
        period still forming: stamped with its opening it lies in the past, stamped with its
        closing in the future — unless the answer came within the moment after a period
        ended in which the venue may not list the new period yet.
        """
        interval_s = recording['interval_minutes'] * 60
        rows = _rows(live_answer['answer']['result'])
        for row in rows:
            assert row[0] % interval_s == 0, row
        assert rows[-1][0] <= live_answer['venue_time'], (
            f'the newest row is stamped {rows[-1][0]}, after the venue\'s clock '
            f'{live_answer["venue_time"]} — rows carry their closing instant now')

    def test_the_commit_marker_still_precedes_the_forming_row(self, live_answer):
        result = live_answer['answer']['result']
        assert isinstance(result['last'], int)
        assert _rows(result)[-1][0] > result['last']

    def test_the_reader_takes_only_committed_bars(self, monkeypatch, recording, live_answer):
        """
        The real fetcher over the live answer, read once every row's period has ended.

        Time alone would then take every row, the forming one included; only the answer's
        commit marker keeps the rows after it out, so the newest bar taken opens at `last`.
        """
        result = live_answer['answer']['result']
        fetcher = KrakenOhlcBarFetcher()
        monkeypatch.setattr(fetcher, '_fetch_ohlc', lambda pair, interval, since: result)
        every_row_ended = datetime.fromtimestamp(
            _rows(result)[-1][0] + recording['interval_minutes'] * 60, tz=timezone.utc)

        bars = fetcher.fetch_bars(recording['pair'], 'M1', 10, every_row_ended)

        assert bars[-1].timestamp == datetime.fromtimestamp(
            result['last'], tz=timezone.utc).isoformat()

    def test_the_reader_reads_the_venue(self, recording):
        """The real request path end to end: the venue answers and the reader takes bars."""
        bars = KrakenOhlcBarFetcher().fetch_bars(
            recording['pair'], 'M1', 10, datetime.now(timezone.utc))
        assert len(bars) == 10, 'the venue answered, and the reader took too little'
