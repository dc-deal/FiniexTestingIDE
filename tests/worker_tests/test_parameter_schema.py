"""
FiniexTestingIDE - Parameter Schema Tests
Validates that all Workers and Decision Logics declare correct parameter schemas.

Tests (parametrized over all CORE components):
- Schema returns dict
- ParameterDef instances are valid
- Types are supported Python types
- Numeric bounds are consistent (min < max)
- Defaults are within declared bounds
- Required parameters use REQUIRED sentinel
"""

import pytest

from python.framework.types.parameter_types import ParameterDef, REQUIRED, _RequiredSentinel
from python.framework.workers.core.rsi_worker import RSIWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.macd_worker import MACDWorker
from python.framework.workers.core.obv_worker import OBVWorker
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRSIWorker
from python.framework.workers.core.backtesting.backtesting_sample_worker import BacktestingSampleWorker
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend
from python.framework.decision_logic.core.backtesting.backtesting_deterministic import BacktestingDeterministic

from conftest import (
    ALL_WORKERS,
    ALL_DECISION_LOGICS,
    ALL_COMPONENTS,
)


# ============================================
# Schema Structure Tests
# ============================================

class TestSchemaStructure:
    """Validate schema declaration format for all components."""

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_schema_returns_dict(self, cls):
        """get_parameter_schema() must return a dict."""
        schema = cls.get_parameter_schema()
        assert isinstance(schema, dict), (
            f"{cls.__name__}.get_parameter_schema() returned "
            f"{type(schema).__name__}, expected dict"
        )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_schema_values_are_parameter_defs(self, cls):
        """All schema values must be ParameterDef instances."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            assert isinstance(param_def, ParameterDef), (
                f"{cls.__name__}.{param_name}: expected ParameterDef, "
                f"got {type(param_def).__name__}"
            )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_schema_keys_are_strings(self, cls):
        """All schema keys must be non-empty strings."""
        schema = cls.get_parameter_schema()
        for param_name in schema.keys():
            assert isinstance(param_name, str) and len(param_name) > 0, (
                f"{cls.__name__}: schema key must be non-empty string, "
                f"got {repr(param_name)}"
            )


# ============================================
# ParameterDef Validity Tests
# ============================================

class TestParameterDefValidity:
    """Validate each ParameterDef is internally consistent."""

    SUPPORTED_TYPES = (float, int, bool, str, list)

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_param_types_are_supported(self, cls):
        """param_type must be a supported Python type."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            assert param_def.param_type in self.SUPPORTED_TYPES, (
                f"{cls.__name__}.{param_name}: unsupported param_type "
                f"{param_def.param_type}, allowed: {self.SUPPORTED_TYPES}"
            )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_min_less_than_max(self, cls):
        """If both min_val and max_val are set, min must be < max."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            if param_def.min_val is not None and param_def.max_val is not None:
                assert param_def.min_val < param_def.max_val, (
                    f"{cls.__name__}.{param_name}: min_val={param_def.min_val} "
                    f">= max_val={param_def.max_val}"
                )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_defaults_within_bounds(self, cls):
        """Non-REQUIRED defaults must be within declared min/max bounds."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            if param_def.is_required:
                continue

            default = param_def.default

            if param_def.min_val is not None and isinstance(default, (int, float)):
                assert default >= param_def.min_val, (
                    f"{cls.__name__}.{param_name}: default={default} "
                    f"is below min_val={param_def.min_val}"
                )

            if param_def.max_val is not None and isinstance(default, (int, float)):
                assert default <= param_def.max_val, (
                    f"{cls.__name__}.{param_name}: default={default} "
                    f"is above max_val={param_def.max_val}"
                )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_defaults_match_declared_type(self, cls):
        """Non-REQUIRED defaults must match their declared param_type."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            if param_def.is_required:
                continue

            default = param_def.default
            expected_type = param_def.param_type

            # int is acceptable for float
            if expected_type == float and isinstance(default, int):
                continue

            assert isinstance(default, expected_type), (
                f"{cls.__name__}.{param_name}: default={default} "
                f"({type(default).__name__}) doesn't match "
                f"param_type={expected_type.__name__}"
            )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_choices_contain_valid_values(self, cls):
        """If choices are declared, they must be a tuple with values."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            if param_def.choices is not None:
                assert isinstance(param_def.choices, tuple), (
                    f"{cls.__name__}.{param_name}: choices must be tuple, "
                    f"got {type(param_def.choices).__name__}"
                )
                assert len(param_def.choices) >= 2, (
                    f"{cls.__name__}.{param_name}: choices must have >= 2 values"
                )

    @pytest.mark.parametrize("cls", ALL_COMPONENTS, ids=lambda c: c.__name__)
    def test_defaults_in_choices(self, cls):
        """If choices are declared and param is optional, default must be in choices."""
        schema = cls.get_parameter_schema()
        for param_name, param_def in schema.items():
            if param_def.choices is not None and not param_def.is_required:
                assert param_def.default in param_def.choices, (
                    f"{cls.__name__}.{param_name}: default={param_def.default} "
                    f"not in choices={param_def.choices}"
                )


# ============================================
# Worker-Specific Schema Tests
# ============================================

class TestWorkerSpecificSchemas:
    """Validate specific known schemas for CORE workers."""

    def test_rsi_has_no_algorithm_params(self):
        """RSI only uses periods (structural) - no algorithm parameters."""
        schema = RSIWorker.get_parameter_schema()
        assert schema == {}

    def test_obv_has_no_algorithm_params(self):
        """OBV only uses periods (structural) - no algorithm parameters."""
        schema = OBVWorker.get_parameter_schema()
        assert schema == {}

    def test_envelope_has_deviation(self):
        """EnvelopeWorker must declare deviation with sensible bounds."""
        schema = EnvelopeWorker.get_parameter_schema()
        assert 'deviation' in schema
        dev = schema['deviation']
        assert dev.param_type == float
        assert not dev.is_required
        assert dev.default == 2.0
        assert dev.min_val == 0.5
        assert dev.max_val == 5.0

    def test_macd_has_three_required_periods(self):
        """MACDWorker must declare fast_period, slow_period, signal_period as REQUIRED."""
        schema = MACDWorker.get_parameter_schema()
        for param_name in ('fast_period', 'slow_period', 'signal_period'):
            assert param_name in schema, f"Missing {param_name}"
            assert schema[param_name].is_required, f"{param_name} should be REQUIRED"
            assert schema[param_name].param_type == int

    def test_heavy_rsi_has_artificial_load(self):
        """HeavyRSIWorker must declare artificial_load_ms."""
        schema = HeavyRSIWorker.get_parameter_schema()
        assert 'artificial_load_ms' in schema
        assert schema['artificial_load_ms'].param_type == float
        assert not schema['artificial_load_ms'].is_required


# ============================================
# Decision Logic Specific Schema Tests
# ============================================

class TestDecisionLogicSpecificSchemas:
    """Validate specific known schemas for CORE decision logics."""

    def test_simple_consensus_has_rsi_thresholds(self):
        """SimpleConsensus must have RSI oversold/overbought thresholds."""
        schema = SimpleConsensus.get_parameter_schema()
        assert 'rsi_oversold' in schema
        assert 'rsi_overbought' in schema
        # Oversold max < Overbought min (no overlap)
        assert schema['rsi_oversold'].max_val < schema['rsi_overbought'].min_val

    def test_aggressive_trend_has_rsi_thresholds(self):
        """AggressiveTrend must have RSI buy/sell thresholds."""
        schema = AggressiveTrend.get_parameter_schema()
        assert 'rsi_buy_threshold' in schema
        assert 'rsi_sell_threshold' in schema

    def test_backtesting_deterministic_has_trade_sequence(self):
        """BacktestingDeterministic must declare trade_sequence."""
        schema = BacktestingDeterministic.get_parameter_schema()
        assert 'trade_sequence' in schema
        assert schema['trade_sequence'].param_type == list

    def test_all_logics_have_lot_size(self):
        """All non-backtesting decision logics should have lot_size parameter."""
        for cls in (SimpleConsensus, AggressiveTrend):
            schema = cls.get_parameter_schema()
            assert 'lot_size' in schema, f"{cls.__name__} missing lot_size"
            assert schema['lot_size'].min_val > 0, f"{cls.__name__} lot_size min must be > 0"
