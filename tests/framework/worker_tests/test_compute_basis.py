"""
Worker Compute-Basis Tests (#420).

The single `compute_basis` axis that unifies the former recompute cadence (#384) and
current-bar inclusion (#387). Covers:
- resolution: config 'compute_basis' overrides the worker's mandatory declaration,
- preflight validation of the reserved 'compute_basis' key,
- effective_bars(): LIVE appends the current (forming) bar, BAR_CLOSE is completed-only,
- compute independence: a BAR_CLOSE worker ignores the current bar (the property that
  makes its bar-close recompute determinism-safe on any finer grid); LIVE reacts to it,
- orchestrator routing: BAR_CLOSE recomputes only when a required timeframe closes
  (cached in between, seeded once at cold start); LIVE every tick.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import ComputeBasis, WorkerResult
from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


# =============================================================================
# HELPERS
# =============================================================================

def _bollinger(params: Dict) -> BollingerWorker:
    """Build a Bollinger worker with the given config (mock logger)."""
    return BollingerWorker(name='bb', parameters=params, logger=MagicMock())


def _bar(close: float, complete: bool = True, tf: str = 'M5') -> Bar:
    return Bar(
        symbol='EURUSD', timeframe=tf, timestamp='2025-10-01T00:00:00+00:00',
        open=close, high=close, low=close, close=close, volume=1.0,
        is_complete=complete,
    )


def _tick(mid: float, ts: datetime = None) -> TickData:
    ts = ts or datetime(2025, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    return TickData(timestamp=ts, symbol='EURUSD', bid=mid, ask=mid + 0.0002, volume=0.1)


_HISTORY = {'M5': [_bar(100), _bar(101), _bar(102), _bar(103), _bar(104)]}
_CURRENT = {'M5': _bar(110, complete=False)}
_BASE = {'periods': {'M5': 5}, 'deviation': 2.0}


# =============================================================================
# TESTS — resolution
# =============================================================================

class TestBasisResolution:
    """config 'compute_basis' overrides the worker's declaration; default is LIVE."""

    def test_default_is_live(self):
        assert _bollinger(_BASE).get_compute_basis() == ComputeBasis.LIVE

    def test_declared_default_is_live(self):
        assert _bollinger(_BASE).get_default_compute_basis() == ComputeBasis.LIVE

    def test_config_bar_close_overrides(self):
        worker = _bollinger({**_BASE, 'compute_basis': 'bar_close'})
        assert worker.get_compute_basis() == ComputeBasis.BAR_CLOSE

    def test_config_live_explicit(self):
        worker = _bollinger({**_BASE, 'compute_basis': 'live'})
        assert worker.get_compute_basis() == ComputeBasis.LIVE

    def test_basis_is_cached(self):
        worker = _bollinger({**_BASE, 'compute_basis': 'bar_close'})
        assert worker.get_compute_basis() is worker.get_compute_basis()


class TestBasisValidation:
    """The reserved 'compute_basis' key is validated at preflight."""

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match='compute_basis'):
            BollingerWorker.validate_config({'periods': {'M5': 5}, 'compute_basis': 'nope'})

    def test_valid_value_passes(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}, 'compute_basis': 'bar_close'})

    def test_absent_value_passes(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}})


# =============================================================================
# TESTS — effective_bars (the former current-bar axis, now driven by the basis)
# =============================================================================

class TestEffectiveBars:
    """LIVE appends the current bar; BAR_CLOSE is completed-bars-only."""

    def test_live_appends_current(self):
        bars = _bollinger(_BASE).effective_bars('M5', _HISTORY, _CURRENT)
        assert len(bars) == 6
        assert bars[-1].close == 110

    def test_bar_close_excludes_current(self):
        worker = _bollinger({**_BASE, 'compute_basis': 'bar_close'})
        bars = worker.effective_bars('M5', _HISTORY, _CURRENT)
        assert len(bars) == 5
        assert all(b.close != 110 for b in bars)

    def test_no_current_bar_present(self):
        bars = _bollinger(_BASE).effective_bars('M5', _HISTORY, {})
        assert len(bars) == 5


class TestComputeIndependence:
    """A BAR_CLOSE worker ignores the current bar; LIVE reacts to it."""

    def test_bar_close_bands_independent_of_current(self):
        worker = _bollinger({**_BASE, 'compute_basis': 'bar_close'})
        with_current = worker.compute(_tick(110), _HISTORY, _CURRENT)
        without_current = worker.compute(_tick(110), _HISTORY, {})
        for band in ('upper', 'middle', 'lower'):
            assert with_current.outputs[band] == without_current.outputs[band]

    def test_live_bands_react_to_current(self):
        worker = _bollinger(_BASE)  # LIVE default
        with_current = worker.compute(_tick(110), _HISTORY, _CURRENT)
        without_current = worker.compute(_tick(110), _HISTORY, {})
        assert with_current.outputs['middle'] != without_current.outputs['middle']


# =============================================================================
# TESTS — orchestrator routing
# =============================================================================

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


def _one_minute_ticks(count: int = 30) -> List[TickData]:
    """count one-minute ticks from a 10:00 UTC boundary (M5 closes every 5 ticks)."""
    start = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return [_tick(1.1000 + i * 0.0005, start + timedelta(minutes=i)) for i in range(count)]


def _drive(ticks: List[TickData]) -> List[Tuple[bool, WorkerResult, WorkerResult]]:
    """Feed ticks through one shared bar controller into a LIVE and a BAR_CLOSE Bollinger."""
    controller = BarRenderingController(logger=MagicMock())
    w_live = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0})
    w_bar_close = _bollinger({'periods': {'M5': 5}, 'deviation': 2.0, 'compute_basis': 'bar_close'})
    controller.register_workers([w_live])  # drives M5 rendering

    orch_live = _orchestrator(w_live)
    orch_bc = _orchestrator(w_bar_close)

    records: List[Tuple[bool, WorkerResult, WorkerResult]] = []
    for tick in ticks:
        current_bars = controller.process_tick(tick)
        bar_history = controller.get_all_bar_history(tick.symbol)
        state = controller.consume_bar_render_state()  # consumed ONCE, shared to both
        m5_closed = 'M5' in state.closed_timeframes

        orch_live.process_tick(tick, current_bars, bar_history, state)
        orch_bc.process_tick(tick, current_bars, bar_history, state)

        records.append((
            m5_closed,
            orch_live.get_worker_result('bb'),
            orch_bc.get_worker_result('bb'),
        ))
    return records


class TestOrchestratorRouting:
    """BAR_CLOSE recomputes only on a close (+ cold start); LIVE every tick."""

    def test_live_recomputes_every_tick(self):
        records = _drive(_one_minute_ticks())
        ids = [id(live) for _, live, _ in records]
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
