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
from python.framework.types.signal_data_types import SignalHealthStatus
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


class TestJournalIdentity:
    """
    Which producer journal the envelopes came from — the one fact no envelope carries.

    Shown because two producer instances share a schema, a pipeline_id and a seq range, so
    a session against a development instance and one against the certified series are
    indistinguishable on screen without it.
    """

    def test_the_id_is_shown_with_its_name(self, now):
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=4914,
            health=SignalHealthStatus(journal_id='9c3fa4c80d95', journal_name='dev',
                                      probed_at=now)))
        assert '9c3fa4c80d95' in out
        assert 'dev' in out

    def test_no_probe_renders_no_journal_line(self):
        """A transport without a probe must not render an empty identity."""
        out = render(SignalTransportStats(configured=True, state='live', last_seq=4914))
        assert 'Journal' not in out

    def test_an_unnamed_journal_still_shows_its_id(self, now):
        """The id binds; a missed name lookup on the producer side is not an alarm."""
        out = render(SignalTransportStats(
            configured=True, state='live',
            health=SignalHealthStatus(journal_id='138c68e48b15', journal_name='unknown',
                                      probed_at=now)))
        assert '138c68e48b15' in out
        assert 'red' not in out

    def test_unidentified_is_marked(self, now):
        """No store attached or an unreadable identifier — the session is not certifiable."""
        out = render(SignalTransportStats(
            configured=True, state='live',
            health=SignalHealthStatus(journal_id=None, probed_at=now)))
        assert 'unidentified' in out
        assert 'red' in out

    def test_a_changed_journal_is_marked(self, now):
        """The seq position on the line above belongs to the previous journal."""
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=4914,
            health=SignalHealthStatus(journal_id='138c68e48b15', journal_name='production',
                                      probed_at=now, journal_changed=True)))
        assert 'CHANGED' in out
        assert 'bold red' in out

    def test_a_suspended_producer_budget_is_named(self, now):
        """
        A producer that stopped evaluating to save money reaches the panel as silence: the
        transport stays green and envelopes stop. Without this line the operator reads a
        healthy feed going stale and looks for a fault on our side.
        """
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=4914,
            health=SignalHealthStatus(journal_id='9c3fa4c80d95', journal_name='dev',
                                      probed_at=now, budget_suspended=True,
                                      budget_reason='daily cap reached')))
        assert 'budget suspended' in out
        assert 'daily cap reached' in out

    def test_a_healthy_producer_adds_no_line(self):
        """Noise in the quiet case is how a panel stops being read."""
        out = render(SignalTransportStats(
            configured=True, state='live',
            health=SignalHealthStatus(journal_id='9c3fa4c80d95', journal_name='dev',
                                      probed_at=datetime.now(timezone.utc))))
        assert 'Producer:' not in out

    def test_the_journal_line_sits_above_the_arrival_age(self, now):
        """Identity qualifies the position; both belong to the same reading."""
        out = render(SignalTransportStats(
            configured=True, state='live', last_seq=4914, last_envelope_at=now,
            health=SignalHealthStatus(journal_id='9c3fa4c80d95', journal_name='dev',
                                      probed_at=now)))
        assert out.index('Journal:') < out.index('Last Envelope:')
        assert out.index('Signal Feed:') < out.index('Journal:')


class TestAgeRendering:
    """Ages are read at a glance, so the unit must change with the magnitude."""

    @pytest.mark.parametrize('seconds,expected', [
        (42, '42s ago'),
        (125, '2m'),
        (7200, '2.0h ago'),
    ])
    def test_scales(self, seconds, expected):
        assert expected in AutoTraderLiveDisplay._format_age(seconds)
