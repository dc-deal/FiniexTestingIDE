"""
Field Study — cancel orchestration regression (#332 Fund #3).

`LiveFieldStudy._cancel_resting` must cancel the orders that are ACTUALLY resting
(broker-aligned, via get_active_orders), retrying a cancel that returns False
(order still submit-in-flight) on the next call — never dropping it from tracking.

Guards the leak that made `multi_cancel` / `force_close` report "cancel not
confirmed / not flat": the old code cancelled a per-phase id list and cleared it
unconditionally, so the phase machine's CANCEL_ALL retries found an empty list
and never reached active_limit_count == 0.
"""

from unittest.mock import MagicMock

from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrderExecutionState,
)
from python.framework.types.trading_env_types.order_types import OrderType


class _Order:
    def __init__(self, order_id: str, broker_ref: str = 'REF',
                 order_type: OrderType = OrderType.LIMIT):
        self.pending_order_id = order_id
        self.broker_ref = broker_ref
        self.order_type = order_type
        self.execution_state = PendingOrderExecutionState()


class _FakeApi:
    """
    Minimal trading_api: active orders plus a cancel per resting WORLD.

    The two cancel paths are separate on purpose — that separation is what the live run of
    2026-09-08 proved matters. Each one only removes an order of its own type, exactly as
    the executor's `not_in_active_limits` / `not_in_active_stops` lookup does.
    """

    def __init__(self, fail_first=None, orders=None):
        self._active = orders if orders is not None else {
            'L1': _Order('L1'), 'L2': _Order('L2')}
        self._fail_first = set(fail_first or set())   # ids whose first cancel returns False
        self.cancel_calls = []
        self.stop_cancel_calls = []

    def get_active_orders(self):
        return list(self._active.values())

    def _cancel(self, order_id: str, world: frozenset) -> bool:
        order = self._active.get(order_id)
        if order is None or order.order_type not in world:
            return False                          # the other world's path does not find it
        if order_id in self._fail_first:
            self._fail_first.discard(order_id)   # next attempt succeeds
            return False                          # in-flight → not scheduled, order stays resting
        self._active.pop(order_id, None)          # cancel took effect
        return True

    def cancel_limit_order(self, order_id: str) -> bool:
        self.cancel_calls.append(order_id)
        return self._cancel(order_id, frozenset({OrderType.LIMIT}))

    def cancel_stop_order(self, order_id: str) -> bool:
        self.stop_cancel_calls.append(order_id)
        return self._cancel(order_id, frozenset({OrderType.STOP, OrderType.STOP_LIMIT}))


def _bare_field_study(api) -> LiveFieldStudy:
    """LiveFieldStudy instance with only the attrs _cancel_resting needs."""
    fs = object.__new__(LiveFieldStudy)           # bypass full __init__
    fs.trading_api = api
    fs.logger = MagicMock()                       # diagnostic [FS_CANCEL] logging → no-op here
    fs._phase_order_ids = ['L1', 'L2']
    return fs


class TestCancelResting:
    def test_failed_cancel_is_retried_not_dropped(self):
        # L2's first cancel returns False (still submit-in-flight).
        api = _FakeApi(fail_first={'L2'})
        fs = _bare_field_study(api)

        fs._cancel_resting()   # call 1: L1 (ok→gone) + L2 (False→stays resting)
        fs._cancel_resting()   # call 2 (machine retry): L2 still active → cancelled again

        assert api.cancel_calls.count('L2') == 2     # retried, NOT forgotten (old bug)
        assert api.cancel_calls.count('L1') == 1
        assert api.get_active_orders() == []          # converged to flat

    def test_cancels_all_resting_not_just_phase_ids(self):
        # An order NOT in _phase_order_ids (leaked from an earlier phase) must
        # still be cancelled — force_close is a broker-aligned safety-net.
        api = _FakeApi()
        api._active['L3'] = _Order('L3')              # leaked order, untracked
        fs = _bare_field_study(api)
        fs._phase_order_ids = ['L1']                  # only L1 tracked locally

        fs._cancel_resting()

        assert set(api.cancel_calls) == {'L1', 'L2', 'L3'}   # ALL resting cancelled
        assert api.get_active_orders() == []


class TestAStopIsCancelledThroughItsOwnPath:
    """
    The defect the live Field Study of 2026-09-08 found, in five minutes and for nine cents.

    `_cancel_resting` iterates BOTH resting worlds — `get_active_orders()` unions them — but
    it sent every one of them through `cancel_limit_order`. A stop is not in the limit list,
    so the call returned `not_in_active_limits`, the order stayed armed at Kraken, and the
    phase timed out after 30 s reporting "cancel not confirmed".

    The order was eventually cleared by the NEXT phase's cancel-all, which is why the session
    still ended flat — the failure was one phase's verdict, not a leaked order. That is worth
    knowing: a wrong result and a lost order look different, and only one of them was here.
    """

    def test_a_resting_stop_goes_to_the_stop_path(self):
        api = _FakeApi(orders={'S1': _Order('S1', order_type=OrderType.STOP)})
        fs = _bare_field_study(api)

        fs._cancel_resting()

        assert api.stop_cancel_calls == ['S1'], (
            'a stop must be cancelled through the path that owns its world')
        assert api.cancel_calls == [], 'the limit path must not be asked'
        assert api.get_active_orders() == [], 'and it must actually be gone'

    def test_a_stop_limit_goes_there_too(self):
        api = _FakeApi(orders={'SL1': _Order('SL1', order_type=OrderType.STOP_LIMIT)})
        fs = _bare_field_study(api)

        fs._cancel_resting()

        assert api.stop_cancel_calls == ['SL1']
        assert api.get_active_orders() == []

    def test_a_mixed_book_is_cleared_completely(self):
        """
        The state the multi-phase sequence actually reaches, and the one that broke.

        A stop left over from an earlier phase sits beside the limits, and a cancel-all that
        routes everything to one world clears some and arms the rest.
        """
        api = _FakeApi(orders={
            'L1': _Order('L1'),
            'S1': _Order('S1', order_type=OrderType.STOP),
            'L2': _Order('L2'),
        })
        fs = _bare_field_study(api)

        fs._cancel_resting()

        assert api.get_active_orders() == [], (
            f'a mixed book must be cleared completely, left: '
            f'{[o.pending_order_id for o in api.get_active_orders()]}')
        assert api.cancel_calls == ['L1', 'L2']
        assert api.stop_cancel_calls == ['S1']
