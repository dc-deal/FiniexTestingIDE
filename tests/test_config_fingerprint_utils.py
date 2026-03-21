"""
Config Fingerprint Utility Tests
==================================
Tests for SHA256-based config fingerprinting.
"""

from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint


class TestGenerateConfigFingerprint:
    """Tests for generate_config_fingerprint()."""

    def test_deterministic_output(self):
        """Same input produces same fingerprint."""
        section = {'timeframe': 'M5', 'atr_period': 14}
        fp1 = generate_config_fingerprint(section)
        fp2 = generate_config_fingerprint(section)
        assert fp1 == fp2

    def test_different_input_different_fingerprint(self):
        """Different input produces different fingerprint."""
        section_a = {'timeframe': 'M5', 'atr_period': 14}
        section_b = {'timeframe': 'M5', 'atr_period': 20}
        assert generate_config_fingerprint(section_a) != generate_config_fingerprint(section_b)

    def test_key_ordering_irrelevant(self):
        """Dict key ordering does not affect fingerprint."""
        section_a = {'atr_period': 14, 'timeframe': 'M5'}
        section_b = {'timeframe': 'M5', 'atr_period': 14}
        assert generate_config_fingerprint(section_a) == generate_config_fingerprint(section_b)

    def test_empty_dict(self):
        """Empty dict produces a valid fingerprint."""
        fp = generate_config_fingerprint({})
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA256 hex length

    def test_nested_dict_ordering(self):
        """Nested dicts are also order-independent."""
        section_a = {'outer': {'b': 2, 'a': 1}}
        section_b = {'outer': {'a': 1, 'b': 2}}
        assert generate_config_fingerprint(section_a) == generate_config_fingerprint(section_b)

    def test_returns_hex_string(self):
        """Fingerprint is a valid hex string of correct length."""
        fp = generate_config_fingerprint({'key': 'value'})
        assert len(fp) == 64
        int(fp, 16)  # Raises ValueError if not valid hex
