"""
UTC normalisation in time_utils (§9).

`parse_datetime` promised a UTC-aware datetime and handed back whatever zone `dateutil` chose:
`tzlocal()` for a string whose offset matched the machine's zone, a fixed offset for any other.
The instant was right every time, which is why ten months of runs never showed it — but `.hour`,
`.date()` and `.weekday()` answered in that zone. It surfaced as a repr in an error message
(`tzinfo=tzlocal()` on a scenario window written `+00:00`), and only because this container runs
in UTC does `tzlocal()` there equal UTC.

So the cases run with the machine's zone set to one that is NOT UTC — the machine the suite
never runs on, and the one a contributor may.
"""

import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from python.framework.utils.time_utils import ensure_utc_aware, parse_datetime


@pytest.fixture
def machine_in_berlin(monkeypatch):
    """Set the process's local zone to Europe/Berlin for one test, and restore it after."""
    monkeypatch.setenv('TZ', 'Europe/Berlin')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize('text', [
    '2026-01-24T14:20:00+00:00',   # the offset every file here is written with
    '2026-01-24T15:20:00+01:00',   # Berlin in winter — the machine's own offset
    '2026-07-24T16:20:00+02:00',   # Berlin in summer
    '2026-01-24T14:20:00Z',
])
def test_every_offset_comes_back_as_utc(machine_in_berlin, text):
    parsed = parse_datetime(text)

    assert parsed.tzinfo is timezone.utc
    assert parsed.hour == 14, 'the hour is the UTC hour, whatever offset the string carried'


def test_a_naive_string_is_taken_as_utc(machine_in_berlin):
    parsed = parse_datetime('2026-01-24T14:20:00')

    assert parsed.tzinfo is timezone.utc and parsed.hour == 14


def test_the_utc_day_is_the_day_near_midnight(machine_in_berlin):
    """00:30 in Berlin on the 25th is still the 24th in UTC — `.date()` must say so."""
    parsed = parse_datetime('2026-01-25T00:30:00+01:00')

    assert parsed.date().isoformat() == '2026-01-24'


def test_an_aware_value_in_another_zone_is_converted_not_passed_through():
    new_york = datetime(2026, 1, 14, 17, 0, tzinfo=ZoneInfo('America/New_York'))

    converted = ensure_utc_aware(new_york)

    assert converted.tzinfo is timezone.utc and converted.hour == 22
    assert converted == new_york, 'the instant never moves'


def test_a_pandas_timestamp_stays_a_timestamp():
    """The coverage report hands in a bar's `Timestamp`; it must come back as one, in UTC."""
    stamp = pd.Timestamp('2026-01-24 15:20', tz='Europe/Berlin')

    converted = ensure_utc_aware(stamp)

    assert isinstance(converted, pd.Timestamp)
    assert converted.hour == 14 and converted == stamp
