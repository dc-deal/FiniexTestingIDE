"""
FiniexTestingIDE - Parameter Validation Tests
Unit tests for validate_parameters() utility function.

Tests the validation engine directly with synthetic schemas.
No Worker/Logic instances needed.
"""

import pytest

from python.framework.types.parameter_types import ParameterDef, REQUIRED
from python.framework.validators.parameter_validator import validate_parameters


# ============================================
# Test Schema Fixtures
# ============================================

@pytest.fixture
def sample_schema():
    """Schema with mixed required/optional, bounded/unbounded params."""
    return {
        'fast_period': ParameterDef(
            param_type=int, default=REQUIRED, min_val=1, max_val=200
        ),
        'deviation': ParameterDef(
            param_type=float, default=2.0, min_val=0.5, max_val=5.0
        ),
        'enabled': ParameterDef(
            param_type=bool, default=True
        ),
        'mode': ParameterDef(
            param_type=str, default="normal", choices=("normal", "aggressive", "conservative")
        ),
    }


# ============================================
# Happy Path
# ============================================

class TestValidParameterConfigs:
    """Configs that should pass validation without errors."""

    def test_all_params_provided(self, sample_schema):
        """All parameters provided with valid values."""
        config = {'fast_period': 12, 'deviation': 2.0,
                  'enabled': True, 'mode': "normal"}
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

    def test_only_required_provided(self, sample_schema):
        """Only required params provided - optionals use defaults."""
        config = {'fast_period': 12}
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

    def test_int_accepted_for_float(self, sample_schema):
        """int value should be accepted where float is declared."""
        config = {'fast_period': 12, 'deviation': 2}
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

    def test_boundary_values_accepted(self, sample_schema):
        """Values exactly at min/max should pass."""
        config = {'fast_period': 1}  # min_val=1
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

        config = {'fast_period': 200}  # max_val=200
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

    def test_valid_choice(self, sample_schema):
        """Value from choices list should pass."""
        config = {'fast_period': 12, 'mode': "aggressive"}
        warnings = validate_parameters(config, sample_schema, strict=True)
        assert warnings == []

    def test_empty_schema_always_passes(self):
        """Empty schema should accept any config."""
        warnings = validate_parameters({'anything': 42}, {}, strict=True)
        assert warnings == []


# ============================================
# Missing Required Parameters
# ============================================

class TestMissingRequired:
    """Missing REQUIRED parameters must always raise."""

    def test_missing_required_raises(self, sample_schema):
        """Missing required param raises ValueError."""
        config = {}  # fast_period is REQUIRED
        with pytest.raises(ValueError, match="Required parameter 'fast_period' missing"):
            validate_parameters(config, sample_schema, strict=True)

    def test_missing_required_raises_even_non_strict(self, sample_schema):
        """Missing required raises even in non-strict mode."""
        config = {}
        with pytest.raises(ValueError, match="Required parameter 'fast_period' missing"):
            validate_parameters(config, sample_schema, strict=False)


# ============================================
# Type Errors (Always Strict)
# ============================================

class TestTypeErrors:
    """Type errors must always raise, regardless of strict flag."""

    def test_string_for_int_raises(self, sample_schema):
        """String where int expected."""
        config = {'fast_period': "twelve"}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=True)

    def test_string_for_int_raises_non_strict(self, sample_schema):
        """Type errors raise even in non-strict mode."""
        config = {'fast_period': "twelve"}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=False)

    def test_float_for_int_raises(self, sample_schema):
        """Float where int expected (3.14 is not a valid int)."""
        config = {'fast_period': 3.14}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=True)

    def test_string_for_float_raises(self, sample_schema):
        """String where float expected."""
        config = {'fast_period': 12, 'deviation': "two"}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=True)

    def test_int_for_bool_raises(self, sample_schema):
        """int where bool expected must raise.

        Python subclass relationship is one-directional:
        - isinstance(True, int) → True  (every bool IS an int)
        - isinstance(1, bool) → False   (but NOT every int is a bool)

        In config context, enabled: 1 is NOT the same as enabled: true.
        The validator correctly rejects this.
        """
        config = {'fast_period': 12, 'enabled': 1}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=True)

    def test_string_for_bool_raises(self, sample_schema):
        """String where bool expected."""
        config = {'fast_period': 12, 'enabled': "true"}
        with pytest.raises(ValueError, match="wrong type"):
            validate_parameters(config, sample_schema, strict=True)


# ============================================
# Boundary Violations - Strict Mode
# ============================================

class TestBoundaryStrict:
    """Boundary violations must raise in strict mode."""

    def test_below_min_raises(self, sample_schema):
        """Value below min_val raises."""
        config = {'fast_period': 0}  # min_val=1
        with pytest.raises(ValueError, match="below minimum"):
            validate_parameters(config, sample_schema, strict=True)

    def test_above_max_raises(self, sample_schema):
        """Value above max_val raises."""
        config = {'fast_period': 201}  # max_val=200
        with pytest.raises(ValueError, match="above maximum"):
            validate_parameters(config, sample_schema, strict=True)

    def test_float_below_min_raises(self, sample_schema):
        """Float value below min raises."""
        config = {'fast_period': 12, 'deviation': 0.1}  # min_val=0.5
        with pytest.raises(ValueError, match="below minimum"):
            validate_parameters(config, sample_schema, strict=True)

    def test_float_above_max_raises(self, sample_schema):
        """Float value above max raises."""
        config = {'fast_period': 12, 'deviation': 10.0}  # max_val=5.0
        with pytest.raises(ValueError, match="above maximum"):
            validate_parameters(config, sample_schema, strict=True)

    def test_the_envelope_bug(self, sample_schema):
        """THE BUG: deviation=0.02 must be caught (below min 0.5)."""
        config = {'fast_period': 12, 'deviation': 0.02}
        with pytest.raises(ValueError, match="below minimum"):
            validate_parameters(config, sample_schema, strict=True)


# ============================================
# Boundary Violations - Non-Strict Mode
# ============================================

class TestBoundaryNonStrict:
    """Boundary violations return warnings in non-strict mode."""

    def test_below_min_warns(self, sample_schema):
        """Value below min returns warning, does not raise."""
        config = {'fast_period': 0}
        warnings = validate_parameters(config, sample_schema, strict=False)
        assert len(warnings) == 1
        assert "below minimum" in warnings[0]

    def test_above_max_warns(self, sample_schema):
        """Value above max returns warning, does not raise."""
        config = {'fast_period': 201}
        warnings = validate_parameters(config, sample_schema, strict=False)
        assert len(warnings) == 1
        assert "above maximum" in warnings[0]

    def test_multiple_violations_all_warned(self, sample_schema):
        """Multiple violations each produce a warning."""
        config = {'fast_period': 0, 'deviation': 99.0}
        warnings = validate_parameters(config, sample_schema, strict=False)
        assert len(warnings) == 2


# ============================================
# Choices Validation
# ============================================

class TestChoicesValidation:
    """Validate choices enforcement."""

    def test_invalid_choice_strict_raises(self, sample_schema):
        """Value not in choices raises in strict mode."""
        config = {'fast_period': 12, 'mode': "turbo"}
        with pytest.raises(ValueError, match="not in allowed choices"):
            validate_parameters(config, sample_schema, strict=True)

    def test_invalid_choice_non_strict_warns(self, sample_schema):
        """Value not in choices warns in non-strict mode."""
        config = {'fast_period': 12, 'mode': "turbo"}
        warnings = validate_parameters(config, sample_schema, strict=False)
        assert len(warnings) == 1
        assert "not in allowed choices" in warnings[0]


# ============================================
# Context Name in Error Messages
# ============================================

class TestContextName:
    """Verify context_name appears in error messages."""

    def test_context_name_in_missing_required(self, sample_schema):
        """Worker/Logic name should appear in error for missing required."""
        with pytest.raises(ValueError, match="'EnvelopeWorker'"):
            validate_parameters(
                {}, sample_schema, strict=True, context_name="EnvelopeWorker"
            )

    def test_context_name_in_type_error(self, sample_schema):
        """Worker/Logic name should appear in type error."""
        with pytest.raises(ValueError, match="'MACDWorker'"):
            validate_parameters(
                {'fast_period': "bad"}, sample_schema,
                strict=True, context_name="MACDWorker"
            )
