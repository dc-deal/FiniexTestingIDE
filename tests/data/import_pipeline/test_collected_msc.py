"""
Test collected_msc Import Handling.

Tests for the collected_msc field introduced in data format V1.3.0:
- Presence in Parquet output
- Correct dtype (int64)
- Backward compatibility (default 0 for old data)
- Preservation of values from V1.3.0 data
- time_msc offset consistency
"""

import pandas as pd

from python.data_management.importers.tick_data_importer import TickDataImporter
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestCollectedMscPresence:
    """Verify collected_msc column exists in Parquet output."""

    def test_collected_msc_present_in_parquet(self, tmp_path):
        """Parquet output should contain collected_msc column."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='BTCUSD', broker_type='kraken_spot', tick_count=3)
        write_json_fixture(source, 'BTCUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert 'collected_msc' in df.columns

    def test_collected_msc_dtype_int64(self, tmp_path):
        """collected_msc should be int64 in Parquet."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='ETHUSD', broker_type='kraken_spot', tick_count=3)
        write_json_fixture(source, 'ETHUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert df['collected_msc'].dtype == 'int64'


class TestCollectedMscBackwardCompat:
    """Verify backward compatibility for data without collected_msc."""

    def test_missing_collected_msc_defaults_to_zero(self, tmp_path):
        """Old JSON without collected_msc should import with default 0."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        # Simulate pre-V1.3.0 data: no collected_msc in ticks
        data = build_minimal_tick_json(
            symbol='ADAUSD', broker_type='kraken_spot',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 10:00:00', 'bid': 0.5, 'ask': 0.5001,
                 'last': 0.5, 'tick_volume': 0, 'real_volume': 100.0,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': 1768471200000},
                {'timestamp': '2026.01.15 10:00:01', 'bid': 0.5001, 'ask': 0.5002,
                 'last': 0.5001, 'tick_volume': 0, 'real_volume': 101.0,
                 'chart_tick_volume': 2, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': 1768471201000},
            ],
        )
        write_json_fixture(source, 'ADAUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert 'collected_msc' in df.columns
        assert (df['collected_msc'] == 0).all()


class TestCollectedMscValues:
    """Verify collected_msc values are preserved correctly."""

    def test_collected_msc_values_preserved(self, tmp_path):
        """V1.3.0 data with collected_msc should preserve values in Parquet."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        expected_values = [1768471200100, 1768471201200, 1768471202300]
        data = build_minimal_tick_json(
            symbol='SOLUSD', broker_type='kraken_spot',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 10:00:00', 'bid': 20.0, 'ask': 20.01,
                 'last': 20.0, 'tick_volume': 0, 'real_volume': 1.0,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': 1768471200000, 'collected_msc': expected_values[0]},
                {'timestamp': '2026.01.15 10:00:01', 'bid': 20.01, 'ask': 20.02,
                 'last': 20.01, 'tick_volume': 0, 'real_volume': 1.1,
                 'chart_tick_volume': 2, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': 1768471201000, 'collected_msc': expected_values[1]},
                {'timestamp': '2026.01.15 10:00:02', 'bid': 20.02, 'ask': 20.03,
                 'last': 20.02, 'tick_volume': 0, 'real_volume': 1.2,
                 'chart_tick_volume': 3, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': 1768471202000, 'collected_msc': expected_values[2]},
            ],
        )
        write_json_fixture(source, 'SOLUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert list(df['collected_msc']) == expected_values

    def test_collected_msc_not_affected_by_offset(self, tmp_path):
        """collected_msc should NOT be shifted by time offset."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        # collected_msc is UTC; time_msc 1768489200000 is broker time (GMT+3)
        original_collected = 1768478400500
        data = build_minimal_tick_json(
            symbol='EURUSD', broker_type='mt5',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 15:00:00', 'bid': 1.1, 'ask': 1.1001,
                 'last': 1.1, 'tick_volume': 0, 'real_volume': 0.0,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': 'london',
                 'time_msc': 1768489200000, 'collected_msc': original_collected},
            ],
        )
        write_json_fixture(source, 'EURUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            offset_registry={'mt5': -3}, auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        # collected_msc must remain unchanged despite -3h offset
        assert df['collected_msc'].iloc[0] == original_collected


class TestTimeMscOffset:
    """Verify time_msc is offset-corrected consistently with timestamp."""

    def test_time_msc_shifted_with_offset(self, tmp_path):
        """time_msc should be shifted by the same offset as timestamp."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        original_time_msc = 1768489200000
        data = build_minimal_tick_json(
            symbol='USDJPY', broker_type='mt5',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 15:00:00', 'bid': 150.0, 'ask': 150.01,
                 'last': 150.0, 'tick_volume': 0, 'real_volume': 0.0,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': 'new_york',
                 'time_msc': original_time_msc, 'collected_msc': 0},
            ],
        )
        write_json_fixture(source, 'USDJPY_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            offset_registry={'mt5': -3}, auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        expected_time_msc = original_time_msc + (-3 * 3_600_000)
        assert df['time_msc'].iloc[0] == expected_time_msc

    def test_time_msc_not_shifted_without_offset(self, tmp_path):
        """time_msc should remain unchanged when no offset is applied."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        original_time_msc = 1768489200000
        data = build_minimal_tick_json(
            symbol='BTCUSD', broker_type='kraken_spot',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 15:00:00', 'bid': 90000.0, 'ask': 90001.0,
                 'last': 90000.0, 'tick_volume': 0, 'real_volume': 0.001,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': '24h',
                 'time_msc': original_time_msc, 'collected_msc': 0},
            ],
        )
        write_json_fixture(source, 'BTCUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            offset_registry={'kraken_spot': 0}, auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert df['time_msc'].iloc[0] == original_time_msc

    def test_timestamp_and_time_msc_consistent_after_offset(self, tmp_path):
        """After offset, timestamp and time_msc should represent the same UTC moment."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        # 15:00:00 broker time (GMT+3) = 12:00:00 UTC
        # time_msc must match the timestamp string for consistency check
        broker_time_msc = 1768489200000  # epoch ms for 2026-01-15 15:00:00
        data = build_minimal_tick_json(
            symbol='GBPUSD', broker_type='mt5',
            tick_count=0,
            custom_ticks=[
                {'timestamp': '2026.01.15 15:00:00', 'bid': 1.25, 'ask': 1.2501,
                 'last': 1.25, 'tick_volume': 0, 'real_volume': 0.0,
                 'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
                 'tick_flags': 'BUY', 'session': 'london',
                 'time_msc': broker_time_msc, 'collected_msc': 0},
            ],
        )
        write_json_fixture(source, 'GBPUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            offset_registry={'mt5': -3}, auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])

        # Both should now be offset by -3h
        ts_epoch_ms = int(df['timestamp'].iloc[0].timestamp() * 1000)
        time_msc = df['time_msc'].iloc[0]

        # time_msc has ms precision, timestamp only seconds
        # They should agree at the second level
        assert abs(ts_epoch_ms - time_msc) < 1000


class TestTickOrderPreservation:
    """Verify importer preserves JSON array order (no sorting)."""

    def test_tick_order_matches_json_array_order(self, tmp_path):
        """Parquet row order must match JSON array order, not any re-sort."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        # A burst: three ticks in the same broker millisecond, distinguishable
        # only by their arrival stamp. Their JSON order IS the arrival order —
        # any re-sort of the frame would lose it.
        shared_time_msc = 1768471200000
        ticks = [
            {'timestamp': '2026.01.15 10:00:00', 'bid': 1.0, 'ask': 1.001,
             'last': 1.0, 'tick_volume': 0, 'real_volume': 10.0,
             'chart_tick_volume': 1, 'spread_points': 1, 'spread_pct': 0.01,
             'tick_flags': 'BID ASK', 'session': '24h',
             'time_msc': shared_time_msc, 'collected_msc': shared_time_msc + 3},
            {'timestamp': '2026.01.15 10:00:00', 'bid': 2.0, 'ask': 2.001,
             'last': 2.0, 'tick_volume': 0, 'real_volume': 20.0,
             'chart_tick_volume': 2, 'spread_points': 1, 'spread_pct': 0.01,
             'tick_flags': 'BID ASK', 'session': '24h',
             'time_msc': shared_time_msc, 'collected_msc': shared_time_msc + 4},
            {'timestamp': '2026.01.15 10:00:00', 'bid': 3.0, 'ask': 3.001,
             'last': 3.0, 'tick_volume': 0, 'real_volume': 30.0,
             'chart_tick_volume': 3, 'spread_points': 1, 'spread_pct': 0.01,
             'tick_flags': 'BID ASK', 'session': '24h',
             'time_msc': shared_time_msc, 'collected_msc': shared_time_msc + 5},
        ]
        data = build_minimal_tick_json(
            symbol='XRPUSD', broker_type='kraken_spot',
            tick_count=0, custom_ticks=ticks)
        write_json_fixture(source, 'XRPUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])

        # Row order matches JSON array order (bid values as markers)
        assert list(df['bid']) == [1.0, 2.0, 3.0]
        # Equal event times, distinct arrival stamps — the burst survived intact
        assert list(df['time_msc']) == [shared_time_msc] * 3
        assert list(df['collected_msc']) == [
            shared_time_msc + 3, shared_time_msc + 4, shared_time_msc + 5]

    def test_collected_msc_monotonicity_preserved(self, tmp_path):
        """collected_msc must stay non-decreasing through the import pipeline."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        base = 1768471200000
        ticks = []
        for i in range(5):
            ticks.append({
                'timestamp': '2026.01.15 10:00:00',
                'bid': 100.0 + i * 0.01, 'ask': 100.01 + i * 0.01,
                'last': 100.0 + i * 0.01, 'tick_volume': 0,
                'real_volume': 1.0 + i * 0.1, 'chart_tick_volume': i + 1,
                'spread_points': 1, 'spread_pct': 0.01,
                'tick_flags': 'BID ASK', 'session': '24h',
                'time_msc': base + i * 100,
                'collected_msc': base + i * 100 + 20,
            })
        data = build_minimal_tick_json(
            symbol='DASHUSD', broker_type='kraken_spot',
            tick_count=0, custom_ticks=ticks)
        write_json_fixture(source, 'DASHUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        collected = list(df['collected_msc'])

        for i in range(1, len(collected)):
            assert collected[i] >= collected[i - 1], (
                f'collected_msc not monotonic at index {i}: '
                f'{collected[i - 1]} -> {collected[i]}'
            )


class TestServerTimeRemoved:
    """Verify server_time is no longer in new imports."""

    def test_no_server_time_in_new_parquet(self, tmp_path):
        """New imports should not contain server_time column."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='LTCUSD', broker_type='kraken_spot', tick_count=3)
        write_json_fixture(source, 'LTCUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source), target_dir=str(target),
            auto_render_bars=False)
        importer.process_all_exports()

        df = pd.read_parquet(find_tick_parquets(target)[0])
        assert 'server_time' not in df.columns
