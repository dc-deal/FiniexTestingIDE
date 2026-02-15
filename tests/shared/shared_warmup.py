"""
FiniexTestingIDE - Shared Warmup Validation Tests
Reusable test classes for warmup validation across test suites.

Used by: mvp_baseline, multi_position, margin_validation
Import these classes into suite-specific test_warmup_validation.py files.
"""

import pytest
from python.framework.types.backtesting_metadata_types import BacktestingMetadata


class TestWarmupValidation:
    """Tests for warmup bar validation."""

    def test_no_warmup_errors(self, backtesting_metadata: BacktestingMetadata):
        """Warmup validation should pass with no errors."""
        assert backtesting_metadata.warmup_errors == [], (
            f"Warmup errors detected: {backtesting_metadata.warmup_errors}"
        )

    def test_warmup_errors_list_exists(self, backtesting_metadata: BacktestingMetadata):
        """Warmup errors should be a list."""
        assert isinstance(backtesting_metadata.warmup_errors, list)

    def test_has_warmup_errors_method(self, backtesting_metadata: BacktestingMetadata):
        """has_warmup_errors() should return False when no errors."""
        assert backtesting_metadata.has_warmup_errors() is False
