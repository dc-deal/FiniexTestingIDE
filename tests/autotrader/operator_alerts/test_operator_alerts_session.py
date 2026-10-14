"""
Operator Alerts in a Session — the wiring, end to end, on a short mock replay (#235).

The channel and the monitor are stand-ins that keep what they were sent; everything between
them and the tick loop is the real thing: the session builds its channels at startup, says when
the loop starts, pings while it passes, and ends with one closing message and its last word to
the monitor. An abort is ONE message, sent at once — not a second one at the end. A boot step
that refuses to start is one message too, quoting why, and a loop that never ran is vouched for
by no ping at all.
"""

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.cold_start_setup import ColdStartSetup
from python.framework.autotrader.operator_alerts.liveness_pinger import LivenessPinger
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.autotrader.operator_alerts.operator_alert_dispatcher import (
    OperatorAlertDispatcher,
)
from python.framework.autotrader.operator_alerts.operator_alert_messages import session_label
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.types.config_types.operator_alerts_config_types import (
    LivenessPingConfig,
    TelegramAlertsConfig,
)
from python.framework.types.run_outcome_types import RunOutcome
from tests.autotrader.operator_alerts.operator_alert_doubles import (
    FAIL_URL,
    PING_URL,
    Monitor,
    ScriptedChannel,
)
from tests.shared.fixture_helpers import remove_run_dir

# The shortest mock replay there is — 100 ticks; what it trades does not matter here
PROFILE = 'configs/autotrader_profiles/mock/diagnostics_probe_test.json'

# What the cold start says when an unattended restart finds orders nobody confirmed
REFUSAL = ('❌ COLD START ABORTED — 1 resting order(s) of ours need confirmation and nobody '
           "declared themselves present (adoption_mode='operator_confirm').")


@pytest.fixture
def recorded(monkeypatch):
    """
    Swap the session's channel builder for one wired to stand-ins.

    Returns:
        (channel, monitor, progress) — what the session sent, what it pinged, and the loop
        progress the pinger read
    """
    channel, monitor = ScriptedChannel(), Monitor()
    progress = LoopProgress()

    def build(config, logger):
        dispatcher = OperatorAlertDispatcher(
            channel, TelegramAlertsConfig(enabled=True, min_send_interval_s=0.0), logger,
            session_label(config))
        dispatcher.start()
        pinger = LivenessPinger(
            PING_URL, FAIL_URL,
            LivenessPingConfig(enabled=True, interval_s=0.01, loop_stall_after_s=60.0),
            progress, logger, alert=dispatcher.send, http_get=monitor.get)
        return OperatorAlerts(dispatcher, pinger, progress)

    monkeypatch.setattr(autotrader_main_module, 'build_operator_alerts', build)
    return channel, monitor, progress


def _run():
    trader = AutotraderMain(load_autotrader_config(PROFILE))
    try:
        return trader.run()
    finally:
        remove_run_dir(trader._run_dir)


def test_a_session_says_it_started_and_how_it_ended(recorded):
    channel, monitor, progress = recorded

    result = _run()

    assert result.get_outcome() is RunOutcome.SUCCESS
    assert channel.titles() == ['✅ Session started', '✅ Session ended — SUCCESS (exit 0)']
    started, ended = channel.delivered
    assert 'mock replay' in started
    assert f'Ticks: {result.ticks_processed:,}' in ended.splitlines()
    # the speaker line names the session, so several bots can share one chat
    assert 'kraken_spot' in ended.splitlines()[-2]
    # the loop stamped the very progress the pinger vouches for — not a copy of its own
    assert progress.seconds_since_last_pass() is not None
    # and its last word to the monitor was an ordinary ping
    assert monitor.requests and monitor.requests[-1] == PING_URL
    assert FAIL_URL not in monitor.requests


def test_an_abort_is_one_message_and_a_fail_ping(recorded, monkeypatch):
    channel, monitor, _ = recorded

    def refuse(self):
        raise ValueError('no tick source configured')

    monkeypatch.setattr(AutotraderMain, '_validate_startup', refuse)

    result = _run()

    assert result.get_outcome() is RunOutcome.FAILED
    assert channel.titles() == ['⛔ STARTUP FAILED']
    assert 'no tick source configured' in channel.delivered[0]
    assert monitor.requests == [FAIL_URL]      # the loop never ran; the alarm comes at once


def test_a_crash_while_shutting_down_is_said_and_fails_the_ping(recorded, monkeypatch):
    """A crash in the report phase must not end the session in silence (#235)."""
    channel, monitor, _ = recorded

    def crash(self, result):
        raise OSError('No space left on device')

    monkeypatch.setattr(AutotraderMain, '_generate_reports', crash)

    with pytest.raises(OSError):
        _run()

    assert channel.titles()[-1] == '⛔ SESSION CRASHED while shutting down'
    assert 'No space left on device' in channel.delivered[-1]
    assert monitor.requests[-1] == FAIL_URL


@pytest.fixture
def refused_cold_start(monkeypatch):
    """A cold start that refuses the boot the way the adopter does: the line logged, then False."""
    def refuse(**kwargs):
        kwargs['logger'].error(REFUSAL)
        return ColdStartSetup(proceed=False, refusal=REFUSAL)

    monkeypatch.setattr(autotrader_main_module, 'setup_cold_start', refuse)


def test_a_refused_start_is_one_message_quoting_why_and_no_ping(recorded, refused_cold_start):
    """An unattended restart that refuses must not leave 'Session started' as the last word."""
    channel, monitor, _ = recorded

    result = _run()

    assert channel.titles() == ['⛔ STARTUP REFUSED']
    assert "adoption_mode='operator_confirm'" in channel.delivered[0]
    # graded by what it logged, as before; the loop never ran, so nothing vouches for it
    assert result.get_outcome() is RunOutcome.FINISHED_WITH_ERRORS
    assert monitor.requests == []


def test_a_refused_start_whose_shutdown_crashes_shuts_down_once(recorded, refused_cold_start,
                                                                monkeypatch):
    """The refusal runs the one guarded shutdown — a crash in it is not a second startup failure."""
    channel, monitor, _ = recorded
    shutdowns = []
    real_shutdown = AutotraderMain._shutdown

    def counted(self, ticks_processed, ticks_clipped):
        shutdowns.append(ticks_processed)
        return real_shutdown(self, ticks_processed, ticks_clipped)

    def crash(self, result):
        raise OSError('No space left on device')

    monkeypatch.setattr(AutotraderMain, '_shutdown', counted)
    monkeypatch.setattr(AutotraderMain, '_generate_reports', crash)

    with pytest.raises(OSError):
        _run()

    assert shutdowns == [0]
    assert channel.titles() == ['⛔ STARTUP REFUSED', '⛔ SESSION CRASHED while shutting down']
    assert monitor.requests == [FAIL_URL]
