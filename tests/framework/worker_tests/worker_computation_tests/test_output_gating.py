"""
Output gating — a worker computes only the outputs a consumer declares it reads
(get_required_workers, #425), skipping expensive optional signals. A SUBSCRIBE_ALL
requirement keeps compute-all, so existing strategies stay bit-identical.
"""

import pytest
from conftest import make_bars, make_tick

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator
from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.types.worker_types import SUBSCRIBE_ALL, WorkerRequirement

# 30 bars > period + 1 → the slope path (the gated 2nd moving average) is exercised
CLOSES = [100.0 + (i % 5) - 2 + i * 0.1 for i in range(30)]
ALL_KEYS = {'upper', 'middle', 'lower', 'position', 'position_raw',
            'slope', 'width_pct', 'std_dev', 'bars_used'}
CORE_KEYS = {'upper', 'middle', 'lower', 'position', 'std_dev', 'bars_used'}


def _bollinger(mock_logger, requested=None):
    worker = BollingerWorker(
        name="bollinger_main",
        parameters={"periods": {"M5": 20}, "deviation": 2.0},
        logger=mock_logger,
    )
    if requested is not None:
        worker.set_requested_outputs(requested)
    return worker


def _compute(worker):
    return worker.compute(
        tick=make_tick(bid=CLOSES[-1]),
        bar_history={"M5": make_bars(CLOSES)},
        current_bars={},
    )


class TestWantsOutput:
    def test_no_declaration_wants_everything(self, mock_logger):
        worker = _bollinger(mock_logger)
        assert worker.wants_output('slope') is True
        assert worker.wants_output('anything') is True

    def test_declaration_gates_to_set(self, mock_logger):
        worker = _bollinger(mock_logger, requested={'position'})
        assert worker.wants_output('position') is True
        assert worker.wants_output('slope') is False


class TestBollingerGating:
    def test_no_declaration_computes_all_outputs(self, mock_logger):
        result = _compute(_bollinger(mock_logger))
        assert set(result.outputs.keys()) == ALL_KEYS

    def test_declaration_skips_optional_outputs(self, mock_logger):
        result = _compute(_bollinger(mock_logger, requested={'position'}))
        assert set(result.outputs.keys()) == CORE_KEYS
        assert 'slope' not in result.outputs
        assert 'width_pct' not in result.outputs
        assert 'position_raw' not in result.outputs

    def test_gated_core_is_bit_identical(self, mock_logger):
        full = _compute(_bollinger(mock_logger)).outputs
        gated = _compute(_bollinger(mock_logger, requested={'position'})).outputs
        for key in CORE_KEYS:
            assert gated[key] == full[key]

    def test_explicit_slope_request_computes_it(self, mock_logger):
        result = _compute(_bollinger(mock_logger, requested={'position', 'slope'}))
        assert 'slope' in result.outputs
        assert 'width_pct' not in result.outputs


class TestDecisionDeclaration:
    def test_aggressive_trend_declares_signals(self):
        logic = AggressiveTrend.__new__(AggressiveTrend)
        assert logic.get_required_workers() == {
            "rsi_fast": WorkerRequirement.of('CORE/rsi', 'rsi_value'),
            "bollinger_main": WorkerRequirement.of('CORE/bollinger', 'position'),
        }

    def test_compute_all_logic_subscribes_all(self):
        # A logic that reads every output declares SUBSCRIBE_ALL explicitly (#425)
        workers = SimpleConsensus.__new__(SimpleConsensus).get_required_workers()
        assert workers["bollinger_main"].worker_type == 'CORE/bollinger'
        assert workers["bollinger_main"].signals is SUBSCRIBE_ALL


class _StubLogic(AbstractDecisionLogic):
    """Minimal logic with an injectable worker declaration (subscription-validation tests)."""

    def __init__(self, logger, workers_decl):
        super().__init__(name='stub', logger=logger, config={})
        self._workers_decl = workers_decl

    @classmethod
    def get_required_order_types(cls, decision_logic_config):
        return []

    def get_required_workers(self):
        return self._workers_decl

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None


class TestSubscriptionValidation:
    """The orchestrator cross-checks declared signals against the worker output schema (#425)."""

    def _build(self, mock_logger, workers_decl):
        worker = _bollinger(mock_logger)
        logic = _StubLogic(mock_logger, workers_decl)
        return WorkerOrchestrator(
            workers=[worker],
            decision_logic=logic,
            strategy_config={'worker_instances': {'bollinger_main': 'CORE/bollinger'}},
            worker_decision_tracking=False,
        )

    def test_valid_subset_passes(self, mock_logger):
        self._build(mock_logger, {
            'bollinger_main': WorkerRequirement.of('CORE/bollinger', 'position'),
        })

    def test_subscribe_all_passes(self, mock_logger):
        self._build(mock_logger, {
            'bollinger_main': WorkerRequirement.all('CORE/bollinger'),
        })

    def test_unknown_signal_raises(self, mock_logger):
        with pytest.raises(ValueError, match='nonexistent'):
            self._build(mock_logger, {
                'bollinger_main': WorkerRequirement.of('CORE/bollinger', 'nonexistent'),
            })

    def test_missing_instance_raises(self, mock_logger):
        with pytest.raises(ValueError, match='Missing'):
            self._build(mock_logger, {
                'absent': WorkerRequirement.all('CORE/rsi'),
            })
