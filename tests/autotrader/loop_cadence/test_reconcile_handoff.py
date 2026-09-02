"""
Loop Cadence — Handing Attributions to the Executor (#355 Phase 1)

The tick loop is the seam between the two halves of the client-key feature: the Reconciler
DETECTS (it is read-only by contract) and the executor WRITES (it owns the pending state).
That handoff is the only write path in the feature, and it is invisible to both the
reconciliation and the live-executor suites — each tests one side of a call the loop makes.

The loop is exercised through `__new__` rather than a full construction: the method under
test needs exactly two collaborators, and building a real tick loop would drag in a queue,
a tick source, workers and a decision logic without testing any of them.
"""

from datetime import datetime, timezone
from typing import List, Tuple

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    ReconciliationResult,
)
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

# A fixed stamp: the field is a record timestamp and nothing here reads it, so a test has
# no business asking the wall clock (§9).
_STAMP = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)



class StubReconciler:
    """Answers is_due/reconcile without touching a broker."""

    def __init__(self, result: ReconciliationResult, due: bool = True):
        self._result = result
        self._due = due
        self.reconcile_calls: List[int] = []

    def is_due(self, tick_counter: int) -> bool:
        return self._due

    def reconcile(self, current_tick: int = 0) -> ReconciliationResult:
        self.reconcile_calls.append(current_tick)
        return self._result


class SpyExecutor:
    """Records what the loop hands over."""

    def __init__(self):
        self.applied: List[List[Tuple[PendingOrder, BrokerOrder]]] = []

    def apply_order_attributions(self, attributions) -> None:
        self.applied.append(list(attributions))


def _pair() -> Tuple[PendingOrder, BrokerOrder]:
    """One (local pending, broker order) attribution pair."""
    pending = PendingOrder(
        pending_order_id='pos_btcusd_47',
        order_type=OrderType.LIMIT,
        broker_ref=None,
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
        lots=0.01,
    )
    broker_order = BrokerOrder(
        broker_ref='OQ7X2A-RESTING',
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT,
        lots=0.01,
        status=BrokerOrderStatus.PENDING,
        price=40000.0,
        client_order_id='p1641_47',
    )
    return pending, broker_order


def _loop(reconciler, executor) -> AutotraderTickLoop:
    """A tick loop with only the two collaborators this seam uses."""
    loop = AutotraderTickLoop.__new__(AutotraderTickLoop)
    loop._reconciler = reconciler
    loop._executor = executor
    return loop


class TestReconcileHandoff:
    """What the cycle matched reaches the executor — and nothing else does."""

    def test_attributions_are_handed_to_the_executor(self):
        pair = _pair()
        result = ReconciliationResult(timestamp=_STAMP, attributed_orders=[pair])
        spy = SpyExecutor()

        _loop(StubReconciler(result), spy)._reconcile_if_due(7)

        assert len(spy.applied) == 1
        assert spy.applied[0] == [pair]

    def test_a_clean_cycle_hands_over_nothing(self):
        # The executor must not be called with an empty list either — an attribution is an
        # event, and a call per cycle would make an empty one look like a repair.
        result = ReconciliationResult(timestamp=_STAMP)
        spy = SpyExecutor()

        _loop(StubReconciler(result), spy)._reconcile_if_due(7)

        assert spy.applied == []

    def test_nothing_happens_when_the_cadence_is_not_due(self):
        pair = _pair()
        result = ReconciliationResult(timestamp=_STAMP, attributed_orders=[pair])
        reconciler = StubReconciler(result, due=False)
        spy = SpyExecutor()

        _loop(reconciler, spy)._reconcile_if_due(7)

        assert reconciler.reconcile_calls == []
        assert spy.applied == []

    def test_a_session_without_a_reconciler_is_a_no_op(self):
        # Mock sessions auto-disable reconciliation, so this path runs on every mock run.
        spy = SpyExecutor()

        _loop(None, spy)._reconcile_if_due(7)

        assert spy.applied == []

    def test_the_tick_counter_reaches_the_reconciler(self):
        reconciler = StubReconciler(ReconciliationResult(timestamp=_STAMP))

        _loop(reconciler, SpyExecutor())._reconcile_if_due(4711)

        assert reconciler.reconcile_calls == [4711]
