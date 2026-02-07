"""
FiniexTestingIDE - Worker Defaults Tests
Tests for apply_defaults() utility function.

Validates:
- Missing optionals filled with defaults
- Existing config values preserved (user overrides)
- Required params left unfilled (no default to apply)
- Extra config keys not in schema preserved
"""

import pytest

from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.types.parameter_types import ParameterDef, REQUIRED
from python.framework.validators.parameter_validator import apply_defaults, validate_parameters
from python.framework.workers.core.backtesting.backtesting_sample_worker import BacktestingSampleWorker
from conftest import ALL_WORKERS, ALL_DECISION_LOGICS
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRSIWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.macd_worker import MACDWorker

# ============================================
# Test Schema Fixtures
# ============================================


@pytest.fixture
def mixed_schema():
    """Schema with both required and optional params."""
    return {
        'fast_period': ParameterDef(param_type=int, default=REQUIRED, min_val=1, max_val=200),
        'deviation': ParameterDef(param_type=float, default=2.0, min_val=0.5, max_val=5.0),
        'enabled': ParameterDef(param_type=bool, default=True),
        'label': ParameterDef(param_type=str, default="default_label"),
    }


# ============================================
# Core Behavior
# ============================================

class TestApplyDefaultsCore:
    """Core apply_defaults() behavior."""

    def test_missing_optionals_filled(self, mixed_schema):
        """Missing optional params get their defaults."""
        config = {'fast_period': 12}
        merged = apply_defaults(config, mixed_schema)
        assert merged['deviation'] == 2.0
        assert merged['enabled'] is True
        assert merged['label'] == "default_label"

    def test_existing_values_preserved(self, mixed_schema):
        """User-provided values are not overwritten."""
        config = {'fast_period': 12, 'deviation': 3.5, 'enabled': False}
        merged = apply_defaults(config, mixed_schema)
        assert merged['deviation'] == 3.5
        assert merged['enabled'] is False

    def test_required_params_not_injected(self, mixed_schema):
        """Required params without user value are NOT filled (no default)."""
        config = {'deviation': 2.0}  # fast_period not provided
        merged = apply_defaults(config, mixed_schema)
        assert 'fast_period' not in merged

    def test_original_config_unchanged(self, mixed_schema):
        """apply_defaults returns a copy, original config is not mutated."""
        config = {'fast_period': 12}
        original_keys = set(config.keys())
        merged = apply_defaults(config, mixed_schema)
        assert set(config.keys()) == original_keys
        assert 'deviation' not in config
        assert 'deviation' in merged

    def test_extra_keys_preserved(self, mixed_schema):
        """Keys not in schema are passed through unchanged."""
        config = {'fast_period': 12, 'periods': {
            'M5': 14}, 'custom_flag': True}
        merged = apply_defaults(config, mixed_schema)
        assert merged['periods'] == {'M5': 14}
        assert merged['custom_flag'] is True

    def test_empty_config_gets_all_defaults(self, mixed_schema):
        """Empty config gets all optional defaults, required stays missing."""
        merged = apply_defaults({}, mixed_schema)
        assert merged['deviation'] == 2.0
        assert merged['enabled'] is True
        assert merged['label'] == "default_label"
        assert 'fast_period' not in merged

    def test_empty_schema_returns_copy(self):
        """Empty schema returns config unchanged."""
        config = {'anything': 42}
        merged = apply_defaults(config, {})
        assert merged == {'anything': 42}


# ============================================
# Real Worker Defaults
# ============================================

class TestRealWorkerDefaults:
    """Validate defaults from actual CORE worker schemas."""

    def test_envelope_default_deviation(self):
        """EnvelopeWorker: empty config → deviation=2.0."""

        schema = EnvelopeWorker.get_parameter_schema()
        merged = apply_defaults({}, schema)
        assert merged['deviation'] == 2.0

    def test_heavy_rsi_default_load(self):
        """HeavyRSIWorker: empty config → artificial_load_ms=5.0."""

        schema = HeavyRSIWorker.get_parameter_schema()
        merged = apply_defaults({}, schema)
        assert merged['artificial_load_ms'] == 5.0

    def test_macd_no_defaults_for_required(self):
        """MACDWorker: empty config → no fast/slow/signal injected."""
        schema = MACDWorker.get_parameter_schema()
        merged = apply_defaults({}, schema)
        assert 'fast_period' not in merged
        assert 'slow_period' not in merged
        assert 'signal_period' not in merged

    def test_simple_consensus_all_defaults(self):
        """SimpleConsensus: empty config → all params filled."""
        schema = SimpleConsensus.get_parameter_schema()
        merged = apply_defaults({}, schema)
        # All params are optional with defaults
        for param_name, param_def in schema.items():
            assert param_name in merged, f"Missing default for {param_name}"
            assert merged[param_name] == param_def.default

    def test_aggressive_trend_all_defaults(self):
        """AggressiveTrend: empty config → all params filled."""
        schema = AggressiveTrend.get_parameter_schema()
        merged = apply_defaults({}, schema)
        for param_name, param_def in schema.items():
            assert param_name in merged, f"Missing default for {param_name}"

    def test_backtesting_sample_worker_default(self):
        """BacktestingSampleWorker: empty config → bar_snapshot_checks=[]."""

        schema = BacktestingSampleWorker.get_parameter_schema()
        merged = apply_defaults({}, schema)
        assert merged['bar_snapshot_checks'] == []

    @pytest.mark.parametrize("cls", ALL_WORKERS + ALL_DECISION_LOGICS, ids=lambda c: c.__name__)
    def test_defaults_produce_valid_config(self, cls):
        """Applying defaults to empty config must not violate own schema."""
        schema = cls.get_parameter_schema()
        merged = apply_defaults({}, schema)

        # Only validate params that have defaults (skip REQUIRED)
        optional_schema = {
            k: v for k, v in schema.items() if not v.is_required
        }
        warnings = validate_parameters(merged, optional_schema, strict=True)
        assert warnings == [], (
            f"{cls.__name__}: defaults violate own schema: {warnings}"
        )
