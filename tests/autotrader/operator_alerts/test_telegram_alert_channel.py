"""
Telegram Alert Channel — one message, once, classified, and never a token in a message (#235).

The channel's whole contract is the answer it gives the dispatcher: delivered, try again (and
how long to wait), or stop. And because the bot token sits in the request URL while a `requests`
exception quotes its URL, every failure must leave without the original attached.
"""

from types import SimpleNamespace

import pytest
import requests

from python.framework.autotrader.operator_alerts.telegram_alert_channel import (
    MAX_MESSAGE_LENGTH,
    TelegramAlertChannel,
)
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.exceptions.operator_alert_errors import AlertChannelRateLimitedError

TOKEN = '123456:SECRET-TOKEN'


def _answer(status: int, body: dict) -> SimpleNamespace:
    """A response stand-in with a status and a JSON body."""
    return SimpleNamespace(status_code=status, json=lambda: body)


def _channel(post=None, get=None) -> TelegramAlertChannel:
    return TelegramAlertChannel(
        endpoint_name='production', base_url='https://api.telegram.org/', bot_token=TOKEN,
        chat_id='42', request_timeout_s=7.0, http_post=post, http_get=get)


class TestTheMessage:

    def test_one_post_to_send_message_with_plain_text(self):
        calls = []

        def post(url, json, timeout):
            calls.append((url, json, timeout))
            return _answer(200, {'ok': True})

        _channel(post=post).send('⛔ STARTUP FAILED')

        url, payload, timeout = calls[0]
        assert url == f'https://api.telegram.org/bot{TOKEN}/sendMessage'
        assert payload == {'chat_id': '42', 'text': '⛔ STARTUP FAILED',
                           'disable_web_page_preview': True}
        assert 'parse_mode' not in payload
        assert timeout == 7.0

    def test_a_long_message_is_cut_to_the_limit_and_says_so(self):
        sent = []
        _channel(post=lambda url, json, timeout: (sent.append(json['text']),
                                                  _answer(200, {'ok': True}))[1]
                 ).send('x' * (MAX_MESSAGE_LENGTH + 500))
        assert len(sent[0]) == MAX_MESSAGE_LENGTH
        assert sent[0].endswith('…')

    def test_verify_proves_the_token_and_the_chat(self):
        """getMe alone proves only the token — a blocked bot or a wrong chat would pass it."""
        calls = []

        def get(url, timeout):
            calls.append(url)
            return _answer(200, {'ok': True})

        def post(url, json, timeout):
            calls.append((url, json))
            return _answer(200, {'ok': True})

        _channel(post=post, get=get).verify()
        assert calls == [
            f'https://api.telegram.org/bot{TOKEN}/getMe',
            (f'https://api.telegram.org/bot{TOKEN}/sendChatAction',
             {'chat_id': '42', 'action': 'typing'})]

    def test_a_chat_the_bot_may_not_write_into_refuses(self):
        channel = _channel(
            get=lambda url, timeout: _answer(200, {'ok': True}),
            post=lambda url, json, timeout: _answer(
                403, {'ok': False, 'description': 'Forbidden: bot was blocked by the user'}))
        with pytest.raises(ConnectionAttemptFailedError) as caught:
            channel.verify()
        assert caught.value.terminal
        assert 'blocked by the user' in str(caught.value)

    def test_the_description_names_no_secret(self):
        assert TOKEN not in _channel().describe()
        assert _channel().describe() == 'telegram (production)'

    def test_the_chats_that_wrote_to_the_bot_are_listed_once_each(self):
        """The setup aid: after `/start`, the chat id — without a URL carrying the token."""
        updates = {'ok': True, 'result': [
            {'message': {'chat': {'id': 42, 'type': 'private', 'first_name': 'Frank'}}},
            {'message': {'chat': {'id': 42, 'type': 'private', 'first_name': 'Frank'}}},
            {'my_chat_member': {'chat': {'id': -1001, 'type': 'group', 'title': 'Finiex'}}},
            {'edited_message': {'chat': {'id': 7}}},
        ]}
        channel = _channel(get=lambda url, timeout: _answer(200, updates))
        assert channel.recent_chats() == [('42', 'Frank (private)'), ('-1001', 'Finiex (group)')]


class TestTheAnswerIsClassified:

    @pytest.mark.parametrize('status, terminal', [(401, True), (400, True), (403, True),
                                                  (500, False), (502, False)])
    def test_a_refusal_is_terminal_an_outage_is_not(self, status, terminal):
        channel = _channel(post=lambda url, json, timeout: _answer(
            status, {'ok': False, 'description': 'Unauthorized'}))
        with pytest.raises(ConnectionAttemptFailedError) as caught:
            channel.send('x')
        assert caught.value.terminal is terminal
        assert str(status) in str(caught.value)

    def test_a_200_without_a_bot_api_answer_is_not_delivered(self):
        """A wrong base_url answering a 200 page must not count every alert as delivered."""
        channel = _channel(post=lambda url, json, timeout: SimpleNamespace(
            status_code=200, json=lambda: (_ for _ in ()).throw(ValueError('not json'))))
        with pytest.raises(ConnectionAttemptFailedError) as caught:
            channel.send('x')
        assert caught.value.terminal
        assert 'base_url' in str(caught.value)

    def test_ok_false_beside_a_200_is_a_refusal(self):
        channel = _channel(post=lambda url, json, timeout: _answer(
            200, {'ok': False, 'description': 'Bad Request: chat not found'}))
        with pytest.raises(ConnectionAttemptFailedError) as caught:
            channel.send('x')
        assert caught.value.terminal
        assert 'chat not found' in str(caught.value)

    def test_429_carries_the_wait_telegram_asked_for(self):
        channel = _channel(post=lambda url, json, timeout: _answer(
            429, {'ok': False, 'description': 'Too Many Requests: retry after 9',
                  'parameters': {'retry_after': 9}}))
        with pytest.raises(AlertChannelRateLimitedError) as caught:
            channel.send('x')
        assert caught.value.retry_after_s == 9.0
        assert caught.value.terminal is False


class TestNoTokenLeaves:

    def test_a_transport_failure_names_its_kind_and_drops_the_original(self):
        def post(url, json, timeout):
            raise requests.ConnectionError(f'Max retries exceeded with url: {url}')

        with pytest.raises(ConnectionAttemptFailedError) as caught:
            _channel(post=post).send('x')
        assert TOKEN not in str(caught.value)
        assert 'ConnectionError' in str(caught.value)
        assert caught.value.terminal is False
        # the original — which quotes the URL, token included — is not chained
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__
