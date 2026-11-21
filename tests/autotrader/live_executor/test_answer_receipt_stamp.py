"""
LiveRequestProcessor — An Answer's Stamp Is Its Receipt (#461)

A venue's answer carries `ts_init`: when this process received it. It is read as the answer comes
back — after the venue call — and never when the request left, which would make it the send time
under a receipt's name. The same holds for a call that failed: the failure is stamped when it
surfaced. Each test gives the venue a round trip it cannot beat and checks the stamp lies past it.
"""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)

_ROUND_TRIP_S = 0.05


def _ms_since(moment: datetime, stamp: datetime) -> float:
    return (stamp - moment).total_seconds() * 1000


def _venue(fail: bool = False) -> SimpleNamespace:
    """A venue whose every call takes the round trip, answering or failing after it."""

    def call(payload):
        time.sleep(_ROUND_TRIP_S)
        if fail:
            raise ConnectionError('the venue went away')
        return {'txid': ['VENUE-1']}

    def answer(raw, broker_ref='VENUE-1', ts_init=None, **kwargs):
        return BrokerResponse(broker_ref='VENUE-1', status=BrokerOrderStatus.PENDING,
                              ts_init=ts_init)

    return SimpleNamespace(
        build_query_payload=lambda broker_ref: {}, do_request_query=call,
        parse_query_response=lambda raw, broker_ref, ts_init, market=None: answer(
            raw, ts_init=ts_init),
        build_cancel_payload=lambda broker_ref: {}, do_request_cancel=call,
        parse_cancel_response=lambda raw, broker_ref, ts_init: answer(raw, ts_init=ts_init),
        do_request_submit=call, parse_submit_response=answer,
        build_modify_payload=lambda **kwargs: {}, do_request_modify=call,
        parse_modify_response=lambda raw, original_broker_ref, ts_init: answer(
            raw, ts_init=ts_init),
        build_position_modify_payload=lambda **kwargs: {}, do_request_position_modify=call,
        parse_position_modify_response=lambda raw, position_id, ts_init: answer(
            raw, ts_init=ts_init),
    )


class TestAnAnswersStampIsItsReceipt:

    def test_a_status_answer_is_stamped_as_it_comes_back(self, request_processor):
        before = datetime.now(timezone.utc)

        answer = request_processor.query_order_sync('VENUE-1', _venue())

        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9

    def test_a_cancel_answer_is_stamped_as_it_comes_back(self, request_processor):
        before = datetime.now(timezone.utc)

        answer = request_processor.cancel_order_sync('VENUE-1', _venue())

        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9

    def test_a_failed_call_is_stamped_when_the_failure_surfaced(self, request_processor):
        before = datetime.now(timezone.utc)

        answer = request_processor.query_order_sync('VENUE-1', _venue(fail=True))

        assert answer.status is BrokerOrderStatus.UNRESOLVED
        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9

    def test_a_submit_answer_on_the_worker_is_stamped_as_it_comes_back(
            self, request_processor: LiveRequestProcessor):
        heard = []
        request_processor.set_arrival_listener(
            lambda: heard.append(datetime.now(timezone.utc)))
        job = SimpleNamespace(adapter=_venue(), payload={}, direction=None, order_type=None,
                              order_id='pos_btcusd_1', action=None)
        before = datetime.now(timezone.utc)

        request_processor._dispatch_submit_job(job)

        answer = request_processor._http_inbox.get_nowait().broker_response
        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9
        assert answer.ts_init <= heard[0], 'stamped no later than the loop is told'


_JOBS_ON_THE_WORKER = {
    '_dispatch_submit_job': lambda venue: SimpleNamespace(
        adapter=venue, payload={}, direction=None, order_type=None, order_id='pos_btcusd_1',
        action=None),
    '_dispatch_edit_job': lambda venue: SimpleNamespace(
        order_id='pos_btcusd_1', broker_ref='VENUE-1', symbol='BTCUSD', order_type=None,
        new_price=1.0, new_limit_price=None, new_stop_loss=None, new_take_profit=None,
        adapter=venue),
    '_dispatch_cancel_job': lambda venue: SimpleNamespace(
        order_id='pos_btcusd_1', broker_ref='VENUE-1', adapter=venue),
    '_dispatch_position_modify_job': lambda venue: SimpleNamespace(
        position_id='pos_btcusd_1', symbol='BTCUSD', new_stop_loss=1.0, new_take_profit=None,
        adapter=venue),
    '_dispatch_query_job': lambda venue: SimpleNamespace(
        order_id='pos_btcusd_1', broker_ref='VENUE-1', adapter=venue, market=None),
}


class TestEveryCallThatStampsAnAnswer:
    """
    Answered and failed, on the worker and synchronously. The order-resolve dispatcher is not
    among them: the answer it puts in the inbox carries no stamp.
    """

    @pytest.mark.parametrize('fail', [False, True])
    @pytest.mark.parametrize('dispatch', sorted(_JOBS_ON_THE_WORKER))
    def test_an_answer_on_the_worker_is_stamped_when_it_came_back(
            self, request_processor: LiveRequestProcessor, dispatch: str, fail: bool):
        job = _JOBS_ON_THE_WORKER[dispatch](_venue(fail=fail))
        before = datetime.now(timezone.utc)

        getattr(request_processor, dispatch)(job)

        answer = request_processor._http_inbox.get_nowait().broker_response
        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9

    @pytest.mark.parametrize('fail', [False, True])
    def test_a_modify_answer_is_stamped_when_it_came_back(
            self, request_processor: LiveRequestProcessor, fail: bool):
        before = datetime.now(timezone.utc)

        answer = request_processor.modify_order_sync(
            'VENUE-1', 'BTCUSD', None, 1.0, None, None, _venue(fail=fail))

        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9

    def test_a_failed_cancel_is_stamped_when_the_failure_surfaced(
            self, request_processor: LiveRequestProcessor):
        before = datetime.now(timezone.utc)

        answer = request_processor.cancel_order_sync('VENUE-1', _venue(fail=True))

        assert _ms_since(before, answer.ts_init) >= _ROUND_TRIP_S * 1000 * 0.9
