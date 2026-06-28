"""
Output gating — a worker computes only the outputs a consumer declares it reads
(get_required_worker_signals), skipping expensive optional signals. No declaration
means compute-all, so existing strategies stay bit-identical.
"""

from conftest import make_bars, make_tick

from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend

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
    def test_aggressive_trend_declares_minimal_signals(self):
        signals = AggressiveTrend.get_required_worker_signals(AggressiveTrend.__new__(AggressiveTrend))
        assert signals == {
            "rsi_fast": {"rsi_value"},
            "bollinger_main": {"position"},
        }
