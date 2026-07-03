"""HybridSentimentReference fusion + factory registration + #425 subscription (#141)."""

from conftest import make_tick, utc
from python.framework.decision_logic.core.hybrid_sentiment_reference import HybridSentimentReference
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.types.decision_logic_types import DecisionLogicAction
from python.framework.types.worker_types import WorkerResult


def _logic(mock_logger) -> HybridSentimentReference:
    # Build via the factory so schema defaults are applied (matches real construction).
    return DecisionLogicFactory(logger=mock_logger).create_logic(
        logic_type='CORE/hybrid_sentiment_reference',
        logger=mock_logger,
        logic_config={},
    )


def _rsi(value: float) -> WorkerResult:
    return WorkerResult(outputs={'rsi_value': value})


def _sentiment(score: float, confidence: float = 0.8, is_stale: bool = False) -> WorkerResult:
    # Feed status rides the result ENVELOPE (#434), not the payload outputs
    return WorkerResult(
        outputs={'sentiment_score': score, 'confidence': confidence},
        is_stale=is_stale,
    )


def _tick():
    return make_tick(utc(2026, 1, 15, 8, 5))


class TestFusion:
    def test_rsi_neutral_is_flat(self, mock_logger):
        decision = _logic(mock_logger).compute_tick(_tick(), {'rsi_fast': _rsi(50)})
        assert decision.action == DecisionLogicAction.FLAT

    def test_buy_with_aligned_sentiment_boosts_confidence(self, mock_logger):
        logic = _logic(mock_logger)
        # Unusable sentiment (confidence below threshold) → pure indicator baseline
        base = logic.compute_tick(
            _tick(), {'rsi_fast': _rsi(20), 'sentiment': _sentiment(0.0, confidence=0.0)})
        boosted = logic.compute_tick(
            _tick(), {'rsi_fast': _rsi(20), 'sentiment': _sentiment(0.6)})
        assert boosted.action == DecisionLogicAction.BUY
        assert boosted.get_signal('confidence') > base.get_signal('confidence')

    def test_buy_blocked_by_opposing_sentiment(self, mock_logger):
        decision = _logic(mock_logger).compute_tick(
            _tick(), {'rsi_fast': _rsi(20), 'sentiment': _sentiment(-0.6)})
        assert decision.action == DecisionLogicAction.FLAT

    def test_stale_opposing_sentiment_is_ignored(self, mock_logger):
        decision = _logic(mock_logger).compute_tick(
            _tick(), {'rsi_fast': _rsi(20), 'sentiment': _sentiment(-0.6, is_stale=True)})
        assert decision.action == DecisionLogicAction.BUY  # opposing but stale → ignored

    def test_missing_rsi_is_flat(self, mock_logger):
        decision = _logic(mock_logger).compute_tick(_tick(), {})
        assert decision.action == DecisionLogicAction.FLAT


class TestRegistrationAndSubscription:
    def test_declares_sentiment_signals(self, mock_logger):
        requirements = _logic(mock_logger).get_required_workers()
        assert requirements['sentiment'].worker_type == 'CORE/llm_sentiment'
        assert 'sentiment_score' in requirements['sentiment'].signals

    def test_factory_registered(self, mock_logger):
        logic = _logic(mock_logger)
        assert isinstance(logic, HybridSentimentReference)

    def test_declared_signals_exist_on_worker_schema(self, mock_logger):
        # #425: every declared sentiment signal must exist on the worker's output schema
        worker_class, _ = WorkerFactory(logger=mock_logger).resolve_worker_class('CORE/llm_sentiment')
        worker_outputs = set(worker_class.get_output_schema().keys())
        declared = _logic(mock_logger).get_required_workers()['sentiment'].signals
        assert set(declared) <= worker_outputs
