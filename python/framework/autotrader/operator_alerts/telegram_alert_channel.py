"""
FiniexTestingIDE - Telegram Alert Channel

Operator alerts through the Telegram Bot API (#235): `sendMessage` to one chat, as plain text.
Plain on purpose — MarkdownV2 requires escaping eighteen characters, and an error message quoting
a symbol or a path would fail to send exactly when it matters.

The bot token sits in the request URL, and a `requests` exception quotes its URL. So no exception
leaves this unit with the original attached: every failure is re-raised naming only its kind, the
status and Telegram's own description — never the token (§29).
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from python.framework.autotrader.operator_alerts.abstract_alert_channel import (
    AbstractAlertChannel,
)
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.exceptions.operator_alert_errors import AlertChannelRateLimitedError
from python.framework.utils.connection_ladder import is_terminal_status

# Telegram's limit for one message's text.
MAX_MESSAGE_LENGTH = 4096
_TRUNCATION_MARK = '\n…'


class TelegramAlertChannel(AbstractAlertChannel):
    """
    One bot writing to one chat.

    Args:
        endpoint_name: The configured endpoint's name, for log lines
        base_url: The Bot API's address
        bot_token: The bot's token — a secret
        chat_id: The chat the alerts go to
        request_timeout_s: Socket timeout for one request
        http_post: The POST call — `requests.post`, injected by tests
        http_get: The GET call — `requests.get`, injected by tests
    """

    def __init__(
        self,
        endpoint_name: str,
        base_url: str,
        bot_token: str,
        chat_id: str,
        request_timeout_s: float,
        http_post: Optional[Callable[..., Any]] = None,
        http_get: Optional[Callable[..., Any]] = None,
    ):
        self._endpoint_name = endpoint_name
        self._base_url = base_url.rstrip('/')
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._request_timeout_s = request_timeout_s
        self._http_post = http_post if http_post is not None else requests.post
        self._http_get = http_get if http_get is not None else requests.get

    def verify(self) -> None:
        """
        Ask Telegram whether the bot token is accepted AND the bot may write into the chat.

        `getMe` alone proves only the token: a wrong chat id, a chat that never sent /start, a
        bot blocked by its user or removed from the group would pass it and refuse the first
        alert instead. `sendChatAction` is the cheapest call that needs the chat — the chat shows
        "typing" for a moment, and nothing is posted.

        Returns:
            None
        """
        try:
            response = self._http_get(
                self._method_url('getMe'), timeout=self._request_timeout_s)
        except requests.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'Telegram unreachable ({type(error).__name__})') from None
        self._raise_for_answer(response, 'getMe')
        try:
            response = self._http_post(
                self._method_url('sendChatAction'),
                json={'chat_id': self._chat_id, 'action': 'typing'},
                timeout=self._request_timeout_s)
        except requests.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'Telegram unreachable ({type(error).__name__})') from None
        self._raise_for_answer(response, 'sendChatAction')

    def send(self, text: str) -> None:
        """
        Post one message to the chat.

        Args:
            text: The message; cut to Telegram's limit when longer

        Returns:
            None
        """
        try:
            response = self._http_post(
                self._method_url('sendMessage'),
                json={'chat_id': self._chat_id, 'text': _fit(text),
                      'disable_web_page_preview': True},
                timeout=self._request_timeout_s)
        except requests.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'Telegram unreachable ({type(error).__name__})') from None
        self._raise_for_answer(response, 'sendMessage')

    def describe(self) -> str:
        """
        Name the channel for a log line.

        Returns:
            'telegram (<endpoint>)'
        """
        return f'telegram ({self._endpoint_name})'

    def recent_chats(self) -> List[Tuple[str, str]]:
        """
        The chats that have recently written to the bot — the values `chat_id` can take.

        A bot can only write into a chat that has addressed it first, so after `/start` the
        operator's chat appears here (`getUpdates` keeps an update for about a day). A setup
        aid, not part of sending: it spares the operator a browser URL carrying the token.

        Returns:
            (chat id, a readable name) per chat, in the order first seen
        """
        try:
            response = self._http_get(
                self._method_url('getUpdates'), timeout=self._request_timeout_s)
        except requests.RequestException as error:
            raise ConnectionAttemptFailedError(
                f'Telegram unreachable ({type(error).__name__})') from None
        self._raise_for_answer(response, 'getUpdates')
        chats: Dict[str, str] = {}
        for update in _json_or_empty(response).get('result') or []:
            message = (update.get('message') or update.get('channel_post')
                       or update.get('my_chat_member') or {})
            chat = message.get('chat') or {}
            if 'id' not in chat:
                continue
            name = (chat.get('title') or chat.get('username')
                    or ' '.join(filter(None, (chat.get('first_name'), chat.get('last_name')))))
            chats.setdefault(str(chat['id']), f"{name or 'unnamed'} ({chat.get('type', '?')})")
        return list(chats.items())

    def _method_url(self, method: str) -> str:
        """
        The URL of one Bot API method.

        Args:
            method: The method name, e.g. 'sendMessage'

        Returns:
            The URL — it carries the token, so it is never logged
        """
        return f'{self._base_url}/bot{self._bot_token}/{method}'

    def _raise_for_answer(self, response: Any, method: str) -> None:
        """
        Turn Telegram's answer into success or a classified failure.

        Args:
            response: The HTTP response
            method: The method called, for the message

        Returns:
            None when Telegram accepted the call
        """
        body = _json_or_empty(response)
        status = response.status_code
        # Only an answer that says `ok: true` counts — a 200 without it is no Bot API answer
        # at all (a wrong base_url, a proxy page), and reading it as delivered fails open.
        if status == 200 and body.get('ok') is True:
            return
        description = str(body.get('description') or f'HTTP {status}')
        if status == 200 and 'ok' not in body:
            raise ConnectionAttemptFailedError(
                f'Telegram {method}: the answer is not a Bot API answer — check base_url',
                terminal=True)
        if status == 429:
            retry_after = (body.get('parameters') or {}).get('retry_after') or 0
            raise AlertChannelRateLimitedError(
                f'Telegram asked to slow down ({description})', float(retry_after))
        if status == 200:
            # `ok: false` beside a 200 is a refusal of the call itself, not of the transport
            raise ConnectionAttemptFailedError(
                f'Telegram refused {method}: {description}', terminal=True)
        raise ConnectionAttemptFailedError(
            f'Telegram refused {method} ({status}): {description}',
            terminal=is_terminal_status(status))


def _fit(text: str) -> str:
    """
    Cut a message to Telegram's length limit, marking the cut.

    Args:
        text: The message

    Returns:
        The message unchanged when it fits, else its head and a mark
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return text
    return text[:MAX_MESSAGE_LENGTH - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK


def _json_or_empty(response: Any) -> dict:
    """
    The answer's JSON object, or an empty one when the body is not JSON.

    Args:
        response: The HTTP response

    Returns:
        The parsed object, {} when there is none
    """
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
