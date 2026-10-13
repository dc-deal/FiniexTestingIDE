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
from python.framework.utils.bar_period import is_complete_at

_FIXTURE = Path('tests/fixtures/warmup_history/kraken_ohlc_answers.json')


@pytest.fixture(scope='module')
def recording() -> dict:
    """The recorded answers the warmup suite reads."""
    return json.loads(_FIXTURE.read_text())


@pytest.fixture(scope='module')
def live_answer(recording) -> dict:
    """The venue's answer now, for the recording's pair and interval."""
    response = requests.get(KrakenOhlcBarFetcher.API_BASE + '/0/public/OHLC', params={
        'pair': recording['pair'], 'interval': recording['interval_minutes'],
        'since': int(time.time()) - 20 * 60}, timeout=15)
    response.raise_for_status()
    return response.json()


def _recorded_result(recording: dict) -> dict:
    return recording['after_commit']['answer']['result']


def _rows(result: dict) -> list:
    return next(value for key, value in result.items() if key != 'last')


class TestTheVenueStillAnswersAsRecorded:

    def test_no_error_and_the_same_result_keys(self, recording, live_answer):
        assert live_answer['error'] == []
        assert sorted(live_answer['result']) == sorted(_recorded_result(recording)), (
            'the result keys changed — the pair key or the commit marker')

    def test_every_field_keeps_its_type(self, recording, live_answer):
        recorded_types = [type(field) for field in _rows(_recorded_result(recording))[-1]]
        for row in _rows(live_answer['result']):
            assert [type(field) for field in row] == recorded_types, row

    def test_rows_are_still_stamped_with_their_opening_minute(self, recording, live_answer):
        interval_s = recording['interval_minutes'] * 60
        for row in _rows(live_answer['result']):
            assert row[0] % interval_s == 0, row

    def test_the_commit_marker_still_precedes_the_forming_row(self, live_answer):
        result = live_answer['result']
        assert isinstance(result['last'], int)
        assert _rows(result)[-1][0] > result['last']

    def test_the_reader_takes_only_finished_and_committed_bars(self, recording):
        now = datetime.now(timezone.utc)
        bars = KrakenOhlcBarFetcher().fetch_bars(recording['pair'], 'M1', 10, now)
        assert bars, 'the venue answered, and the reader took nothing'
        assert all(is_complete_at(datetime.fromisoformat(bar.timestamp), 'M1', now)
                   for bar in bars)
