"""
Operator Alerts Check — the command the operator runs before leaving a session alone (#235).

It proves what is configured whether or not it is switched on, says in one line what was never
set up, and lists the chats that wrote to a new bot — so the chat id is found without a browser
URL that carries the token.
"""

from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.autotrader.operator_alerts import operator_alerts_check as check_module
from python.framework.autotrader.operator_alerts.operator_alerts_check import (
    print_bot_chats,
    print_operator_alerts_check,
    resolve_check_settings,
)
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.types.config_types.operator_alerts_config_types import OperatorAlertsConfig
from tests.autotrader.operator_alerts.operator_alert_doubles import ScriptedChannel


class _BotWithChats(ScriptedChannel):
    """A scripted channel that also answers which chats wrote to it."""

    def recent_chats(self):
        return [('42', 'Frank (private)')]


def _use(monkeypatch, channel) -> None:
    monkeypatch.setattr(check_module, 'build_telegram_channel',
                        lambda settings, chat_required=True: channel)


def test_the_check_sends_one_test_message(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)              # no liveness credentials here
    channel = ScriptedChannel()
    _use(monkeypatch, channel)

    assert print_operator_alerts_check(
        OperatorAlertsConfig(), 'channel check · host', 'ethbot') is True

    out = capsys.readouterr().out
    assert channel.titles() == ['✅ Channel check']
    assert 'switched off in the configuration' in out     # a channel is proven before it is on
    assert 'not set up' in out                             # one line, not a placeholder refusal


def test_a_refused_channel_fails_the_check(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _use(monkeypatch, ScriptedChannel(verify_error=ConnectionAttemptFailedError(
        'Telegram refused getMe (401): Unauthorized', terminal=True)))

    assert print_operator_alerts_check(OperatorAlertsConfig(), 'channel check · host', '') is False
    assert '❌ Telegram refused getMe (401)' in capsys.readouterr().out


def test_the_chats_that_wrote_to_the_bot_are_listed(monkeypatch, capsys):
    _use(monkeypatch, _BotWithChats())
    assert print_bot_chats(OperatorAlertsConfig()) is True
    assert '42  Frank (private)' in capsys.readouterr().out


def test_without_a_profile_the_check_names_the_installation(capsys):
    """The speaker is the `host` every run header states — never a container's hostname."""
    settings, speaker, bot_id = resolve_check_settings(None)
    # under test isolation the installation's id is the declared test id
    assert speaker == f'channel check · host {HostIdentityManager().get_host_id()}'
    assert bot_id == ''


def test_a_ping_is_per_bot_so_the_check_asks_for_a_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _use(monkeypatch, ScriptedChannel())
    print_operator_alerts_check(OperatorAlertsConfig(), 'channel check · host', '')
    assert 'the ping URL is per bot' in capsys.readouterr().out

