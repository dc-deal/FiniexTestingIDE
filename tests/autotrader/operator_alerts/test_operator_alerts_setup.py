"""
Operator Alerts Setup — what a profile switches on, and what is refused before the session runs.

An enabled channel that cannot work is refused at startup: a credential that is missing, a
committed placeholder, an empty field, a token the channel rejects, a ping URL nothing could
request. A channel that is only unreachable at boot is not — a messenger outage must not stop a
supervised restart. A test run never reads the operator's own credentials.
"""

import json
import threading
import time

import pytest

from python.framework.autotrader.operator_alerts import operator_alerts_setup as alerts_setup
from python.framework.autotrader.operator_alerts.operator_alerts_setup import (
    build_operator_alerts,
    build_telegram_channel,
    disabled_operator_alerts,
    read_liveness_urls,
)
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.exceptions.operator_alert_errors import OperatorAlertsConfigurationError
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.operator_alerts_config_types import (
    LivenessPingConfig,
    OperatorAlertsConfig,
    TelegramAlertEndpoint,
    TelegramAlertsConfig,
)
from python.framework.types.connection_types import GiveUpAction
from tests.autotrader.operator_alerts.operator_alert_doubles import ScriptedChannel
from tests.shared.recording_logger import RecordingLogger

# No wait between boot attempts, so a budget is exercised without spending its delays
QUICK_LADDER = ConnectionPolicy(initial_delay_s=0.0, max_delay_s=0.0, jitter=False,
                                attempt_budget=2, request_timeout_s=1.0)


def _config(telegram: bool = False) -> AutoTraderConfig:
    return AutoTraderConfig(
        profile_name='eth_daily', symbol='ETHUSD', broker_type='kraken_spot',
        operator_alerts=OperatorAlertsConfig(telegram=TelegramAlertsConfig(
            enabled=telegram, min_send_interval_s=0.0, connection=QUICK_LADDER)))


def _write(path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))


class TestOffByDefault:

    def test_a_profile_that_switches_nothing_on_gets_no_channel(self):
        alerts = build_operator_alerts(AutoTraderConfig(), RecordingLogger())
        assert alerts.describe() == 'telegram off · liveness ping off'
        alerts.send(OperatorAlertKind.SESSION_STARTED, OperatorAlertLevel.INFO, 'nobody hears')
        alerts.finish(emergency=True)

    def test_the_handle_before_startup_is_silent_too(self):
        assert disabled_operator_alerts().describe() == 'telegram off · liveness ping off'


class TestTheBootCheck:

    def test_an_accepted_channel_carries_the_alerts(self):
        channel = ScriptedChannel()
        alerts = build_operator_alerts(_config(telegram=True), RecordingLogger(), channel=channel)
        alerts.send(OperatorAlertKind.SESSION_STARTED, OperatorAlertLevel.INFO, 'Session started')
        alerts.finish(emergency=False)

        assert channel.verified == 1
        assert alerts.describe() == 'telegram on · liveness ping off'
        assert channel.titles() == ['✅ Session started']

    def test_a_refused_token_refuses_the_start(self):
        channel = ScriptedChannel(verify_error=ConnectionAttemptFailedError(
            'Telegram refused getMe (401): Unauthorized', terminal=True))
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            build_operator_alerts(_config(telegram=True), RecordingLogger(), channel=channel)
        assert 'refused the credential' in str(caught.value)

    def test_an_unreachable_messenger_does_not_stop_the_start(self):
        logger = RecordingLogger()
        channel = ScriptedChannel(verify_error=ConnectionAttemptFailedError(
            'Telegram unreachable (ConnectionError)'))
        alerts = build_operator_alerts(_config(telegram=True), logger, channel=channel)
        alerts.finish(emergency=False)

        assert channel.verified == 2                    # the whole budget, then on
        assert alerts.describe() == 'telegram on · liveness ping off'
        assert logger.levels().count('error') == 1      # said, so the run is graded with it

    def test_telegram_alone_still_hears_a_stalled_loop(self):
        """The ping off, Telegram on: the stall watch runs anyway and pings nothing."""
        channel = ScriptedChannel()
        config = AutoTraderConfig(
            profile_name='eth_daily', symbol='ETHUSD', broker_type='kraken_spot',
            operator_alerts=OperatorAlertsConfig(
                telegram=TelegramAlertsConfig(enabled=True, min_send_interval_s=0.0,
                                              connection=QUICK_LADDER),
                liveness=LivenessPingConfig(enabled=False, interval_s=0.01,
                                            loop_stall_after_s=0.05)))
        alerts = build_operator_alerts(config, RecordingLogger(), channel=channel)
        alerts.get_loop_progress().stamp()
        alerts.start_liveness()
        deadline = time.monotonic() + 5.0
        while '⛔ TICK LOOP STALLED' not in channel.titles() and time.monotonic() < deadline:
            time.sleep(0.01)
        alerts.loop_ended()
        alerts.finish(emergency=False)

        assert alerts.describe() == 'telegram on · liveness ping off'
        assert channel.titles() == ['⛔ TICK LOOP STALLED']


class TestTheCredentials:
    """Read through the shared cascade — in a scratch directory, never the operator's own."""

    NAME = 'services/telegram_credentials.json'

    @pytest.fixture(autouse=True)
    def _scratch_stands_in_for_the_workspace(self, monkeypatch):
        """Isolation keeps the REAL workspace out of reach; the scratch directory already does."""
        monkeypatch.setattr(alerts_setup, 'is_config_isolation_active', lambda: False)

    def test_a_test_run_never_reads_the_workspace_copy(self, tmp_path, monkeypatch):
        """Under isolation only the tracked copy answers — the operator's bot stays out of reach."""
        monkeypatch.setattr(alerts_setup, 'is_config_isolation_active', lambda: True)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / self.NAME,
               {'bot_token': '123:SECRET', 'chat_id': '42'})
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            build_telegram_channel(TelegramAlertsConfig(enabled=True))
        assert 'config isolation' in str(caught.value)

    def test_a_missing_file_names_where_it_belongs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            build_telegram_channel(TelegramAlertsConfig(enabled=True))
        assert f'user_configs/credentials/{self.NAME}' in str(caught.value)

    def test_the_committed_placeholder_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'configs' / 'credentials' / self.NAME,
               {'bot_token': 'x', 'chat_id': '1'})
        with pytest.raises(ValueError) as caught:
            build_telegram_channel(TelegramAlertsConfig(enabled=True))
        assert 'placeholder' in str(caught.value)

    def test_an_empty_field_is_named_and_no_value_is_quoted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / self.NAME,
               {'bot_token': '123:SECRET', 'chat_id': ''})
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            build_telegram_channel(TelegramAlertsConfig(enabled=True))
        assert 'chat_id' in str(caught.value)
        assert 'SECRET' not in str(caught.value)

    def test_the_active_endpoint_decides_which_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / 'services' / 'telegram_dev.json',
               {'bot_token': '123:SECRET', 'chat_id': '42'})
        channel = build_telegram_channel(TelegramAlertsConfig(
            enabled=True, active='dev', endpoints={
                'production': TelegramAlertEndpoint(),
                'dev': TelegramAlertEndpoint(credentials_file='services/telegram_dev.json')}))
        assert channel.describe() == 'telegram (dev)'

    def test_each_bot_reads_its_own_ping_url(self, tmp_path, monkeypatch):
        """One monitor check per bot — two bots on one check vouch for each other's hung loop."""
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / 'services' / 'liveness_ethbot.json',
               {'ping_url': 'https://hc-ping.example/u', 'fail_url': ''})
        assert read_liveness_urls(LivenessPingConfig(enabled=True), 'ethbot') == (
            'https://hc-ping.example/u', '')

    def test_a_named_file_wins_over_the_bot_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / 'services' / 'mine.json',
               {'ping_url': 'https://hc-ping.example/m'})
        settings = LivenessPingConfig(enabled=True, credentials_file='services/mine.json')
        assert read_liveness_urls(settings, 'ethbot')[0] == 'https://hc-ping.example/m'

    def test_without_a_bot_id_or_a_file_the_ping_is_refused(self):
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            read_liveness_urls(LivenessPingConfig(enabled=True), '')
        assert 'belongs to one bot' in str(caught.value)

    def test_a_ping_url_nothing_could_request_is_refused_at_boot(self, tmp_path, monkeypatch):
        """A double dot in the host passes the HTTP parser and fails only on the request."""
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / 'services' / 'liveness_ethbot.json',
               {'ping_url': 'https://hc-ping..example/SECRET-UUID'})
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            read_liveness_urls(LivenessPingConfig(enabled=True), 'ethbot')
        assert 'ping_url' in str(caught.value) and 'not a usable' in str(caught.value)
        assert 'SECRET-UUID' not in str(caught.value)

    def test_a_liveness_file_without_a_ping_url_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / 'user_configs' / 'credentials' / 'services' / 'liveness_ethbot.json',
               {'ping_url': ''})
        with pytest.raises(OperatorAlertsConfigurationError) as caught:
            read_liveness_urls(LivenessPingConfig(enabled=True), 'ethbot')
        assert 'ping_url' in str(caught.value)

    def test_a_refused_credential_leaves_no_thread_running(self, tmp_path, monkeypatch):
        """Every credential is read before a thread starts — a refusal orphans nothing."""
        monkeypatch.chdir(tmp_path)                      # no liveness file here
        config = AutoTraderConfig(
            profile_name='eth_daily', bot_id='ethbot', symbol='ETHUSD',
            broker_type='kraken_spot', operator_alerts=OperatorAlertsConfig(
                telegram=TelegramAlertsConfig(enabled=True, connection=QUICK_LADDER),
                liveness=LivenessPingConfig(enabled=True)))
        channel = ScriptedChannel()
        with pytest.raises(OperatorAlertsConfigurationError):
            build_operator_alerts(config, RecordingLogger(), channel=channel)
        assert channel.verified == 0
        assert 'operator-alerts' not in [thread.name for thread in threading.enumerate()]


class TestTheConfigurationRefusesWhatCannotWork:

    @pytest.mark.parametrize('fields', [
        {'queue_capacity': 0}, {'min_send_interval_s': -1.0}, {'all_clear_after_s': -1.0},
        {'connection': ConnectionPolicy(on_give_up=GiveUpAction.ABORT)},
        # a ladder that never gives up would hold the start for a whole messenger outage
        {'connection': ConnectionPolicy(attempt_budget=0)}])
    def test_the_telegram_block(self, fields):
        with pytest.raises(ValueError):
            TelegramAlertsConfig(**fields)

    @pytest.mark.parametrize('fields', [
        {'interval_s': 0.0}, {'loop_stall_after_s': -1.0}, {'request_timeout_s': 0.0},
        {'liveness_stamp_file': '.'}, {'liveness_stamp_file': '/'},
        {'liveness_stamp_file': 'runs/'}])
    def test_the_liveness_block(self, fields):
        with pytest.raises(ValueError):
            LivenessPingConfig(**fields)
