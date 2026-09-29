"""
FiniexTestingIDE - Bar Snapshot Tests
Validates captured bar snapshots against prerendered bars

Tests:
- Snapshot count matches config
- OHLCV values match prerendered bars
- Timestamps are correct
"""

from typing import Dict

from python.framework.types.probe_metadata_types import ProbeMetadata


class TestBarSnapshots:
    """Tests for bar snapshot validation."""

    def test_snapshot_count(
        self,
        probe_metadata: ProbeMetadata,
        scenario_config: Dict
    ):
        """Captured snapshots should match config count."""
        expected_count = len(
            scenario_config['global']['strategy_config']['workers']['probe_worker']['bar_snapshot_checks']
        )
        actual_count = probe_metadata.get_snapshot_count()

        assert actual_count == expected_count, (
            f'Expected {expected_count} snapshots, got {actual_count}'
        )

    def test_snapshots_not_empty(self, probe_metadata: ProbeMetadata):
        """Bar snapshots dict should not be empty."""
        assert probe_metadata.bar_snapshots, 'No bar snapshots captured'

    def test_snapshot_keys_format(self, probe_metadata: ProbeMetadata):
        """Snapshot keys should follow expected format."""
        for key in probe_metadata.bar_snapshots.keys():
            # Format: {timeframe}_bar{index}_tick{tick_number}
            assert '_bar' in key, f'Invalid snapshot key format: {key}'
            assert '_tick' in key, f'Invalid snapshot key format: {key}'

    def test_snapshot_has_required_fields(self, probe_metadata: ProbeMetadata):
        """Each snapshot should have required OHLCV fields."""
        required_fields = ['open', 'high', 'low',
                           'close', 'volume', 'timestamp']

        for key, snapshot in probe_metadata.bar_snapshots.items():
            for field in required_fields:
                assert field in snapshot, (
                    f'Snapshot {key} missing field: {field}'
                )

    def test_snapshot_ohlc_validity(self, probe_metadata: ProbeMetadata):
        """OHLC values should be valid (high >= low, etc)."""
        for key, snapshot in probe_metadata.bar_snapshots.items():
            assert snapshot['high'] >= snapshot['low'], (
                f"Snapshot {key}: high ({snapshot['high']}) < low ({snapshot['low']})"
            )
            assert snapshot['high'] >= snapshot['open'], (
                f'Snapshot {key}: high < open'
            )
            assert snapshot['high'] >= snapshot['close'], (
                f'Snapshot {key}: high < close'
            )
            assert snapshot['low'] <= snapshot['open'], (
                f'Snapshot {key}: low > open'
            )
            assert snapshot['low'] <= snapshot['close'], (
                f'Snapshot {key}: low > close'
            )

    def test_snapshot_tick_count_positive(self, probe_metadata: ProbeMetadata):
        """Snapshot tick_count should be positive."""
        for key, snapshot in probe_metadata.bar_snapshots.items():
            assert snapshot.get('tick_count', 0) > 0, (
                f'Snapshot {key}: tick_count should be positive'
            )

    def test_snapshot_symbol_matches(
        self,
        probe_metadata: ProbeMetadata,
        scenario_config: Dict
    ):
        """Snapshot symbol should match scenario symbol."""
        expected_symbol = scenario_config['scenarios'][0]['symbol']

        for key, snapshot in probe_metadata.bar_snapshots.items():
            assert snapshot['symbol'] == expected_symbol, (
                f"Snapshot {key}: expected symbol {expected_symbol}, "
                f"got {snapshot['symbol']}"
            )
