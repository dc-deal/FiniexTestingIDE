"""
FiniexTestingIDE - Operator Alerts Configuration Types

`app_config.json::autotrader.operator_alerts` (#235): the session's two out-of-band channels to its
operator. Telegram carries what happened; the liveness ping proves the tick loop is still
passing, so that its ABSENCE can raise the alarm a dead process cannot send itself. Both are off
by default, and both read their secrets through the credentials cascade — the ping URL is itself
a secret, because whoever holds it can fake a liveness ping.

§28: every default here is mirrored with the identical value in app_config.json.
"""

from pathlib import Path
from typing import Dict

from pydantic import Field, field_validator

from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.strict_config_model import StrictConfigModel
from python.framework.types.connection_types import GiveUpAction


class TelegramAlertEndpoint(StrictConfigModel):
    """
    One Telegram bot and chat: the API's address and the credential that names both.

    A development bot writing to a test chat and the production bot writing to the operator are
    two endpoints, switched by `active` — so a test message never reaches the operator's phone
    and a production alert never lands in a test chat.

    Args:
        base_url: The Bot API's address
        credentials_file: Path below the credentials directory holding `bot_token` and `chat_id`
    """
    base_url: str = 'https://api.telegram.org'
    credentials_file: str = 'services/telegram_credentials.json'


class TelegramAlertsConfig(StrictConfigModel):
    """
    Operator alerts over Telegram.

    Args:
        enabled: Whether the session sends alerts at all
        active: Which registered endpoint the session uses
        endpoints: The registered endpoints by name
        min_send_interval_s: Shortest gap between two messages — Telegram allows about one
            message per second to a chat, and a burst of alerts reads worse than a short delay
        queue_capacity: Messages held while the channel is slow; when it is full the oldest
            alert below CRITICAL is dropped, and the drop is said
        all_clear_after_s: How long a symptom must stay away before its all-clear is sent — a
            breaker or a feed flapping around its threshold is one episode, not a message per
            crossing
        connection: Retry ladder for one message — its budget bounds how long one message may
            hold the queue, and how long the boot check waits for an unreachable Telegram
    """
    enabled: bool = False
    active: str = 'production'
    endpoints: Dict[str, TelegramAlertEndpoint] = Field(
        default_factory=lambda: {'production': TelegramAlertEndpoint()})
    min_send_interval_s: float = Field(default=3.0, ge=0.0)
    queue_capacity: int = Field(default=100, ge=1)
    all_clear_after_s: float = Field(default=300.0, ge=0.0)
    connection: ConnectionPolicy = Field(default_factory=lambda: ConnectionPolicy(
        initial_delay_s=2.0, max_delay_s=60.0, attempt_budget=5, request_timeout_s=10.0))

    @field_validator('connection')
    @classmethod
    def _gives_up_and_goes_on(cls, policy: ConnectionPolicy) -> ConnectionPolicy:
        """
        Refuse a ladder that never gives up, and a give-up rule that ends more than one message.

        A budget of 0 or less never runs out: the boot check would wait for as long as Telegram
        is unreachable — a supervised restart during a messenger outage would never start the
        session — and one undeliverable message would hold the queue. The broker ladder of a
        live session is refused for the same reason. `abort` raises where the ladder gives up —
        on the alert thread that ends the thread, and every later alert, an emergency stop
        included, would never be sent. An alert that could not be delivered is dropped and said;
        the session goes on.

        Args:
            policy: The configured ladder

        Returns:
            The policy, when its budget is finite and its give-up rule is `alert` or `degrade`
        """
        if policy.attempt_budget <= 0:
            raise ValueError(
                f'operator_alerts.telegram.connection.attempt_budget is '
                f'{policy.attempt_budget} — it must be at least 1. A budget that never runs out '
                f'holds the session\'s start for as long as Telegram is unreachable; for long '
                f'retries set a large finite budget')
        if policy.on_give_up is GiveUpAction.ABORT:
            raise ValueError(
                'operator_alerts.telegram.connection.on_give_up must be "alert" or "degrade" — '
                '"abort" would end the alert thread at the first message it gives up on')
        return policy

    def get_active_endpoint(self) -> TelegramAlertEndpoint:
        """
        The endpoint named by `active`.

        Returns:
            Its configuration
        """
        endpoint = self.endpoints.get(self.active)
        if endpoint is None:
            known = ', '.join(sorted(self.endpoints)) or '(none registered)'
            raise ValueError(
                f"operator_alerts.telegram.active is '{self.active}', which is not a registered "
                f'endpoint. Known endpoints: {known}')
        return endpoint


class LivenessPingConfig(StrictConfigModel):
    """
    The liveness ping — an outward sign that the tick loop is still passing.

    A monitoring service (healthchecks.io and its kin) expects a ping every period and raises
    the alarm when one is missing. The ping goes out only while the loop has passed recently,
    so a process that is alive but hung goes silent too.

    Args:
        enabled: Whether the session pings at all
        credentials_file: Path below the credentials directory holding `ping_url` and,
            optionally, `fail_url` (pinged once when the session ends in an emergency, so the
            alarm comes at once rather than after the monitor's grace period). Empty reads
            `services/liveness_<bot_id>.json` — one monitor check per bot, because two sessions
            pinging one check would each vouch for the other's hung loop
        interval_s: Time between two pings — also the retry cadence: a failed ping is not
            repeated, the next one is due anyway
        loop_stall_after_s: How long the tick loop may go without a pass before the pings stop —
            and, with the ping off and Telegram on, before the operator is told the loop stalled
        liveness_stamp_file: Optional file the pinger rewrites with the UTC time of each ping, for
            a local watcher; empty writes none
        request_timeout_s: Socket timeout for one ping
    """
    enabled: bool = False
    credentials_file: str = ''
    interval_s: float = Field(default=60.0, gt=0.0)
    loop_stall_after_s: float = Field(default=120.0, gt=0.0)
    liveness_stamp_file: str = ''
    request_timeout_s: float = Field(default=10.0, gt=0.0)

    @field_validator('liveness_stamp_file')
    @classmethod
    def _names_a_file(cls, value: str) -> str:
        """
        Refuse a stamp file path that names no file — a directory or the file system's root.

        Args:
            value: The configured path, '' for none

        Returns:
            The path, when it is empty or names a file
        """
        if value and (value.endswith(('/', '\\')) or Path(value).name in ('', '..')):
            raise ValueError(
                f"operator_alerts.liveness.liveness_stamp_file is '{value}', which names no "
                f'file — give the path of a file, e.g. runs/eth_bot.alive')
        return value


class OperatorAlertsConfig(StrictConfigModel):
    """
    Top-level model for `autotrader.operator_alerts`.

    Args:
        telegram: The alert channel
        liveness: The liveness ping
    """
    telegram: TelegramAlertsConfig = Field(default_factory=TelegramAlertsConfig)
    liveness: LivenessPingConfig = Field(default_factory=LivenessPingConfig)
