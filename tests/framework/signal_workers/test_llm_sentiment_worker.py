"""LlmSentimentWorker compute_signal + provider injection + orchestrator dispatch (#141)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import SYMBOL, make_provider, make_tick, snapshot, utc
from python.framework.exceptions.signal_data_errors import SignalProviderNotInjectedError
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.worker_types import WorkerType
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _worker(mock_logger, max_staleness=30) -> LlmSentimentWorker:
    trading_context = SimpleNamespace(symbol=SYMBOL)
    return LlmSentimentWorker(
        name='sent',
        parameters={'max_staleness_minutes': max_staleness},
        logger=mock_logger,
        trading_context=trading_context,
    )


class TestContract:
    def test_is_signal_type(self):
        assert LlmSentimentWorker.get_worker_type() == WorkerType.SIGNAL
        assert issubclass(LlmSentimentWorker, AbstractSignalWorker)

    def test_signal_source(self):
        assert LlmSentimentWorker.get_signal_source() == 'llm_sentiment'

    def test_output_schema_keys(self):
        keys = set(LlmSentimentWorker.get_output_schema().keys())
        assert {'sentiment_score', 'confidence', 'signal', 'is_breaking'} <= keys
        # Feed status is the result ENVELOPE (#434), not a payload output
        assert 'is_stale' not in keys

    def test_no_warmup(self, mock_logger):
        worker = _worker(mock_logger)
        assert worker.get_warmup_requirements() == {}
        assert worker.get_required_timeframes() == []

    def test_factory_creates_core_llm_sentiment(self, mock_logger):
        worker = WorkerFactory(logger=mock_logger).create_worker(
            'sent', 'CORE/llm_sentiment', {},
            trading_context=SimpleNamespace(symbol=SYMBOL))
        assert isinstance(worker, LlmSentimentWorker)


class TestProviderRequired:
    def test_missing_provider_raises(self, mock_logger):
        worker = _worker(mock_logger)
        with pytest.raises(SignalProviderNotInjectedError):
            worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 5)))


class TestComputeSignal:
    def test_gap_returns_empty(self, mock_logger):
        worker = _worker(mock_logger)
        worker.set_signal_provider(make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5)))
        result = worker.compute_signal(make_tick(utc(2026, 1, 15, 7, 0)))
        assert result.outputs['confidence'] == 0.0
        assert result.outputs['signal'] == 'HOLD'
        assert result.is_stale is True

    def test_maps_snapshot_fields(self, mock_logger):
        worker = _worker(mock_logger)
        worker.set_signal_provider(make_provider(
            snapshot(utc(2026, 1, 15, 8, 0), 0.35, 0.8,
                     signal='BUY', urgency=0.9, is_breaking=True)))
        result = worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 5)))
        assert result.outputs['sentiment_score'] == 0.35
        assert result.outputs['confidence'] == 0.8
        assert result.outputs['signal'] == 'BUY'
        assert result.outputs['is_breaking'] is True
        assert result.is_stale is False

    def test_staleness(self, mock_logger):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5)))
        fresh = worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 20)))
        stale = worker.compute_signal(make_tick(utc(2026, 1, 15, 9, 0)))
        assert fresh.is_stale is False
        assert stale.is_stale is True


class TestShouldRefresh:
    def test_cold_start_then_window_crossing(self, mock_logger):
        worker = _worker(mock_logger)
        worker.set_signal_provider(make_provider(
            snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5),
            snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8)))
        first = make_tick(utc(2026, 1, 15, 8, 5))
        assert worker.should_refresh(first) is True       # cold start
        worker.compute_signal(first)                       # records the 08:00 window
        assert worker.should_refresh(make_tick(utc(2026, 1, 15, 8, 7))) is False   # same window
        assert worker.should_refresh(make_tick(utc(2026, 1, 15, 8, 12))) is True   # new window


class _StubDecisionLogic:
    """Minimal decision-logic surface for WorkerOrchestrator.process_tick."""

    def __init__(self):
        self.performance_logger = None

    def compute_tick(self, tick, worker_results):
        return Decision(action=DecisionLogicAction.FLAT, outputs={})


def _orchestrator(worker) -> WorkerOrchestrator:
    """process_tick-ready orchestrator without the heavy constructor."""
    orch = object.__new__(WorkerOrchestrator)
    orch.is_initialized = True
    orch.workers = {worker.name: worker}
    orch._worker_results = {}
    orch._signal_workers = {worker.name: worker}
    orch._signal_stale_state = {}
    orch.logger = MagicMock()
    orch.parallel_workers = False
    orch._coordination_stats = SimpleNamespace(ticks_processed=0)
    orch.decision_logic = _StubDecisionLogic()
    orch.tick_logger = SimpleNamespace(log_tick_data=lambda **kw: None)
    return orch


class TestOrchestratorDispatch:
    """The orchestrator dispatches compute_signal and honors the snapshot cadence (B5)."""

    def test_signal_worker_recompute_cadence(self, mock_logger):
        worker = _worker(mock_logger)
        worker.set_signal_provider(make_provider(
            snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5, signal='HOLD'),
            snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8, signal='BUY')))
        orch = _orchestrator(worker)

        orch.process_tick(make_tick(utc(2026, 1, 15, 8, 5)), {}, {}, None)
        first = orch.get_worker_result('sent')
        orch.process_tick(make_tick(utc(2026, 1, 15, 8, 7)), {}, {}, None)
        cached = orch.get_worker_result('sent')
        orch.process_tick(make_tick(utc(2026, 1, 15, 8, 12)), {}, {}, None)
        refreshed = orch.get_worker_result('sent')

        assert first.get_signal('signal') == 'HOLD'
        assert cached is first                       # served from cache (no recompute)
        assert refreshed.get_signal('signal') == 'BUY'  # recomputed on the new window
