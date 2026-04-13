"""
Tests for the central tick parquet reader.

Validates column normalization (real_volume → volume) and
graceful handling of missing/legacy columns.
"""

from pathlib import Path

import pandas as pd
import pytest

from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def crypto_parquet(tmp_path: Path) -> Path:
    """Crypto tick parquet with real_volume (Kraken-style)."""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-24', periods=5, freq='s'),
        'time_msc': [1769264386420 + i * 1000 for i in range(5)],
        'bid': [89308.8, 89309.0, 89310.5, 89311.0, 89312.0],
        'ask': [89309.0, 89309.2, 89310.7, 89311.2, 89312.2],
        'real_volume': [0.000567, 0.000321, 0.000239, 0.015726, 0.001000],
        'tick_volume': [0, 0, 0, 0, 0],
        'tick_flags': ['SELL', 'BUY', 'BUY', 'BUY', 'SELL'],
    })
    path = tmp_path / 'crypto_ticks.parquet'
    df.to_parquet(path, index=False)
    return path


@pytest.fixture
def forex_parquet(tmp_path: Path) -> Path:
    """Forex tick parquet with real_volume=0.0 (MT5 CFD-style)."""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2025-09-17', periods=5, freq='s'),
        'time_msc': [1758131914646 + i * 1000 for i in range(5)],
        'bid': [146.254, 146.259, 146.258, 146.259, 146.257],
        'ask': [146.277, 146.282, 146.281, 146.282, 146.280],
        'real_volume': [0.0, 0.0, 0.0, 0.0, 0.0],
        'tick_volume': [0, 0, 0, 0, 0],
    })
    path = tmp_path / 'forex_ticks.parquet'
    df.to_parquet(path, index=False)
    return path


@pytest.fixture
def legacy_parquet(tmp_path: Path) -> Path:
    """Legacy parquet without real_volume or volume columns."""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2025-01-01', periods=3, freq='s'),
        'time_msc': [1000000000000 + i * 1000 for i in range(3)],
        'bid': [1.1000, 1.1001, 1.1002],
        'ask': [1.1002, 1.1003, 1.1004],
    })
    path = tmp_path / 'legacy_ticks.parquet'
    df.to_parquet(path, index=False)
    return path


@pytest.fixture
def already_normalized_parquet(tmp_path: Path) -> Path:
    """Parquet that already has a volume column (no real_volume)."""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=3, freq='s'),
        'time_msc': [1769000000000 + i * 1000 for i in range(3)],
        'bid': [50000.0, 50001.0, 50002.0],
        'ask': [50000.5, 50001.5, 50002.5],
        'volume': [0.05, 0.10, 0.03],
    })
    path = tmp_path / 'normalized_ticks.parquet'
    df.to_parquet(path, index=False)
    return path


# ============================================================================
# Unit Tests — Column Normalization
# ============================================================================


class TestColumnNormalization:
    """Verify real_volume → volume mapping and edge cases."""

    def test_crypto_real_volume_normalized(self, crypto_parquet: Path) -> None:
        """Crypto parquet: real_volume renamed to volume with correct values."""
        df = read_tick_parquet(crypto_parquet)

        assert 'volume' in df.columns
        assert 'real_volume' not in df.columns
        assert df['volume'].iloc[0] == pytest.approx(0.000567)
        assert df['volume'].iloc[3] == pytest.approx(0.015726)

    def test_forex_zero_volume(self, forex_parquet: Path) -> None:
        """Forex parquet: real_volume=0.0 becomes volume=0.0."""
        df = read_tick_parquet(forex_parquet)

        assert 'volume' in df.columns
        assert 'real_volume' not in df.columns
        assert (df['volume'] == 0.0).all()

    def test_legacy_no_volume_column(self, legacy_parquet: Path) -> None:
        """Legacy parquet without any volume field: volume=0.0 added."""
        df = read_tick_parquet(legacy_parquet)

        assert 'volume' in df.columns
        assert (df['volume'] == 0.0).all()

    def test_already_normalized_passthrough(self, already_normalized_parquet: Path) -> None:
        """Parquet with volume column passes through unchanged."""
        df = read_tick_parquet(already_normalized_parquet)

        assert 'volume' in df.columns
        assert df['volume'].iloc[0] == pytest.approx(0.05)
        assert df['volume'].iloc[1] == pytest.approx(0.10)

    def test_raw_columns_preserved(self, crypto_parquet: Path) -> None:
        """Other raw columns survive normalization."""
        df = read_tick_parquet(crypto_parquet)

        assert 'bid' in df.columns
        assert 'ask' in df.columns
        assert 'tick_volume' in df.columns
        assert 'tick_flags' in df.columns
        assert 'time_msc' in df.columns


# ============================================================================
# Integration Test — Full Volume Chain
# ============================================================================


class TestVolumeChain:
    """End-to-end: parquet → read_tick_parquet → VectorizedBarRenderer → bar.volume > 0."""

    def test_volume_chain_parquet_to_bar(self, tmp_path: Path) -> None:
        """The exact bug path: volume must survive from parquet to rendered bars."""
        from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer

        # Create realistic crypto tick data spanning 10 minutes
        timestamps = pd.date_range('2026-01-24 14:00:00', periods=100, freq='6s', tz='UTC')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'time_msc': [int(t.timestamp() * 1000) for t in timestamps],
            'bid': [89300.0 + i * 0.5 for i in range(100)],
            'ask': [89300.5 + i * 0.5 for i in range(100)],
            'real_volume': [0.001 + i * 0.0001 for i in range(100)],
            'tick_volume': [0] * 100,
        })
        path = tmp_path / 'chain_test.parquet'
        df.to_parquet(path, index=False)

        # Read with central reader
        normalized_df = read_tick_parquet(path)
        assert 'volume' in normalized_df.columns
        assert 'real_volume' not in normalized_df.columns

        # Render bars (renders all timeframes, we check M5)
        renderer = VectorizedBarRenderer('BTCUSD', 'kraken_spot')
        bar_dfs = renderer.render_all_timeframes(normalized_df)

        assert 'M5' in bar_dfs
        m5_bars = bar_dfs['M5']
        assert len(m5_bars) > 0

        # The critical assertion: bar volume must be > 0
        total_volume = m5_bars['volume'].sum()
        assert total_volume > 0, (
            f"Bar volume should be > 0 for crypto data, got {total_volume}"
        )

        # Volume should match sum of tick volumes
        expected_total = normalized_df['volume'].sum()
        assert total_volume == pytest.approx(expected_total, rel=1e-6)
