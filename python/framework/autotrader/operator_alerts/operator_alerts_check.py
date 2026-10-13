"""
FiniexTestingIDE - Operator Alerts Check

What the operator runs on the live machine before leaving a session alone (#235): one test
message through the configured Telegram channel and one liveness ping, each reported as it went.
And, while a bot is being set up, which chats have written to it — the value `chat_id` takes —
so the token never has to travel through a browser URL.

Neither prints a secret: the token and the ping URL stay in their credentials files.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

import requests

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.credentials_cascade import workspace_credentials_path
from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    render_alert_text,
    session_label,
)
from python.framework.autotrader.operator_alerts.operator_alerts_setup import (
    build_telegram_channel,
    liveness_credentials_file,
    read_liveness_urls,
)
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlert,
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.operator_alerts_config_types import OperatorAlertsConfig

# What a check can fail on without being a defect: a configuration that cannot work, a guard
# refusing a placeholder, a channel or monitor that answered no.
_CHECK_FAILURES = (ValueError, ConnectionAttemptFailedError)


def resolve_check_settings(
        profile_path: Optional[str]) -> Tuple[OperatorAlertsConfig, str, str]:
    """
    The operator alerts block to check, who the test message comes from, and which bot.

    Args:
        profile_path: An AutoTrader profile — its sessions' configuration is checked; None
            checks the app_config defaults with their user_configs override

    Returns:
        The block, the speaker line (the profile, or the installation's id — the `host` every
        run header states), and the bot id ('' without a profile)
    """
    if profile_path:
        config = load_autotrader_config(profile_path)
        return (config.operator_alerts, f'channel check · {session_label(config)}',
                config.bot_id)
    defaults = AppConfigManager().get_autotrader_defaults() or {}
    settings = OperatorAlertsConfig(**defaults.get('operator_alerts', {}))
    return settings, f'channel check · host {HostIdentityManager().get_host_id()}', ''


def print_operator_alerts_check(settings: OperatorAlertsConfig, speaker: str,
                                bot_id: str) -> bool:
    """
    Send one test message and one liveness ping, and print how each went.

    Checks what is configured whether or not it is switched on, so a channel can be proven
    before a profile relies on it.

    Args:
        settings: The operator alerts block the session would run with
        speaker: Who the test message says it comes from
        bot_id: The bot whose ping URL to check — '' when no profile names one

    Returns:
        True when every configured channel answered
    """
    telegram = settings.telegram
    state = 'switched on' if telegram.enabled else 'switched off in the configuration'
    print(f'Telegram — endpoint {telegram.active!r}, {state}')
    telegram_ok = True
    try:
        channel = build_telegram_channel(telegram)
        channel.verify()
        channel.send(render_alert_text(OperatorAlert(
            kind=OperatorAlertKind.CHECK_MESSAGE, level=OperatorAlertLevel.INFO,
            title='Channel check',
            detail='If you read this, the operator alerts of this machine reach you.',
            raised_at=datetime.now(timezone.utc)), speaker))
        print(f'  ✅ test message sent through {channel.describe()} — look at the chat')
    except _CHECK_FAILURES as error:
        telegram_ok = False
        print(f'  ❌ {error}')

    liveness = settings.liveness
    state = 'switched on' if liveness.enabled else 'switched off in the configuration'
    print(f'Liveness ping — {state}')
    credentials_file = liveness_credentials_file(liveness, bot_id)
    if not credentials_file:
        print("  ℹ️  the ping URL is per bot — run the check with --profile <the bot's profile>")
        return telegram_ok
    workspace_file = workspace_credentials_path(credentials_file)
    if not liveness.enabled and not workspace_file.exists():
        # Off and never set up — one line, not the credential guard's refusal of a placeholder
        print(f'  ℹ️  not set up — no {workspace_file}')
        return telegram_ok
    liveness_ok = True
    try:
        ping_url, fail_url = read_liveness_urls(liveness, bot_id)
        from_monitor = _ping(ping_url, liveness.request_timeout_s)
        liveness_ok = from_monitor is None
        print('  ✅ ping accepted by the monitor' if liveness_ok
              else f'  ❌ the monitor answered {from_monitor}')
        if fail_url:
            print("  ℹ️  fail URL configured — not exercised here: it would raise the monitor's "
                  "alarm")
        else:
            print('  ℹ️  no fail URL — an emergency end then reaches the monitor only as silence')
    except _CHECK_FAILURES as error:
        liveness_ok = not liveness.enabled
        mark = '❌' if liveness.enabled else 'ℹ️ '
        print(f'  {mark} {error}')
    return telegram_ok and liveness_ok


def print_bot_chats(settings: OperatorAlertsConfig) -> bool:
    """
    Print the chats that have written to the configured bot — the value `chat_id` takes.

    Args:
        settings: The operator alerts block

    Returns:
        True when Telegram answered
    """
    try:
        chats = build_telegram_channel(settings.telegram, chat_required=False).recent_chats()
    except _CHECK_FAILURES as error:
        print(f'❌ {error}')
        return False
    if not chats:
        print('No chat has written to the bot yet. Send it /start in Telegram, then run this '
              'again — Telegram keeps an update for about a day.')
        return True
    print('Chats that wrote to the bot — put the id into chat_id:')
    for chat_id, name in chats:
        print(f'  {chat_id}  {name}')
    return True


def _ping(url: str, timeout_s: float) -> Optional[str]:
    """
    One GET to the monitor.

    Args:
        url: The ping URL — never printed
        timeout_s: Socket timeout

    Returns:
        None when the monitor accepted it, else a short description of its answer
    """
    try:
        response = requests.get(url, timeout=timeout_s)
    except requests.RequestException as error:
        return type(error).__name__
    return None if 200 <= response.status_code < 300 else f'HTTP {response.status_code}'
