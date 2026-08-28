"""
Signal resolution counters (#433 Part C): tick-exact counting in the shared orchestrator.

The counters answer what the strategy DECIDED ON, so they must be tick-weighted, never
refresh-weighted — the worker only recomputes when the snapshot or the staleness flips.
These tests pin the three properties that guarantee that: one count per tick per worker
(sequential AND parallel worker path), the cached class stays correct between refreshes,
and a heartbeat pass counts nothing (it re-evaluates nothing).
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from tests.framework.signal_workers.conftest import SYMBOL, make_provider, make_tick, snapshot, utc

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.signal_data_types import SignalResolution
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.types.worker_types import WorkerRequirement
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _worker(mock_logger, max_staleness: int = 30) -> LlmSentimentWorker:
    """A SIGNAL worker bound to the fixture symbol."""
    return LlmSentimentWorker(
        name='sentiment',
        parameters={'max_staleness_minutes': max_staleness,
                    'signal_delay_minutes': 0},
        logger=mock_logger,
        trading_context=SimpleNamespace(symbol=SYMBOL),
    )


class _CountingDecision(AbstractDecisionLogic):
    """Minimal contract-complete decision — records nothing but stays out of the way."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'sentiment': WorkerRequirement.of('CORE/llm_sentiment', 'sentiment_score')}

    def compute_tick(self, tick, worker_results):
        return Decision(action=DecisionLogicAction.FLAT, outputs={})

    def _execute_decision_impl(self, decision, tick):
        return []

    def on_market_data_stale(self, status):
        pass

    def on_signal_stale(self, worker_name: str, signal_kind: str) -> None:
        pass


def _orchestrator(mock_logger, worker, parallel: bool = False) -> WorkerOrchestrator:
    """A process_tick-ready orchestrator around one SIGNAL worker."""
    orchestrator = WorkerOrchestrator(
        decision_logic=_CountingDecision('stub', mock_logger, {}),
        strategy_config={'worker_instances': {'sentiment': 'CORE/llm_sentiment'}},
        workers=[worker],
        parallel_workers=parallel,
    )
    orchestrator.is_initialized = True
    orchestrator.tick_logger = SimpleNamespace(log_tick_data=lambda **kw: None)
    return orchestrator


def _stats(orchestrator):
    """The single SIGNAL worker's counters."""
    rows = orchestrator.get_signal_statistics()
    assert len(rows) == 1
    return rows[0]


@pytest.fixture
def provider():
    """Two snapshots, 10 minutes apart — the cadence the counters are measured against."""
    return make_provider(
        snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5, signal='HOLD'),
        snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8, signal='BUY'))


class TestClassification:
    """The worker splits what _evaluate_stale collapses into one boolean."""

    def test_fresh_stale_and_blind(self, mock_logger, provider):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)

        # Before the series starts: nothing resolvable at all
        worker.compute_signal(make_tick(utc(2026, 1, 15, 7, 0)))
        assert worker.get_last_resolution() == SignalResolution.BLIND

        # Inside the staleness window
        worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 5)))
        assert worker.get_last_resolution() == SignalResolution.FRESH

        # Snapshot aged out — resolved, but too old
        worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 45)))
        assert worker.get_last_resolution() == SignalResolution.STALE

    def test_cached_class_survives_a_tick_without_refresh(self, mock_logger, provider):
        """should_refresh keeps the class current when no recompute happens."""
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)

        worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 5)))
        assert worker.should_refresh(make_tick(utc(2026, 1, 15, 8, 7))) is False
        assert worker.get_last_resolution() == SignalResolution.FRESH


class TestTickExactCounting:
    """One count per tick per worker — the property the whole metric rests on."""

    @pytest.mark.parametrize('parallel', [False, True])
    def test_every_tick_counted_once(self, mock_logger, provider, parallel):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)
        orchestrator = _orchestrator(mock_logger, worker, parallel=parallel)

        # 8:01 … 8:08 — one refresh (cold start), eight ticks
        for minute in range(1, 9):
            orchestrator.process_tick(
                make_tick(utc(2026, 1, 15, 8, minute)), {}, {}, None)

        stats = _stats(orchestrator)
        assert stats.fresh_ticks + stats.stale_ticks + stats.blind_ticks == 8
        assert stats.fresh_ticks == 8

    def test_counts_ticks_not_refreshes(self, mock_logger, provider):
        """The worker computes twice over these ticks; the counter must say eight."""
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)
        orchestrator = _orchestrator(mock_logger, worker)

        for minute in (1, 3, 5, 7, 11, 13, 15, 17):   # crosses the 8:10 snapshot
            orchestrator.process_tick(
                make_tick(utc(2026, 1, 15, 8, minute)), {}, {}, None)

        assert worker.performance_logger.get_stats().worker_call_count == 2
        assert _stats(orchestrator).fresh_ticks == 8

    def test_stale_ticks_accumulate_while_the_feed_is_dead(self, mock_logger, provider):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)
        orchestrator = _orchestrator(mock_logger, worker)

        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 15)), {}, {}, None)
        for minute in (45, 50, 55):     # > 30 min past the 8:10 snapshot
            orchestrator.process_tick(
                make_tick(utc(2026, 1, 15, 8, minute)), {}, {}, None)

        stats = _stats(orchestrator)
        assert (stats.fresh_ticks, stats.stale_ticks, stats.blind_ticks) == (1, 3, 0)

    def test_identity_is_stamped_on_the_row(self, mock_logger, provider):
        worker = _worker(mock_logger)
        worker.set_signal_provider(provider)
        orchestrator = _orchestrator(mock_logger, worker)
        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 5)), {}, {}, None)

        stats = _stats(orchestrator)
        assert stats.worker_name == 'sentiment'
        assert stats.signal_kind == 'llm_sentiment'
        assert stats.symbol == SYMBOL


class TestHeartbeatDoesNotCount:
    """A heartbeat forwards the cache without re-evaluating — it is not a tick."""

    def test_heartbeat_leaves_the_counters_untouched(self, mock_logger, provider):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(provider)
        orchestrator = _orchestrator(mock_logger, worker)

        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 5)), {}, {}, None)
        before = _stats(orchestrator).fresh_ticks
        orchestrator.process_heartbeat()

        assert _stats(orchestrator).fresh_ticks == before
