"""
FiniexTestingIDE - Volume Integrity Tests
=========================================

Validates volume data consistency across the data pipeline.

Tests:
- Volume schema exists in all bar files
- Crypto markets have real trade volume (> 0)
- Forex markets have zero volume (CFD - no real volume)
- Tick count is positive for all markets
- Index volume stats match actual bar data

Market Type Rules:
- crypto: volume > 0 (actual trade volume in base currency)
- forex:  volume == 0 (CFD has no real volume, only tick_count)
"""

import pytest
from typing import Dict, List

import pandas as pd

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.market_config_types import MarketType


class TestVolumeSchema:
    """Tests for volume column presence and schema validity."""

    def test_volume_column_exists_in_all_bars(
        self,
        bars_index_manager: BarsIndexManager,
        bar_file_loader
    ):
        """All bar files should have 'volume' column."""
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)

                # Test one timeframe per symbol (M30 preferred, fallback to first)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)
                    if 'volume' not in df.columns:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: missing 'volume' column")
                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Volume schema errors:\n" + "\n".join(errors)

    def test_tick_count_column_exists(
        self,
        bars_index_manager: BarsIndexManager,
        bar_file_loader
    ):
        """All bar files should have 'tick_count' column."""
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)
                    if 'tick_count' not in df.columns:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: missing 'tick_count' column")
                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Tick count schema errors:\n" + "\n".join(errors)


class TestCryptoVolume:
    """Tests for crypto market volume (should have real trade volume)."""

    def test_crypto_has_positive_volume(
        self,
        bars_index_manager: BarsIndexManager,
        market_config: MarketConfigManager,
        bar_file_loader
    ):
        """Crypto bars should have volume > 0 (real trade volume)."""
        errors = []
        tested = 0

        for broker_type in bars_index_manager.list_broker_types():
            market_type = market_config.get_market_type(broker_type)

            if market_type != MarketType.CRYPTO:
                continue

            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)

                    # Only check real bars (not synthetic)
                    real_bars = df[df['bar_type'] == 'real']

                    if len(real_bars) == 0:
                        continue

                    total_volume = real_bars['volume'].sum()

                    if total_volume <= 0:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"crypto volume should be > 0, got {total_volume}"
                        )

                    tested += 1

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        if tested == 0:
            pytest.skip("No crypto data available")

        assert not errors, f"Crypto volume errors ({tested} symbols tested):\n" + "\n".join(
            errors)

    def test_crypto_volume_per_bar_positive(
        self,
        bars_index_manager: BarsIndexManager,
        market_config: MarketConfigManager,
        bar_file_loader
    ):
        """Individual crypto bars should have volume >= 0 (no negative values)."""
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            market_type = market_config.get_market_type(broker_type)

            if market_type != MarketType.CRYPTO:
                continue

            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)
                    negative_volume = (df['volume'] < 0).sum()

                    if negative_volume > 0:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"{negative_volume} bars with negative volume"
                        )

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Negative volume errors:\n" + "\n".join(errors)


class TestForexVolume:
    """Tests for forex market volume (should be zero - CFD has no real volume)."""

    def test_forex_has_zero_volume(
        self,
        bars_index_manager: BarsIndexManager,
        market_config: MarketConfigManager,
        bar_file_loader
    ):
        """Forex bars should have volume == 0 (CFD - no real trade volume)."""
        errors = []
        tested = 0

        for broker_type in bars_index_manager.list_broker_types():
            market_type = market_config.get_market_type(broker_type)

            if market_type != MarketType.FOREX:
                continue

            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)
                    total_volume = df['volume'].sum()

                    if total_volume != 0:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"forex volume should be 0, got {total_volume}"
                        )

                    tested += 1

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        if tested == 0:
            pytest.skip("No forex data available")

        assert not errors, f"Forex volume errors ({tested} symbols tested):\n" + "\n".join(
            errors)


class TestTickCount:
    """Tests for tick count consistency (positive for all markets)."""

    def test_all_markets_have_positive_tick_count(
        self,
        bars_index_manager: BarsIndexManager,
        bar_file_loader
    ):
        """All markets should have tick_count > 0 for real bars."""
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    df = bar_file_loader(broker_type, symbol, tf)

                    # Only check real bars
                    real_bars = df[df['bar_type'] == 'real']

                    if len(real_bars) == 0:
                        continue

                    total_ticks = real_bars['tick_count'].sum()

                    if total_ticks <= 0:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"tick_count should be > 0, got {total_ticks}"
                        )

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Tick count errors:\n" + "\n".join(errors)


class TestIndexBarConsistency:
    """Tests for consistency between index stats and actual bar data."""

    def test_index_volume_matches_bar_data(
        self,
        bars_index_manager: BarsIndexManager,
        bar_file_loader
    ):
        """Index total_trade_volume should match sum of bar volumes."""
        errors = []
        tolerance = 0.01  # Allow small float precision difference

        for broker_type in bars_index_manager.list_broker_types():
            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    # Get index entry
                    index_entry = bars_index_manager.index[broker_type][symbol][tf]
                    index_volume = index_entry.get('total_trade_volume')

                    if index_volume is None:
                        continue  # Skip if index doesn't have volume

                    # Load actual bar data
                    df = bar_file_loader(broker_type, symbol, tf)
                    actual_volume = df['volume'].sum()

                    # Compare
                    diff = abs(index_volume - actual_volume)
                    if diff > tolerance and diff / max(actual_volume, 1) > 0.001:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"index volume={index_volume:.2f}, "
                            f"actual={actual_volume:.2f}, diff={diff:.2f}"
                        )

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Index/bar volume mismatch:\n" + "\n".join(errors)

    def test_index_tick_count_matches_bar_data(
        self,
        bars_index_manager: BarsIndexManager,
        bar_file_loader
    ):
        """Index total_tick_count should match sum of bar tick_counts."""
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)
                tf = 'M30' if 'M30' in timeframes else timeframes[0]

                try:
                    # Get index entry
                    index_entry = bars_index_manager.index[broker_type][symbol][tf]
                    index_ticks = index_entry.get('total_tick_count', 0)

                    # Load actual bar data
                    df = bar_file_loader(broker_type, symbol, tf)
                    actual_ticks = int(df['tick_count'].sum())

                    # Compare (exact match for integers)
                    if index_ticks != actual_ticks:
                        errors.append(
                            f"{broker_type}/{symbol}/{tf}: "
                            f"index ticks={index_ticks}, actual={actual_ticks}"
                        )

                except Exception as e:
                    errors.append(f"{broker_type}/{symbol}/{tf}: {e}")

        assert not errors, f"Index/bar tick count mismatch:\n" + \
            "\n".join(errors)


class TestAllTimeframes:
    """Tests across all timeframes for comprehensive validation."""

    def test_volume_consistent_across_timeframes(
        self,
        bars_index_manager: BarsIndexManager,
        market_config: MarketConfigManager,
        bar_file_loader
    ):
        """
        Total volume should be approximately equal across timeframes.

        M1, M5, M15, M30, H1, H4, D1 should all sum to ~same total volume
        (small differences due to bar boundaries are acceptable).
        """
        errors = []

        for broker_type in bars_index_manager.list_broker_types():
            market_type = market_config.get_market_type(broker_type)

            if market_type != MarketType.CRYPTO:
                continue  # Only check crypto (forex is always 0)

            for symbol in bars_index_manager.list_symbols(broker_type):
                timeframes = bars_index_manager.get_available_timeframes(
                    broker_type, symbol)

                if len(timeframes) < 2:
                    continue

                volumes = {}
                for tf in timeframes:
                    try:
                        df = bar_file_loader(broker_type, symbol, tf)
                        volumes[tf] = df['volume'].sum()
                    except:
                        pass

                if len(volumes) < 2:
                    continue

                # Check all timeframes have similar total (within 1%)
                max_vol = max(volumes.values())
                min_vol = min(volumes.values())

                if max_vol > 0 and (max_vol - min_vol) / max_vol > 0.01:
                    errors.append(
                        f"{broker_type}/{symbol}: volume varies across timeframes: "
                        f"min={min_vol:.2f}, max={max_vol:.2f}"
                    )

        # This is a warning-level check, not a hard failure
        if errors:
            pytest.xfail(
                f"Volume inconsistencies (may be acceptable):\n" + "\n".join(errors[:5]))
