"""
Loop Cadence — A Cadence Run Starts Its Timer Again (#461)

The reconciliation and the state persistence each have two gates: a tick count, checked after an
instant with ticks, and a time, which is a timer on the session's timeline. Whichever gate ran it,
a run starts the timer again from now, so the two never fire back to back; a gate that did not
open leaves the timer where it is.

The loop is exercised through `__new__`: the methods under test read a handful of collaborators,
and building a real tick loop would drag in a queue, workers and a decision logic without testing
any of them.
"""

from datetime import datetime, timezone
from typing import List, Tuple
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.types.live_types.reconciliation_types import ReconciliationResult
from python.framework.types.loop_types.timer_kind_types import TimerKind

_NOW = datetime(2026, 1, 25, 12, 0, 0, tzinfo=timezone.utc)
_NOW_MSC = round(_NOW.timestamp() * 1000)


class _ArmingIntake:
    """Records what the loop schedules on its timeline."""

    def __init__(self):
        self.armed: List[Tuple[TimerKind, int]] = []

    def arm(self, timer: TimerKind, due_msc: int) -> None:
        self.armed.append((timer, due_msc))


class _Reconciler:
    def __init__(self, due: bool):
        self._due = due
        self.runs = 0

    def is_due(self, tick_counter: int) -> bool:
        return self._due

    def reconcile(self, current_tick: int = 0) -> ReconciliationResult:
        self.runs += 1
        return ReconciliationResult()

    def get_min_interval_seconds(self) -> float:
        return 60.0


class _StateStore:
    def __init__(self, due: bool):
        self._due = due
        self.saves = 0

    def is_due(self, tick_counter: int) -> bool:
        return self._due

    def save(self, snapshot, tick_counter: int) -> None:
        self.saves += 1

    def get_save_interval_seconds(self) -> float:
        return 30.0


def _loop(reconciler=None, state_store=None) -> Tuple[AutotraderTickLoop, _ArmingIntake]:
    intake = _ArmingIntake()
    loop = AutotraderTickLoop.__new__(AutotraderTickLoop)
    loop._tick_source = MagicMock()
    loop._tick_source.get_idle_pass_time.return_value = _NOW
    loop._replay = False
    loop._intake = intake
    loop._reconciler = reconciler
    loop._state_store = state_store
    loop._executor = MagicMock()
    loop._decision_logic = MagicMock()
    loop._logger = MagicMock()
    return loop, intake


class TestTheReconcileTimer:

    def test_a_reconcile_the_tick_count_opened_starts_the_timer_again(self):
        reconciler = _Reconciler(due=True)
        loop, intake = _loop(reconciler=reconciler)

        loop._reconcile_if_due(100)

        assert reconciler.runs == 1
        assert intake.armed == [(TimerKind.RECONCILE, _NOW_MSC + 60_000)]

    def test_a_gate_that_did_not_open_leaves_the_timer_where_it_is(self):
        reconciler = _Reconciler(due=False)
        loop, intake = _loop(reconciler=reconciler)

        loop._reconcile_if_due(100)

        assert (reconciler.runs, intake.armed) == (0, [])


class TestThePersistenceTimer:

    def test_a_save_the_tick_count_opened_starts_the_timer_again(self):
        store = _StateStore(due=True)
        loop, intake = _loop(state_store=store)

        loop._persist_state_if_due(500)

        assert store.saves == 1
        assert intake.armed == [(TimerKind.PERSISTENCE, _NOW_MSC + 30_000)]

    def test_a_save_the_timer_brought_starts_it_again_too(self):
        store = _StateStore(due=False)
        loop, intake = _loop(state_store=store)

        loop._persist_state_now(7)

        assert store.saves == 1
        assert intake.armed == [(TimerKind.PERSISTENCE, _NOW_MSC + 30_000)]
