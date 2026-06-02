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

from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy


class _Order:
    def __init__(self, order_id: str):
        self.pending_order_id = order_id


class _FakeApi:
    """Minimal trading_api: active orders + a cancel that can fail then succeed."""

    def __init__(self, fail_first=None):
        self._active = {'L1': _Order('L1'), 'L2': _Order('L2')}
        self._fail_first = set(fail_first or set())   # ids whose first cancel returns False
        self.cancel_calls = []

    def get_active_orders(self):
        return list(self._active.values())

    def cancel_limit_order(self, order_id: str) -> bool:
        self.cancel_calls.append(order_id)
        if order_id in self._fail_first:
            self._fail_first.discard(order_id)   # next attempt succeeds
            return False                          # in-flight → not scheduled, order stays resting
        self._active.pop(order_id, None)          # cancel took effect
        return True


def _bare_field_study(api) -> LiveFieldStudy:
    """LiveFieldStudy instance with only the attrs _cancel_resting needs."""
    fs = object.__new__(LiveFieldStudy)           # bypass full __init__
    fs.trading_api = api
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
