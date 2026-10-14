"""
FiniexTestingIDE - Operator Alerts Setup

Builds a session's out-of-band channels from its profile (#235) — the same shape as
`tick_source_setup.py` and `cold_start_setup.py`: one function that builds, wires and hands back.

An enabled channel that cannot work is refused HERE, at startup: a missing or placeholder
credential, an empty field, a token Telegram rejects. An unattended session whose alerts were
never going to arrive is the silence this feature exists to end. A Telegram that is only
unreachable at boot is not refused — the session would otherwise refuse to start whenever the
messenger has an outage, and a supervised restart would loop on it; that case is logged, and
the dispatcher keeps trying per message.

Under config isolation a credential is read from the tracked copy only — the placeholder the
guard refuses — so a test run never reaches the operator's bot, chat or monitor.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from python.configuration.credential_guard import assert_real_credential
from python.configuration.credentials_cascade import (
    resolve_credentials_file,
    tracked_credentials_path,
    workspace_credentials_path,
)
from python.framework.autotrader.operator_alerts.abstract_alert_channel import (
    AbstractAlertChannel,
)
from python.framework.autotrader.operator_alerts.liveness_pinger import LivenessPinger
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.autotrader.operator_alerts.operator_alert_dispatcher import (
    OperatorAlertDispatcher,
)
from python.framework.autotrader.operator_alerts.operator_alert_messages import session_label
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.operator_alerts.telegram_alert_channel import (
    TelegramAlertChannel,
)
from python.framework.exceptions.operator_alert_errors import OperatorAlertsConfigurationError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.operator_alerts_config_types import (
    LivenessPingConfig,
    TelegramAlertsConfig,
)
from python.framework.types.connection_types import ConnectionOutcome
from python.framework.utils.config_merge_utils import is_config_isolation_active
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder


def build_operator_alerts(
    config: AutoTraderConfig,
    logger: AbstractLogger,
    channel: Optional[AbstractAlertChannel] = None,
    http_get: Optional[Callable[..., Any]] = None,
) -> OperatorAlerts:
    """
    Build the session's alert dispatcher and liveness pinger, each only when switched on.

    The pinger is also what watches the loop for a stall, so with the ping off and Telegram on
    it is built without a URL: it pings nothing, and a stalled loop still reaches the operator.

    Args:
        config: The session's resolved profile
        logger: The session logger
        channel: A channel to use instead of the configured Telegram one — tests inject one
        http_get: The GET call for the liveness ping — tests inject one

    Returns:
        The handle the session keeps; with both channels off, every call on it is a no-op
    """
    settings = config.operator_alerts
    loop_progress = LoopProgress()

    # Every credential is read and checked before a thread starts — a refusal leaves nothing
    # running behind it
    alert_channel: Optional[AbstractAlertChannel] = None
    if settings.telegram.enabled:
        alert_channel = channel if channel is not None else build_telegram_channel(
            settings.telegram)
    liveness_urls: Optional[Tuple[str, str]] = None
    if settings.liveness.enabled:
        liveness_urls = read_liveness_urls(settings.liveness, config.bot_id)

    dispatcher: Optional[OperatorAlertDispatcher] = None
    if alert_channel is not None:
        verify_alert_channel(alert_channel, settings.telegram, logger)
        dispatcher = OperatorAlertDispatcher(
            alert_channel, settings.telegram, logger, session_label(config))
        dispatcher.start()

    pinger: Optional[LivenessPinger] = None
    if liveness_urls is not None or dispatcher is not None:
        ping_url, fail_url = liveness_urls if liveness_urls is not None else ('', '')
        pinger = LivenessPinger(
            ping_url, fail_url, settings.liveness, loop_progress, logger,
            alert=dispatcher.send if dispatcher is not None else _no_alert,
            http_get=http_get)

    return OperatorAlerts(dispatcher, pinger, loop_progress)


def disabled_operator_alerts() -> OperatorAlerts:
    """
    The handle of a session whose channels are off — what the session holds until they are built.

    Returns:
        An OperatorAlerts on which every call is a no-op
    """
    return OperatorAlerts(None, None, LoopProgress())


def build_telegram_channel(settings: TelegramAlertsConfig,
                           chat_required: bool = True) -> TelegramAlertChannel:
    """
    The Telegram channel of the active endpoint, with its credential read through the cascade.

    Args:
        settings: The Telegram block
        chat_required: False while a bot is being set up — the chat id is what the setup
            looks for, so only the token has to be there

    Returns:
        The channel
    """
    endpoint = settings.get_active_endpoint()
    required = ('bot_token', 'chat_id') if chat_required else ('bot_token',)
    credential = _read_credential(endpoint.credentials_file, 'Sending operator alerts', required)
    return TelegramAlertChannel(
        endpoint_name=settings.active, base_url=endpoint.base_url,
        bot_token=credential['bot_token'], chat_id=str(credential.get('chat_id') or ''),
        request_timeout_s=settings.connection.request_timeout_s)


def verify_alert_channel(channel: AbstractAlertChannel, settings: TelegramAlertsConfig,
                         logger: AbstractLogger) -> None:
    """
    Ask the channel whether it accepts its credential, before the session relies on it.

    A refusal ends the startup; an unreachable channel is logged and the session goes on.

    Args:
        channel: The channel
        settings: The Telegram block — its ladder bounds how long the boot waits
        logger: The session logger

    Returns:
        None
    """
    failures: List[BaseException] = []

    def attempt() -> bool:
        try:
            channel.verify()
        except Exception as error:   # noqa: BLE001 — classified by the ladder below
            failures.append(error)
            raise
        return True

    ladder = ConnectionLadder('operator_alerts', settings.connection, logger)
    if run_with_ladder(attempt, ladder):
        return
    if failures and ladder.classify(failures[-1]) is ConnectionOutcome.TERMINAL:
        raise OperatorAlertsConfigurationError(
            f'Operator alerts are switched on, but {channel.describe()} refused the credential: '
            f'{failures[-1]}. Fix the credentials file or switch operator_alerts.telegram off.')


def liveness_credentials_file(settings: LivenessPingConfig, bot_id: str) -> str:
    """
    Which credentials file holds this bot's ping URL.

    Args:
        settings: The liveness block
        bot_id: The bot's identity — names the default file, one monitor check per bot

    Returns:
        The configured file, or `services/liveness_<bot_id>.json`; '' when neither is known
    """
    if settings.credentials_file:
        return settings.credentials_file
    return f'services/liveness_{bot_id}.json' if bot_id else ''


def read_liveness_urls(settings: LivenessPingConfig, bot_id: str) -> Tuple[str, str]:
    """
    The monitor's ping URL and its optional fail URL, read through the cascade.

    Args:
        settings: The liveness block
        bot_id: The bot's identity — names the default credentials file

    Returns:
        (ping_url, fail_url) — fail_url '' when the file names none
    """
    credentials_file = liveness_credentials_file(settings, bot_id)
    if not credentials_file:
        raise OperatorAlertsConfigurationError(
            'The liveness ping is switched on, but the profile names neither a bot_id nor '
            'operator_alerts.liveness.credentials_file — a ping URL belongs to one bot.')
    purpose = 'Sending the liveness ping'
    credential = _read_credential(credentials_file, purpose, ('ping_url',))
    ping_url, fail_url = credential['ping_url'], str(credential.get('fail_url') or '')
    for field, url in (('ping_url', ping_url), ('fail_url', fail_url)):
        if url and not _is_requestable(url):
            raise OperatorAlertsConfigurationError(
                f'{purpose}: {field} in {liveness_credentials_path(credentials_file)} is not a '
                f'usable http(s) URL — check its scheme and host.')
    return ping_url, fail_url


def liveness_credentials_path(credentials_file: str) -> Path:
    """
    The file a liveness credential is read from, for a message — the one the cascade offers.

    Args:
        credentials_file: Path below the credentials directory

    Returns:
        The file read, or the workspace path when none exists
    """
    return _credentials_path(credentials_file) or workspace_credentials_path(credentials_file)


def _read_credential(credentials_file: str, purpose: str,
                     required: Tuple[str, ...]) -> Dict[str, Any]:
    """
    Read one credentials file through the cascade, refusing what cannot work.

    No value is ever quoted in a message — only which field is missing and where the file is.

    Args:
        credentials_file: Path below the credentials directory
        purpose: What the credential is for, named in a refusal
        required: The fields that must hold a value

    Returns:
        The file's content
    """
    path = _credentials_path(credentials_file)
    if path is None:
        isolated = (' Under config isolation only the tracked copy is read — a test injects its '
                    'channel.' if is_config_isolation_active() else '')
        raise OperatorAlertsConfigurationError(
            f'{purpose} is switched on, but {workspace_credentials_path(credentials_file)} does '
            f'not exist. Put the credential there, or switch it off.{isolated}')
    assert_real_credential(path, purpose)
    try:
        content = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise OperatorAlertsConfigurationError(
            f'Invalid JSON in {path}: {error}. Fix the syntax.') from None
    if not isinstance(content, dict):
        raise OperatorAlertsConfigurationError(f'{path} must hold a JSON object.')
    missing = [name for name in required if not str(content.get(name) or '').strip()]
    if missing:
        raise OperatorAlertsConfigurationError(
            f'{purpose}: {path} holds no value for {", ".join(missing)}.')
    return content


def _credentials_path(credentials_file: str) -> Optional[Path]:
    """
    The file an alert credential is read from — under config isolation the tracked copy only.

    A test run must never verify the operator's real bot, post into their chat or ping their
    monitor; the API's own tokens follow the same rule. The venue credentials do not: the live
    adapter gate runs under isolation and needs the real key.

    Args:
        credentials_file: Path below the credentials directory

    Returns:
        The file, or None when the cascade offers none
    """
    if not is_config_isolation_active():
        return resolve_credentials_file(credentials_file)
    tracked = tracked_credentials_path(credentials_file)
    return tracked if tracked.exists() else None


def _is_requestable(url: str) -> bool:
    """
    Whether the HTTP stack could ever request this URL — checked offline, before a thread relies
    on it. A double dot in the host passes `requests`' own parser and fails only on the request.

    Args:
        url: A monitor URL — never quoted in a message

    Returns:
        True for an http(s) URL with a well-formed host
    """
    try:
        requests.Request('GET', url).prepare()
    except requests.RequestException:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or '').rstrip('.')
    return parts.scheme in ('http', 'https') and bool(host) and '' not in host.split('.')


def _no_alert(kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
              detail: str) -> None:
    """
    The alert sender of a session whose Telegram channel is off.

    Args:
        kind: Unused
        level: Unused
        title: Unused
        detail: Unused

    Returns:
        None
    """
