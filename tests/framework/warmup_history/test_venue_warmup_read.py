"""
Venue Warmup Read — the live fetch takes a row by what it IS, never by where it stands (#588).

Kraken's OHLC answer stamps each row with its period's opening time; the row after the `last`
field is the period still forming. The fetch used to drop the LAST ROW, whatever it held. These
tests give the real fetcher answers in which position and content disagree, and hold it to the
two rules that decide instead: the period has ended by the reference instant (the archive's
rule), and the venue has committed it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher

UTC = timezone.utc
H1 = 3600


def _row(opened: datetime, close: float) -> list:
    """One OHLC row as the venue sends it: open time, prices as strings, volume, trade count."""
    price = str(close)
    return [int(opened.timestamp()), price, price, price, price, price, '1.0', 10]


def _fetch(monkeypatch, rows, committed_until, reference, count=10):
    """The real fetcher over a canned answer."""
    answer = {'ETHUSD': rows}
    if committed_until is not None:
        answer['last'] = int(committed_until.timestamp())
    fetcher = KrakenOhlcBarFetcher()
    monkeypatch.setattr(fetcher, '_fetch_ohlc', lambda pair, interval, since: answer)
    return [bar.timestamp for bar in fetcher.fetch_bars('ETHUSD', 'H1', count, reference)]


def _hours(*hours):
    return [datetime(2026, 8, 5, hour, tzinfo=UTC) for hour in hours]


class TestTheRowIsJudgedByItsPeriod:
    """Position never decides."""

    def test_the_forming_row_is_dropped(self, monkeypatch):
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12, 13)]
        kept = _fetch(monkeypatch, rows, _hours(12)[0], reference)
        assert kept[-1] == '2026-08-05T12:00:00+00:00'

    def test_a_complete_last_row_is_kept(self, monkeypatch):
        """An answer whose newest row is finished: dropping the last row cost a complete bar."""
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12)]
        kept = _fetch(monkeypatch, rows, _hours(12)[0], reference)
        assert kept == [f'2026-08-05T{hour}:00:00+00:00' for hour in (10, 11, 12)]

    def test_a_row_the_venue_has_not_committed_is_dropped(self, monkeypatch):
        """Our clock is past the period's end, the venue still adds trades to it."""
        reference = datetime(2026, 8, 5, 13, 0, 0, 300000, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12)]
        kept = _fetch(monkeypatch, rows, _hours(11)[0], reference)
        assert kept[-1] == '2026-08-05T11:00:00+00:00'

    def test_an_uncommitted_row_is_dropped_wherever_it_stands(self, monkeypatch):
        """The finished-but-uncommitted period sits BEFORE a fresh row: the last row is not it."""
        reference = datetime(2026, 8, 5, 13, 0, 0, 300000, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12, 13)]
        kept = _fetch(monkeypatch, rows, _hours(11)[0], reference)
        assert kept[-1] == '2026-08-05T11:00:00+00:00'

    def test_a_row_not_complete_at_the_reference_is_dropped_even_if_committed(self, monkeypatch):
        """Our clock is behind the venue's: the venue has committed what our rule calls open."""
        reference = datetime(2026, 8, 5, 12, 59, 59, 700000, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12, 13)]
        kept = _fetch(monkeypatch, rows, _hours(12)[0], reference)
        assert kept[-1] == '2026-08-05T11:00:00+00:00'

    def test_without_a_commit_marker_the_period_rule_alone_decides(self, monkeypatch):
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12, 13)]
        kept = _fetch(monkeypatch, rows, None, reference)
        assert kept[-1] == '2026-08-05T12:00:00+00:00'


class TestTheRead:
    """What the read asks the venue for and what it hands back."""

    def test_only_the_newest_count_bars_are_returned(self, monkeypatch):
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(9, 10, 11, 12, 13)]
        kept = _fetch(monkeypatch, rows, _hours(12)[0], reference, count=2)
        assert kept == ['2026-08-05T11:00:00+00:00', '2026-08-05T12:00:00+00:00']

    def test_the_read_reaches_back_from_the_reference_not_from_the_wall_clock(self, monkeypatch):
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        asked = {}

        def answer(pair, interval, since):
            asked['since'] = since
            return {'ETHUSD': [], 'last': 0}

        fetcher = KrakenOhlcBarFetcher()
        monkeypatch.setattr(fetcher, '_fetch_ohlc', answer)
        fetcher.fetch_bars('ETHUSD', 'H1', 5, reference)
        assert asked['since'] == int(reference.timestamp()) - (5 + 10) * H1

    def test_the_fetched_bars_claim_no_completeness_of_their_own(self, monkeypatch):
        """Completeness is stamped by the injection guard, after it has checked — not here."""
        reference = datetime(2026, 8, 5, 13, 20, tzinfo=UTC)
        rows = [_row(opened, 1.0) for opened in _hours(11, 12)]
        fetcher = KrakenOhlcBarFetcher()
        monkeypatch.setattr(
            fetcher, '_fetch_ohlc',
            lambda pair, interval, since: {'ETHUSD': rows, 'last': int(_hours(12)[0].timestamp())})
        assert not any(bar.is_complete for bar in fetcher.fetch_bars('ETHUSD', 'H1', 5, reference))


@pytest.mark.parametrize('lag_s', [0.3, 0.79, 1.5])
def test_the_measured_commit_lag_holds_the_period_back(monkeypatch, lag_s):
    """
    Measured on ETHUSD M1: 2026-10-12 19:48 UTC, `last` still named the period before 0.39 s
    after the boundary and the period that had just ended at 0.91 s; 2026-10-13 07:30 UTC, still
    the period before at 1.50 s and the ended one at 2.18 s. Inside the lag the finished period
    is not taken.
    """
    boundary = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)
    reference = boundary + timedelta(seconds=lag_s)
    rows = [_row(opened, 1.0) for opened in _hours(10, 11, 12)]
    kept = _fetch(monkeypatch, rows, _hours(11)[0], reference)
    assert kept[-1] == '2026-08-05T11:00:00+00:00'
