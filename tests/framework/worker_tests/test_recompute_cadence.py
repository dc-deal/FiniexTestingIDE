"""
Worker Recompute Cadence Tests.

Verifies the consumer side of the ON_BAR_CLOSE feature:
- cadence resolution: config 'recompute' overrides the class default,
- preflight validation of the reserved 'recompute' key,
- orchestrator routing: ON_BAR_CLOSE recomputes only when a required timeframe
  closes (cached in between, seeded once at cold start); PER_TICK every tick,
- determinism: a PER_TICK and an ON_BAR_CLOSE Bollinger fed identical inputs
  produce byte-identical results on the bar-close grid (the #368 discipline).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.worker_types import RecomputeCadence, WorkerResult
from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


# =============================================================================
# HELPERS
# =============================================================================

def _bollinger(params: Dict) -> BollingerWorker:
    """Build a Bollinger worker with the given config (mock logger)."""
    return BollingerWorker(name='bb', parameters=params, logger=MagicMock())


def _tick(ts: datetime, bid: float, symbol: str = 'EURUSD') -> TickData:
    return TickData(timestamp=ts, symbol=symbol, bid=bid, ask=bid + 0.0002, volume=0.1)


def _one_minute_ticks(count: int = 30) -> List[TickData]:
    """count one-minute ticks from a 10:00 UTC boundary (M5 closes every 5 ticks)."""
    start = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return [_tick(start + timedelta(minutes=i), bid=1.1000 + i * 0.0005) for i in range(count)]


class _StubDecisionLogic:
    """Minimal decision-logic surface used by WorkerOrchestrator.process_tick."""

    def __init__(self):
        self.performance_logger = None

    def compute_tick(self, tick, worker_results: Dict[str, WorkerResult]) -> Decision:
        return Decision(action=DecisionLogicAction.FLAT, outputs={})


def _orchestrator(worker: BollingerWorker) -> WorkerOrchestrator:
    """Build a process_tick-ready orchestrator without the heavy constructor."""
    orch = object.__new__(WorkerOrchestrator)
    orch.is_initialized = True
    orch.workers = {worker.name: worker}
    orch._worker_results = {}
    orch.logger = MagicMock()
    orch.parallel_workers = False
    orch._coordination_stats = SimpleNamespace(ticks_processed=0)
    orch.decision_logic = _StubDecisionLogic()
    orch.tick_logger = SimpleNamespace(log_tick_data=lambda **kw: None)
    return orch


def _drive(ticks: List[TickData]) -> List[Tuple[bool, WorkerResult, WorkerResult]]:
    """
    Feed ticks through one shared bar controller into a PER_TICK and an
    ON_BAR_CLOSE Bollinger. Both see identical bars / render state per tick.

    Returns one record per tick: (m5_closed, per_tick_result, bar_close_result).
    """
    controller = BarRenderingController(logger=MagicMock())
    w_per_tick = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0})
    w_bar_close = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0, 'recompute': 'bar_close'})
    controller.register_workers([w_per_tick])  # drives M5 rendering

    orch_pt = _orchestrator(w_per_tick)
    orch_bc = _orchestrator(w_bar_close)

    records: List[Tuple[bool, WorkerResult, WorkerResult]] = []
    for tick in ticks:
        current_bars = controller.process_tick(tick)
        bar_history = controller.get_all_bar_history(tick.symbol)
        state = controller.consume_bar_render_state()  # consumed ONCE, shared to both
        m5_closed = 'M5' in state.closed_timeframes

        orch_pt.process_tick(tick, current_bars, bar_history, state)
        orch_bc.process_tick(tick, current_bars, bar_history, state)

        records.append((
            m5_closed,
            orch_pt.get_worker_result('bb'),
            orch_bc.get_worker_result('bb'),
        ))
    return records


# =============================================================================
# TESTS — cadence resolution
# =============================================================================

class TestCadenceResolution:
    """config 'recompute' overrides the class default."""

    def test_default_is_per_tick(self):
        worker = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0})
        assert worker.get_recompute_cadence() == RecomputeCadence.PER_TICK

    def test_class_default_is_per_tick(self):
        assert BollingerWorker.get_default_recompute_cadence() == RecomputeCadence.PER_TICK

    def test_config_bar_close_overrides(self):
        worker = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0, 'recompute': 'bar_close'})
        assert worker.get_recompute_cadence() == RecomputeCadence.ON_BAR_CLOSE

    def test_config_per_tick_explicit(self):
        worker = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0, 'recompute': 'per_tick'})
        assert worker.get_recompute_cadence() == RecomputeCadence.PER_TICK


class TestCadenceValidation:
    """The reserved 'recompute' key is validated at preflight."""

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match='recompute'):
            BollingerWorker.validate_config({'periods': {'M5': 5}, 'recompute': 'nope'})

    def test_valid_value_passes(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}, 'recompute': 'bar_close'})

    def test_absent_value_passes(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}})


# =============================================================================
# TESTS — orchestrator routing
# =============================================================================

class TestOrchestratorRouting:
    """ON_BAR_CLOSE recomputes only on a close (+ cold start); PER_TICK every tick."""

    def test_per_tick_recomputes_every_tick(self):
        records = _drive(_one_minute_ticks())
        ids = [id(pt) for _, pt, _ in records]
        assert len(set(ids)) == len(records)

    def test_bar_close_recomputes_only_on_close_plus_cold_start(self):
        records = _drive(_one_minute_ticks())
        num_closes = sum(1 for closed, _, _ in records if closed)
        distinct_bc = {id(bc) for _, _, bc in records}
        assert num_closes >= 1
        assert len(distinct_bc) == num_closes + 1  # one cold-start seed + one per close

    def test_bar_close_serves_cache_between_closes(self):
        records = _drive(_one_minute_ticks())
        for idx in range(1, len(records)):
            closed, _, bc = records[idx]
            if not closed:
                assert bc is records[idx - 1][2]  # unchanged cached object


# =============================================================================
# TESTS — determinism (#368)
# =============================================================================

class TestDeterminism:
    """bar_close == per_tick on the bar-close grid — only faster, never different."""

    def test_bar_close_matches_per_tick_on_close_grid(self):
        records = _drive(_one_minute_ticks())
        close_records = [(pt, bc) for closed, pt, bc in records if closed]
        assert close_records
        for pt, bc in close_records:
            assert bc.outputs == pt.outputs
