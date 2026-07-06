"""Signal-outage contract (#434): staleness-flip refresh, startup validation, hook dispatch."""

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from conftest import SYMBOL, make_provider, make_tick, snapshot, utc
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.decision_logic.core.hybrid_sentiment_reference import HybridSentimentReference
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.types.worker_types import WorkerRequirement, WorkerResult
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _worker(mock_logger, max_staleness=30) -> LlmSentimentWorker:
    trading_context = SimpleNamespace(symbol=SYMBOL)
    return LlmSentimentWorker(
        name='sentiment',
        parameters={'max_staleness_minutes': max_staleness},
        logger=mock_logger,
        trading_context=trading_context,
    )


class _StubDecisionBase(AbstractDecisionLogic):
    """Minimal concrete decision for orchestrator-validation tests."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return []


class _NoHookDecision(_StubDecisionBase):
    """Consumes the SIGNAL worker but does not override on_signal_stale."""

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'sentiment': WorkerRequirement.of('CORE/llm_sentiment', 'sentiment_score')}


class _CompliantDecision(_NoHookDecision):
    """Full contract: programmed reaction (recorded); status arrives via envelope."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stale_calls: List[tuple] = []

    def on_signal_stale(self, worker_name: str, source: str) -> None:
        self.stale_calls.append((worker_name, source))


class _SubscribeAllDecision(_CompliantDecision):
    """SUBSCRIBE_ALL satisfies the is_stale requirement implicitly."""

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'sentiment': WorkerRequirement.all('CORE/llm_sentiment')}


def _orchestrator(decision_cls, mock_logger) -> WorkerOrchestrator:
    decision = decision_cls('stub', mock_logger, {})
    return WorkerOrchestrator(
        decision_logic=decision,
        strategy_config={'worker_instances': {'sentiment': 'CORE/llm_sentiment'}},
        workers=[_worker(mock_logger)],
        parallel_workers=False,
    )


class TestStalenessFlipRefresh:
    """The feed dying mid-session must trigger ONE recompute (is_stale flip)."""

    def test_refresh_fires_on_staleness_boundary(self, mock_logger):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.5, 0.8)))

        # Cold start: new snapshot window → refresh, fresh result
        tick = make_tick(utc(2026, 1, 15, 8, 5))
        assert worker.should_refresh(tick) is True
        assert worker.compute_signal(tick).is_stale is False

        # Same snapshot, still inside the staleness window → no refresh
        assert worker.should_refresh(make_tick(utc(2026, 1, 15, 8, 20))) is False

        # Feed dead: age crosses max_staleness_minutes → staleness flip → refresh
        stale_tick = make_tick(utc(2026, 1, 15, 8, 31))
        assert worker.should_refresh(stale_tick) is True
        assert worker.compute_signal(stale_tick).is_stale is True

        # Still stale, no further flip → no refresh churn
        assert worker.should_refresh(make_tick(utc(2026, 1, 15, 9, 0))) is False

    def test_recovery_refreshes_via_new_snapshot(self, mock_logger):
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(make_provider(
            snapshot(utc(2026, 1, 15, 8, 0), 0.5, 0.8),
            snapshot(utc(2026, 1, 15, 9, 30), 0.6, 0.8),
        ))
        worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 5)))          # fresh
        worker.compute_signal(make_tick(utc(2026, 1, 15, 8, 45)))         # stale flip
        # New snapshot arrives → window change → refresh, fresh again
        recovery_tick = make_tick(utc(2026, 1, 15, 9, 31))
        assert worker.should_refresh(recovery_tick) is True
        assert worker.compute_signal(recovery_tick).is_stale is False

    def test_contract_params_inherited_from_base(self):
        """
        max_staleness_minutes + data_path are TYPE-level params — merged into
        every SIGNAL worker's schema by the base (no worker can forget them),
        while the schema getter stays the single visible config surface.
        """
        schema = LlmSentimentWorker.get_parameter_schema()
        assert schema['max_staleness_minutes'].default == 30
        assert schema['data_path'].default == ''

    def test_envelope_survives_subscription_narrowing(self, mock_logger):
        """
        The feed-status envelope is delivered with EVERY result — #425 output
        narrowing cannot filter it (the blindness is designed away, not validated).
        """
        worker = _worker(mock_logger, max_staleness=30)
        worker.set_signal_provider(make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.5, 0.8)))
        worker.set_requested_outputs({'sentiment_score'})

        result = worker.compute_signal(make_tick(utc(2026, 1, 15, 9, 0)))
        assert result.is_stale is True
        assert 'is_stale' not in result.outputs  # status is envelope, not payload


class TestContractValidation:
    """Startup validation (#434) in the shared orchestrator — both pipelines."""

    def test_missing_hook_rejected(self, mock_logger):
        with pytest.raises(ValueError, match='on_signal_stale'):
            _orchestrator(_NoHookDecision, mock_logger)

    def test_compliant_decision_accepted(self, mock_logger):
        assert _orchestrator(_CompliantDecision, mock_logger) is not None

    def test_subscribe_all_accepted(self, mock_logger):
        assert _orchestrator(_SubscribeAllDecision, mock_logger) is not None

    def test_indicator_only_decision_unaffected(self, mock_logger):
        class _IndicatorOnly(_StubDecisionBase):
            def get_required_workers(self) -> Dict[str, WorkerRequirement]:
                return {}
        decision = _IndicatorOnly('stub', mock_logger, {})
        orchestrator = WorkerOrchestrator(
            decision_logic=decision, strategy_config={},
            workers=[], parallel_workers=False,
        )
        assert orchestrator is not None


class TestHookDispatch:
    """Edge-triggered on_signal_stale: once per fresh→stale flip."""

    def _stale_result(self, is_stale: bool) -> WorkerResult:
        return WorkerResult(outputs={'sentiment_score': 0.1}, is_stale=is_stale)

    def test_fires_once_per_flip(self, mock_logger):
        orchestrator = _orchestrator(_CompliantDecision, mock_logger)
        decision = orchestrator.decision_logic

        # Session starts stale → fires on the first result
        orchestrator._worker_results['sentiment'] = self._stale_result(True)
        orchestrator._dispatch_signal_stale_transitions()
        assert decision.stale_calls == [('sentiment', 'llm_sentiment')]

        # Still stale → no re-fire
        orchestrator._dispatch_signal_stale_transitions()
        assert len(decision.stale_calls) == 1

        # Recovery resets the edge; next flip fires again
        orchestrator._worker_results['sentiment'] = self._stale_result(False)
        orchestrator._dispatch_signal_stale_transitions()
        orchestrator._worker_results['sentiment'] = self._stale_result(True)
        orchestrator._dispatch_signal_stale_transitions()
        assert len(decision.stale_calls) == 2


class TestReferenceReaction:
    """The didactic reference programs its reaction: warning + event-tape entry."""

    def test_hybrid_hook_warns_and_emits(self):
        logger = MagicMock()
        logic: HybridSentimentReference = DecisionLogicFactory(logger=logger).create_logic(
            logic_type='CORE/hybrid_sentiment_reference',
            logger=logger,
            logic_config={},
        )
        logic.trading_api = MagicMock()
        logic.trading_api.get_current_time.return_value = utc(2026, 1, 15, 8, 31)

        logic.on_signal_stale(worker_name='sentiment', source='llm_sentiment')

        assert logger.warning.called
        events = logic.get_event_history()
        assert len(events) == 1
        assert events[0].reason_key == 'signal_stale'
