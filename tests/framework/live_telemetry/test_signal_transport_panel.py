"""
Signal-transport block in the operator's CONNECTION panel (#141 Part 2a).

It exists because on an unattended multi-week run **a dead feed and a quiet market look
identical on screen**: the signal values keep displaying their last known state either way, so
only the transport can say whether anything still arrives.

Rendering is tested rather than eyeballed because this panel is read exactly when something is
wrong — and a panel that renders an idle connection in a session that never had one is worse than
no panel, since it answers a question it was not asked.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.types.autotrader_types.autotrader_display_types import (
    SignalTransportEvent, SignalTransportStats)
from python.framework.types.decision_logic_types import AwarenessLevel
from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay


def render(transport: SignalTransportStats) -> str:
    """Render the transport block and join it for substring assertions."""
    stats = MagicMock()
    stats.signal_transport = transport
    display = AutoTraderLiveDisplay.__new__(AutoTraderLiveDisplay)
    return '\n'.join(display._build_signal_transport_lines(stats))


@pytest.fixture
def now():
    """A fixed 'now' for age rendering."""
    return datetime.now(timezone.utc)


class TestMountedSession:
    """A session replaying a mounted series never had a transport."""

    def test_says_mounted_instead_of_faking_a_connection(self):
        out = render(SignalTransportStats())
        assert 'mounted (no transport)' in out
        assert 'Last Envelope' not in out

    def test_no_session_data_renders_nothing(self):
        display = AutoTraderLiveDisplay.__new__(AutoTraderLiveDisplay)
        assert display._build_signal_transport_lines(None) == []


class TestLiveTransport:
    """What the operator sees while envelopes are arriving."""

    def test_position_and_age(self, now):
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=4914, stream_epoch=1,
            last_envelope_at=now - timedelta(seconds=42)))
        assert 'epoch 1  seq 4914' in out
        assert '42s ago' in out

    def test_before_the_first_envelope(self):
        """A fresh session has a transport but no position yet — say so, do not print None."""
        out = render(SignalTransportStats(configured=True, state='live'))
        assert 'awaiting first envelope' in out
        assert 'None' not in out

    def test_tape_is_newest_first(self, now):
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=2, stream_epoch=1,
            last_envelope_at=now,
            tape=[SignalTransportEvent('seq 1 scheduled', now - timedelta(minutes=10)),
                  SignalTransportEvent('seq 2 breaking', now)],
            total_events=2))
        assert out.index('seq 2 breaking') < out.index('seq 1 scheduled')

    def test_older_events_are_counted_not_dropped_silently(self, now):
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=9, stream_epoch=1,
            last_envelope_at=now,
            tape=[SignalTransportEvent('seq 9 scheduled', now)],
            total_events=14))
        assert '+13 older feed events' in out


class TestTrouble:
    """The states the panel exists for."""

    def test_degraded_producer_is_visible(self, now):
        out = render(SignalTransportStats(
            configured=True, state='degraded', last_seq=4914, stream_epoch=1,
            last_envelope_at=now - timedelta(minutes=8), degraded_responses=3))
        assert 'degraded' in out
        assert '3 degraded' in out

    def test_transport_errors_are_counted(self, now):
        out = render(SignalTransportStats(
            configured=True, state='error', transport_errors=2,
            last_envelope_at=now - timedelta(minutes=30)))
        assert '2 errors' in out

    def test_a_healthy_transport_shows_no_issue_line(self, now):
        """Noise in the quiet case is how a panel stops being read."""
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=1, stream_epoch=1,
            last_envelope_at=now))
        assert 'Feed Issues' not in out

    def test_severity_reaches_the_tape(self, now):
        out = render(SignalTransportStats(
            configured=True, state='error', last_envelope_at=now,
            tape=[SignalTransportEvent('transport failed: URLError', now,
                                       AwarenessLevel.ALERT)],
            total_events=1))
        assert 'bold red' in out


class TestAgeRendering:
    """Ages are read at a glance, so the unit must change with the magnitude."""

    @pytest.mark.parametrize('seconds,expected', [
        (42, '42s ago'),
        (125, '2m'),
        (7200, '2.0h ago'),
    ])
    def test_scales(self, seconds, expected):
        assert expected in AutoTraderLiveDisplay._format_age(seconds)
