"""
A broker server's clock turned into UTC, through the zone's own daylight saving rules.

The MT5 server runs on New York close time: New York plus seven hours, UTC+2 while New York keeps
standard time and UTC+3 in daylight time. The importer used to subtract a fixed three hours, which
stored every tick recorded in US winter one hour early. These cases pin the rule on the anchors
that exposed it: the US payroll report (08:30 New York) and the weekly forex open (17:00 New York,
midnight on the server).
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from python.framework.utils.time_utils import server_clock_to_utc, server_clock_to_utc_ms

NEW_YORK = 'America/New_York'
HOURS_AHEAD = 7


def _ms(text: str) -> int:
    """
    Read a naive wall-clock time as epoch milliseconds — a server stamp the way a collector
    writes `time_msc`, or a UTC instant; the reading is the same arithmetic for both.

    Args:
        text: ISO time without an offset

    Returns:
        Epoch milliseconds of that wall-clock reading
    """
    return (datetime.fromisoformat(text) - datetime(1970, 1, 1)) // timedelta(milliseconds=1)


_utc = _ms


class TestTheTwoSeasons:
    """One rule, two offsets — the whole point of not writing a number down."""

    @pytest.mark.parametrize('server,utc', [
        # Payroll is released at 08:30 New York: 13:30 UTC in winter, 12:30 UTC in summer.
        ('2026-01-09 15:30:00', '2026-01-09 13:30:00'),
        ('2026-04-03 15:30:00', '2026-04-03 12:30:00'),
        # The forex week opens at 17:00 New York, which is midnight on this server.
        ('2026-01-12 00:00:00', '2026-01-11 22:00:00'),
        ('2026-06-08 00:00:00', '2026-06-07 21:00:00'),
    ])
    def test_the_anchor_lands_on_its_utc_time(self, server, utc):
        """The same server wall-clock time means a different UTC instant in each season."""
        converted = server_clock_to_utc_ms(np.array([_ms(server)]), NEW_YORK, HOURS_AHEAD)

        assert int(converted[0]) == _utc(utc)

    def test_the_server_follows_the_us_switch_not_the_eu_switch(self):
        """
        Between 2026-03-08 (US) and 2026-03-29 (EU) only a US rule is already in summer.

        A clock on EU rules would still be two hours ahead here; this server is three.
        """
        converted = server_clock_to_utc_ms(
            np.array([_ms('2026-03-20 12:00:00')]), NEW_YORK, HOURS_AHEAD)

        assert int(converted[0]) == _utc('2026-03-20 09:00:00')


class TestTheTransitionHours:
    """An hour the zone repeats or skips has no single UTC answer, so it is refused."""

    @pytest.mark.parametrize('server', [
        # New York repeats 01:00-01:59 on 2025-11-02 — 08:00-08:59 on this server.
        '2025-11-02 08:30:00',
        # New York skips 02:00-02:59 on 2026-03-08 — 09:00-09:59 on this server.
        '2026-03-08 09:30:00',
    ])
    def test_a_stamp_inside_the_changed_hour_is_refused(self, server):
        """Guessing either reading would move the tick by an hour without a trace."""
        with pytest.raises(ValueError, match='no single UTC time'):
            server_clock_to_utc_ms(np.array([_ms(server)]), NEW_YORK, HOURS_AHEAD)

    def test_the_minutes_around_the_changed_hour_still_convert(self):
        """The refusal is the hour itself, not the whole Sunday morning."""
        before = _ms('2025-11-02 07:59:00')
        after = _ms('2025-11-02 09:00:00')

        converted = server_clock_to_utc_ms(np.array([before, after]), NEW_YORK, HOURS_AHEAD)

        assert int(converted[0]) == _utc('2025-11-02 04:59:00')   # still daylight time
        assert int(converted[1]) == _utc('2025-11-02 07:00:00')   # standard time


class TestTheArrayForm:
    """What the importer relies on when it converts a whole file in one call."""

    def test_order_and_length_are_kept(self):
        """Ticks are not sorted for the conversion, so out-of-order input stays out of order."""
        stamps = np.array([_ms('2026-06-01 10:00:00'), _ms('2026-01-05 10:00:00'),
                           _ms('2026-06-01 09:00:00')])

        converted = server_clock_to_utc_ms(stamps, NEW_YORK, HOURS_AHEAD)

        assert converted.tolist() == [_utc('2026-06-01 07:00:00'), _utc('2026-01-05 08:00:00'),
                                      _utc('2026-06-01 06:00:00')]

    def test_milliseconds_survive(self):
        """Only whole hours move — the sub-second part of a tick is untouched."""
        converted = server_clock_to_utc_ms(
            np.array([_ms('2026-01-05 10:00:00') + 75]), NEW_YORK, HOURS_AHEAD)

        assert int(converted[0]) == _utc('2026-01-05 08:00:00') + 75

    def test_utc_at_zero_hours_is_left_as_it_is(self):
        """Kraken's clock is UTC: nothing to resolve, nothing to cost."""
        stamps = np.array([_ms('2026-01-05 10:00:00'), _ms('2026-06-01 10:00:00')])

        assert server_clock_to_utc_ms(stamps, 'UTC', 0).tolist() == stamps.tolist()

    def test_an_empty_file_converts_to_nothing(self):
        """A file without ticks is refused elsewhere; the conversion must not be the one to fail."""
        assert server_clock_to_utc_ms(np.array([], dtype=np.int64), NEW_YORK, HOURS_AHEAD).size == 0


class TestTheSingleValueForm:
    """The coverage report reads one open time per file name; it must answer the same."""

    @pytest.mark.parametrize('server', [
        '2025-12-01 00:00:00', '2026-01-09 15:30:00', '2026-03-20 12:00:00',
        '2026-06-08 00:00:00', '2025-10-31 23:59:59',
    ])
    def test_it_agrees_with_the_array_form(self, server):
        """One call through the other — compared anyway, because the wrapper converts types."""
        single = server_clock_to_utc(datetime.fromisoformat(server), NEW_YORK, HOURS_AHEAD)
        array = server_clock_to_utc_ms(np.array([_ms(server)]), NEW_YORK, HOURS_AHEAD)

        assert single.tzinfo is timezone.utc
        assert single == datetime.fromtimestamp(int(array[0]) / 1000, tz=timezone.utc)
