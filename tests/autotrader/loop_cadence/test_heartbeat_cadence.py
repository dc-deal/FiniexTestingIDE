"""
Loop Cadence — Heartbeat Re-Poll + Decision Ghost-Pass (#360)

Part A: the live heartbeat now re-polls active limit orders (the fill/cancel-confirm
query fires during idle, not only on a real tick).

Part C: the WorkerOrchestrator runs a decision ghost-pass on the heartbeat only for
logics that opt in via wants_heartbeat(), forwarding cached worker results (workers
are never recomputed) with tick=None.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.trading_env_types.order_types import (
    OrderType,
    OrderDirection,
    OpenOrderRequest,
)
from python.framework.types.worker_types import WorkerResult
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class TestHeartbeatRepoll:
    """Part A — the re-poll fires on the heartbeat, not only on a real tick."""

    def test_heartbeat_schedules_active_order_poll(self):
        """heartbeat() re-polls an active limit order (in_flight_query flips True)."""
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)

        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0,
        ))
        # broker_ref set, no poll yet (await does not trigger Phase-2 polling)
        mock.await_submit_confirmation(executor)
        active = executor._active_limit_orders
        assert len(active) == 1
        assert active[0].execution_state.in_flight_query is False

        # The heartbeat must now schedule the status poll (was on_tick-only).
        executor.heartbeat()
        assert active[0].execution_state.in_flight_query is True


class _StubDecisionLogic:
    """Minimal decision-logic surface used by WorkerOrchestrator.process_heartbeat."""

    def __init__(self, wants: bool):
        self._wants = wants
        self.performance_logger = None
        self.compute_calls = []  # (tick, worker_results) per call

    def wants_heartbeat(self) -> bool:
        return self._wants

    def compute(self, tick, worker_results: Dict[str, WorkerResult]) -> Optional[Decision]:
        self.compute_calls.append((tick, worker_results))
        return Decision(action=DecisionLogicAction.FLAT, outputs={})


def _orchestrator_with(decision_logic) -> WorkerOrchestrator:
    """Build a process_heartbeat-ready orchestrator without the heavy constructor."""
    orch = object.__new__(WorkerOrchestrator)
    orch.is_initialized = True
    orch.decision_logic = decision_logic
    orch._worker_results = {'rsi': WorkerResult(outputs={'value': 1.0})}
    return orch


class TestProcessHeartbeat:
    """Part C — the ghost-pass is opt-in and forwards cached worker results."""

    def test_no_opt_in_returns_none_and_skips_compute(self):
        """A logic that does not opt in is never run on the heartbeat."""
        dl = _StubDecisionLogic(wants=False)
        orch = _orchestrator_with(dl)
        assert orch.process_heartbeat() is None
        assert dl.compute_calls == []

    def test_opt_in_runs_compute_with_none_tick_and_cached_results(self):
        """An opt-in logic is run with tick=None and the cached worker results."""
        dl = _StubDecisionLogic(wants=True)
        orch = _orchestrator_with(dl)
        decision = orch.process_heartbeat()
        assert decision is not None
        assert len(dl.compute_calls) == 1
        tick_arg, results_arg = dl.compute_calls[0]
        assert tick_arg is None  # ghost-pass signal
        assert results_arg is orch._worker_results  # cached, not recomputed
