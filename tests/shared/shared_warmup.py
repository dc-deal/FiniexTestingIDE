"""
FiniexTestingIDE - Shared Warmup Validation Tests
Reusable test classes for warmup validation across test suites.

Used by: baseline, multi_position, margin_validation
Import these classes into suite-specific test_<suite>_warmup_validation.py files.
"""


from python.framework.types.probe_metadata_types import ProbeMetadata


class TestWarmupValidation:
    """Tests for warmup bar validation."""

    def test_no_warmup_errors(self, probe_metadata: ProbeMetadata):
        """Warmup validation should pass with no errors."""
        assert probe_metadata.warmup_errors == [], (
            f'Warmup errors detected: {probe_metadata.warmup_errors}'
        )

    def test_warmup_errors_list_exists(self, probe_metadata: ProbeMetadata):
        """Warmup errors should be a list."""
        assert isinstance(probe_metadata.warmup_errors, list)

    def test_has_warmup_errors_method(self, probe_metadata: ProbeMetadata):
        """has_warmup_errors() should return False when no errors."""
        assert probe_metadata.has_warmup_errors() is False
