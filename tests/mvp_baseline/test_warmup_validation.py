"""
FiniexTestingIDE - Warmup Validation Tests
Validates warmup bar loading and counting

Tests:
- Warmup errors should be empty
- Bar counts match config requirements
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
