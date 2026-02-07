"""
FiniexTestingIDE - Factory Integration Tests
Tests WorkerFactory and DecisionLogicFactory with parameter schema validation.

Validates:
- Successful creation with valid configs
- Rejection on missing required params
- Rejection on out-of-range (strict)
- Warning on out-of-range (non-strict)
- Default injection for optional params
"""

import pytest
from unittest.mock import MagicMock

from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def mock_logger():
    """Fresh mock logger per test (not session-scoped)."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def strict_worker_factory(mock_logger):
    """WorkerFactory with strict validation."""
    return WorkerFactory(logger=mock_logger, strict_parameter_validation=True)


@pytest.fixture
def lenient_worker_factory(mock_logger):
    """WorkerFactory with non-strict validation (warnings only)."""
    return WorkerFactory(logger=mock_logger, strict_parameter_validation=False)


@pytest.fixture
def strict_logic_factory(mock_logger):
    """DecisionLogicFactory with strict validation."""
    return DecisionLogicFactory(logger=mock_logger, strict_parameter_validation=True)


@pytest.fixture
def lenient_logic_factory(mock_logger):
    """DecisionLogicFactory with non-strict validation."""
    return DecisionLogicFactory(logger=mock_logger, strict_parameter_validation=False)


# ============================================
# WorkerFactory - Valid Configs
# ============================================

class TestWorkerFactoryValidConfigs:
    """Workers created successfully with valid configurations."""

    def test_create_rsi_worker(self, strict_worker_factory):
        """RSI worker with valid periods config."""
        worker = strict_worker_factory.create_worker(
            instance_name="rsi_test",
            worker_type="CORE/rsi",
            worker_config={"periods": {"M5": 14}},
        )
        assert worker is not None
        assert worker.name == "rsi_test"

    def test_create_envelope_worker(self, strict_worker_factory):
        """Envelope worker with valid deviation."""
        worker = strict_worker_factory.create_worker(
            instance_name="envelope_test",
            worker_type="CORE/envelope",
            worker_config={"periods": {"M5": 20}, "deviation": 2.0},
        )
        assert worker is not None

    def test_create_envelope_worker_default_deviation(self, strict_worker_factory):
        """Envelope worker without deviation uses default 2.0."""
        worker = strict_worker_factory.create_worker(
            instance_name="envelope_default",
            worker_type="CORE/envelope",
            worker_config={"periods": {"M5": 20}},
        )
        assert worker is not None

    def test_create_macd_worker(self, strict_worker_factory):
        """MACD worker with all required params."""
        worker = strict_worker_factory.create_worker(
            instance_name="macd_test",
            worker_type="CORE/macd",
            worker_config={
                "periods": {"M5": 26},
                "fast_period": 12,
                "slow_period": 26,
                "signal_period": 9,
            },
        )
        assert worker is not None

    def test_create_heavy_rsi_worker(self, strict_worker_factory):
        """HeavyRSI worker with optional artificial_load_ms."""
        worker = strict_worker_factory.create_worker(
            instance_name="heavy_test",
            worker_type="CORE/heavy_rsi",
            worker_config={"periods": {"M5": 14}, "artificial_load_ms": 10.0},
        )
        assert worker is not None

    def test_create_obv_worker(self, strict_worker_factory):
        """OBV worker with valid periods."""
        worker = strict_worker_factory.create_worker(
            instance_name="obv_test",
            worker_type="CORE/obv",
            worker_config={"periods": {"M5": 20}},
        )
        assert worker is not None


# ============================================
# WorkerFactory - Missing Required Params
# ============================================

class TestWorkerFactoryMissingRequired:
    """Missing required parameters must abort creation."""

    def test_macd_missing_fast_period(self, strict_worker_factory):
        """MACD without fast_period must raise."""
        with pytest.raises(ValueError, match="fast_period"):
            strict_worker_factory.create_worker(
                instance_name="macd_bad",
                worker_type="CORE/macd",
                worker_config={
                    "periods": {"M5": 26},
                    "slow_period": 26,
                    "signal_period": 9,
                    # fast_period missing
                },
            )

    def test_macd_missing_all_required(self, strict_worker_factory):
        """MACD without any algorithm params must raise."""
        with pytest.raises(ValueError):
            strict_worker_factory.create_worker(
                instance_name="macd_empty",
                worker_type="CORE/macd",
                worker_config={"periods": {"M5": 26}},
            )


# ============================================
# WorkerFactory - Boundary Violations (Strict)
# ============================================

class TestWorkerFactoryBoundaryStrict:
    """Out-of-range values must abort in strict mode."""

    def test_envelope_deviation_too_low(self, strict_worker_factory):
        """deviation=0.02 must be rejected (the original bug)."""
        with pytest.raises(ValueError, match="below minimum"):
            strict_worker_factory.create_worker(
                instance_name="envelope_bug",
                worker_type="CORE/envelope",
                worker_config={"periods": {"M5": 20}, "deviation": 0.02},
            )

    def test_envelope_deviation_too_high(self, strict_worker_factory):
        """deviation=50.0 must be rejected."""
        with pytest.raises(ValueError, match="above maximum"):
            strict_worker_factory.create_worker(
                instance_name="envelope_high",
                worker_type="CORE/envelope",
                worker_config={"periods": {"M5": 20}, "deviation": 50.0},
            )

    def test_macd_fast_period_zero(self, strict_worker_factory):
        """fast_period=0 below min_val=1 must be rejected."""
        with pytest.raises(ValueError, match="below minimum"):
            strict_worker_factory.create_worker(
                instance_name="macd_zero",
                worker_type="CORE/macd",
                worker_config={
                    "periods": {"M5": 26},
                    "fast_period": 0,
                    "slow_period": 26,
                    "signal_period": 9,
                },
            )

    def test_heavy_rsi_negative_load(self, strict_worker_factory):
        """artificial_load_ms=-1 below min_val=0 must be rejected."""
        with pytest.raises(ValueError, match="below minimum"):
            strict_worker_factory.create_worker(
                instance_name="heavy_neg",
                worker_type="CORE/heavy_rsi",
                worker_config={"periods": {"M5": 14}, "artificial_load_ms": -1.0},
            )


# ============================================
# WorkerFactory - Boundary Violations (Non-Strict)
# ============================================

class TestWorkerFactoryBoundaryNonStrict:
    """Out-of-range values warn but allow creation in non-strict mode."""

    def test_envelope_deviation_too_low_warns(self, lenient_worker_factory, mock_logger):
        """deviation=0.02 creates worker but logs warning."""
        worker = lenient_worker_factory.create_worker(
            instance_name="envelope_warn",
            worker_type="CORE/envelope",
            worker_config={"periods": {"M5": 20}, "deviation": 0.02},
        )
        assert worker is not None
        # Check that warning was logged
        mock_logger.warning.assert_called()
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("below minimum" in w for w in warning_calls)


# ============================================
# DecisionLogicFactory - Valid Configs
# ============================================

class TestDecisionLogicFactoryValidConfigs:
    """Decision logics created successfully with valid configurations."""

    def test_create_simple_consensus(self, strict_logic_factory, mock_logger):
        """SimpleConsensus with valid thresholds."""
        logic = strict_logic_factory.create_logic(
            logic_type="CORE/simple_consensus",
            logger=mock_logger,
            logic_config={"rsi_oversold": 30, "rsi_overbought": 70, "lot_size": 0.1},
        )
        assert logic is not None

    def test_create_aggressive_trend(self, strict_logic_factory, mock_logger):
        """AggressiveTrend with valid config."""
        logic = strict_logic_factory.create_logic(
            logic_type="CORE/aggressive_trend",
            logger=mock_logger,
            logic_config={"rsi_buy_threshold": 35, "rsi_sell_threshold": 65},
        )
        assert logic is not None

    def test_create_simple_consensus_defaults_only(self, strict_logic_factory, mock_logger):
        """SimpleConsensus with empty config uses all defaults."""
        logic = strict_logic_factory.create_logic(
            logic_type="CORE/simple_consensus",
            logger=mock_logger,
            logic_config={},
        )
        assert logic is not None

    def test_create_backtesting_deterministic(self, strict_logic_factory, mock_logger):
        """BacktestingDeterministic with trade sequence."""
        logic = strict_logic_factory.create_logic(
            logic_type="CORE/backtesting/backtesting_deterministic",
            logger=mock_logger,
            logic_config={
                "trade_sequence": [
                    {"tick_number": 10, "direction": "LONG", "hold_ticks": 100, "lot_size": 0.01}
                ],
                "lot_size": 0.1,
            },
        )
        assert logic is not None


# ============================================
# DecisionLogicFactory - Boundary Violations (Strict)
# ============================================

class TestDecisionLogicFactoryBoundaryStrict:
    """Out-of-range decision logic params must abort in strict mode."""

    def test_consensus_rsi_oversold_too_high(self, strict_logic_factory, mock_logger):
        """rsi_oversold=60 above max_val=49 must be rejected."""
        with pytest.raises(ValueError, match="above maximum"):
            strict_logic_factory.create_logic(
                logic_type="CORE/simple_consensus",
                logger=mock_logger,
                logic_config={"rsi_oversold": 60},
            )

    def test_consensus_lot_size_zero(self, strict_logic_factory, mock_logger):
        """lot_size=0.0 below min_val=0.01 must be rejected."""
        with pytest.raises(ValueError, match="below minimum"):
            strict_logic_factory.create_logic(
                logic_type="CORE/simple_consensus",
                logger=mock_logger,
                logic_config={"lot_size": 0.0},
            )

    def test_consensus_min_confidence_above_one(self, strict_logic_factory, mock_logger):
        """min_confidence=1.5 above max_val=1.0 must be rejected."""
        with pytest.raises(ValueError, match="above maximum"):
            strict_logic_factory.create_logic(
                logic_type="CORE/simple_consensus",
                logger=mock_logger,
                logic_config={"min_confidence": 1.5},
            )


# ============================================
# DecisionLogicFactory - Boundary Violations (Non-Strict)
# ============================================

class TestDecisionLogicFactoryBoundaryNonStrict:
    """Out-of-range values warn but allow creation in non-strict mode."""

    def test_consensus_oversold_too_high_warns(self, lenient_logic_factory, mock_logger):
        """rsi_oversold=60 creates logic but logs warning."""
        logic = lenient_logic_factory.create_logic(
            logic_type="CORE/simple_consensus",
            logger=mock_logger,
            logic_config={"rsi_oversold": 60},
        )
        assert logic is not None
        mock_logger.warning.assert_called()
