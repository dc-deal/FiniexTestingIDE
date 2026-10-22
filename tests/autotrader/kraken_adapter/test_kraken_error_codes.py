"""
Kraken's "not now" inside an answer (#598). The venue reports most failures with HTTP 200 and an
error list, and three of its documented codes mean the request was not answered at all: the
matching engine offline for maintenance, a deadline that ran out, an internal error. Those reach
the ladder as transient — a read is retried, a write is asked about — while every other code stays
the venue's answer.
"""

import base64
import json

import pytest
import requests

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.kraken_error_codes import raise_for_kraken_errors
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import ConnectionOutcome
from python.framework.utils.connection_ladder import ConnectionLadder
from tests.shared.recording_logger import RecordingLogger

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'


def _outcome(error: Exception) -> ConnectionOutcome:
    """How the broker's REST ladder classifies one failure."""
    return ConnectionLadder('broker_rest', ConnectionPolicy(), RecordingLogger()).classify(error)


class TestWhatTheCodesMean:

    @pytest.mark.parametrize('code', ['EService:Unavailable', 'EService:Deadline elapsed',
                                      'EGeneral:Internal error', 'EGeneral:Internal error:5'])
    def test_the_venue_not_answering_is_transient(self, code):
        with pytest.raises(ConnectionAttemptFailedError) as raised:
            raise_for_kraken_errors([code])
        assert _outcome(raised.value) is ConnectionOutcome.TRANSIENT
        assert code in str(raised.value)

    @pytest.mark.parametrize('code', ['EOrder:Insufficient funds', 'EAPI:Invalid key',
                                      'EGeneral:Temporary lockout',
                                      'EService:Market in cancel_only mode'])
    def test_the_venue_answering_stays_terminal(self, code):
        with pytest.raises(ConnectionError) as raised:
            raise_for_kraken_errors([code])
        assert _outcome(raised.value) is ConnectionOutcome.TERMINAL

    def test_an_answer_beside_a_not_now_is_still_an_answer(self):
        with pytest.raises(ConnectionError):
            raise_for_kraken_errors(['EService:Unavailable', 'EOrder:Insufficient funds'])

    def test_an_answer_without_errors_passes(self):
        raise_for_kraken_errors([])

    def test_the_message_keeps_the_readers_label(self):
        with pytest.raises(ConnectionError, match=r"^Kraken OHLC API error: \['EAPI:Invalid key'\]$"):
            raise_for_kraken_errors(['EAPI:Invalid key'], 'Kraken OHLC API error')


class TestTheAdapterAppliesThem:
    """Every private call of the adapter goes through one method, and the rule is applied there."""

    @pytest.fixture
    def adapter(self) -> KrakenAdapter:
        with open(_KRAKEN_CONFIG) as f:
            adapter = KrakenAdapter(json.load(f))
        adapter._api_key = 'test_key'
        adapter._api_secret = base64.b64encode(b'test_secret').decode()
        adapter._api_base_url = 'https://api.example.invalid'
        return adapter

    @staticmethod
    def _answering(monkeypatch, body: dict) -> None:
        def post(url, headers=None, data=None, timeout=None):
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(body).encode()
            return response

        monkeypatch.setattr(requests, 'post', post)

    def test_a_deadline_on_an_order_is_not_a_refusal(self, adapter, monkeypatch):
        self._answering(monkeypatch, {'error': ['EService:Deadline elapsed'], 'result': {}})
        with pytest.raises(ConnectionAttemptFailedError) as raised:
            adapter._do_fetch_private('/0/private/AddOrder', {'pair': 'XBTUSD'})
        assert raised.value.terminal is False

    def test_insufficient_funds_stays_the_venues_answer(self, adapter, monkeypatch):
        self._answering(monkeypatch, {'error': ['EOrder:Insufficient funds'], 'result': {}})
        with pytest.raises(ConnectionError, match='EOrder:Insufficient funds'):
            adapter._do_fetch_private('/0/private/AddOrder', {'pair': 'XBTUSD'})
