"""
Whether a failed start came from a passing cause (#598): a give-up after transient faults is tried
again by the supervisor (exit 75), a refusal or a terminal fault stays down (exit 2).
"""

import pytest
import requests

from python.framework.autotrader.startup.start_failure_classifier import is_passing_start_failure
from python.framework.exceptions.connection_errors import (
    ConnectionGaveUpError,
    TransientStartError,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.http_attempt import send_classified
from tests.shared.recording_logger import RecordingLogger


def _give_up_after(send) -> ConnectionGaveUpError:
    """The give-up a boot read raises when every attempt failed the given way."""
    ladder = ConnectionLadder('broker_boot_read',
                              ConnectionPolicy(attempt_budget=3, on_give_up=GiveUpAction.ABORT),
                              RecordingLogger())
    with pytest.raises(ConnectionGaveUpError) as raised:
        run_with_ladder(lambda: send_classified(send, '/0/private/Balance'), ladder,
                        wait=lambda delay: None)
    return raised.value


def _timeout():
    raise requests.exceptions.ReadTimeout('read timed out')


def _refused():
    response = requests.Response()
    response.status_code = 401
    return response


class TestAPassingCause:

    def test_a_give_up_after_timeouts_is_tried_again(self):
        assert is_passing_start_failure(_give_up_after(_timeout))

    def test_it_stays_passing_when_a_boot_step_wraps_it(self):
        try:
            try:
                raise _give_up_after(_timeout)
            except ConnectionGaveUpError:
                raise ConnectionError('No broker config available for kraken_spot ETHUSD')
        except ConnectionError as wrapped:
            assert is_passing_start_failure(wrapped)

    def test_a_boot_step_may_say_so_itself(self):
        assert is_passing_start_failure(TransientStartError('the venue did not answer'))


class TestNotAPassingCause:

    def test_a_refused_credential_stays_down(self):
        assert not is_passing_start_failure(_give_up_after(_refused))

    def test_the_venue_refusing_in_its_answer_stays_down(self):
        def venue_error():
            raise ConnectionError("Kraken API error: ['EAPI:Invalid key']")
        ladder = ConnectionLadder('broker_boot_read',
                                  ConnectionPolicy(attempt_budget=3, on_give_up=GiveUpAction.ABORT),
                                  RecordingLogger())
        with pytest.raises(ConnectionGaveUpError) as raised:
            run_with_ladder(venue_error, ladder, wait=lambda delay: None)
        assert not is_passing_start_failure(raised.value)

    def test_a_defect_stays_down(self):
        assert not is_passing_start_failure(KeyError('symbol'))
