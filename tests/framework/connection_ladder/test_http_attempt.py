"""
One HTTP attempt, classified for the ladder (#598): a transport fault and a status worth retrying are
TRANSIENT, a refusal is TERMINAL — so a boot read retries a timeout instead of giving up at the
first one.
"""

import pytest
import requests

from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionGaveUpError,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.http_attempt import send_classified
from tests.shared.recording_logger import RecordingLogger


def _answer(status: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    return response


def _raises(error: Exception):
    def send():
        raise error
    return send


class TestTheClassification:

    @pytest.mark.parametrize('error', [requests.exceptions.ReadTimeout('read timed out'),
                                       requests.exceptions.ConnectionError('refused')])
    def test_a_transport_fault_is_transient(self, error):
        with pytest.raises(ConnectionAttemptFailedError) as raised:
            send_classified(_raises(error), '/0/private/Balance')
        assert raised.value.terminal is False
        assert type(error).__name__ in str(raised.value)

    @pytest.mark.parametrize('status, terminal', [(503, False), (500, False), (429, False),
                                                  (401, True), (400, True), (404, True)])
    def test_a_status_is_classified_by_the_shared_rule(self, status, terminal):
        with pytest.raises(ConnectionAttemptFailedError) as raised:
            send_classified(lambda: _answer(status), '/0/public/OHLC')
        assert raised.value.terminal is terminal
        assert f'HTTP {status} from /0/public/OHLC' in str(raised.value)

    def test_a_good_answer_is_handed_back(self):
        answer = _answer(200)
        assert send_classified(lambda: answer, '/0/public/AssetPairs') is answer


class TestTheLadderRetriesWhatWasClassified:

    def test_a_timeout_is_tried_as_often_as_the_budget_allows(self):
        attempts = []

        def read():
            attempts.append(1)
            return send_classified(_raises(requests.exceptions.ReadTimeout('slow')), '/0/private/Balance')

        ladder = ConnectionLadder('broker_balance',
                                  ConnectionPolicy(attempt_budget=3, on_give_up=GiveUpAction.ABORT),
                                  RecordingLogger())
        with pytest.raises(ConnectionGaveUpError):
            run_with_ladder(read, ladder, wait=lambda delay: None)
        assert len(attempts) == 3

    def test_a_refusal_ends_at_the_first_attempt(self):
        attempts = []

        def read():
            attempts.append(1)
            return send_classified(lambda: _answer(401), '/0/private/Balance')

        ladder = ConnectionLadder('broker_balance',
                                  ConnectionPolicy(attempt_budget=3, on_give_up=GiveUpAction.ABORT),
                                  RecordingLogger())
        with pytest.raises(ConnectionGaveUpError):
            run_with_ladder(read, ladder, wait=lambda delay: None)
        assert len(attempts) == 1
