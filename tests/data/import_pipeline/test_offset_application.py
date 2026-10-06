"""
Test Server Clock Conversion at Import.

The importer turns a venue's own timestamps into UTC through the broker's server clock rule in
market_config.json. The MT5 server runs on New York close time — New York plus seven hours — so
the same rule takes two hours off a tick recorded in US winter and three off one recorded in
summer. A fixed three hours used to stand here, which stored every winter tick one hour early.
"""

from typing import Any, Dict, List

import pandas as pd
import pyarrow.parquet as pq

from python.data_management.importers.tick_data_importer import TickDataImporter
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


def _tick(timestamp: str, session: str = 'london') -> Dict[str, Any]:
    """
    One raw tick in the collector's shape, stamped in the server's wall clock.

    Args:
        timestamp: Server wall-clock time, 'YYYY.MM.DD HH:MM:SS'
        session: Session label the collector wrote

    Returns:
        Tick dict without time_msc — the conversion then reads the timestamp alone
    """
    return {'timestamp': timestamp, 'bid': 1.1, 'ask': 1.1001, 'last': 1.1,
            'tick_volume': 0, 'real_volume': 0.0, 'chart_tick_volume': 1,
            'spread_points': 1, 'spread_pct': 0.01, 'tick_flags': 'BUY',
            'session': session, 'collected_msc': 0}


def _import(tmp_path, symbol: str, broker_type: str,
            ticks: List[Dict[str, Any]]) -> TickDataImporter:
    """
    Import one file of raw ticks into a scratch archive.

    Args:
        tmp_path: pytest temporary directory
        symbol: Symbol the file is named after
        broker_type: Broker whose server clock applies
        ticks: Raw ticks

    Returns:
        The importer after the run, for its errors and its target directory
    """
    data = build_minimal_tick_json(symbol=symbol, broker_type=broker_type,
                                   tick_count=len(ticks), custom_ticks=ticks)
    write_json_fixture(tmp_path / 'source', f'{symbol}_ticks.json', data)
    importer = TickDataImporter(source_dir=str(tmp_path / 'source'),
                                target_dir=str(tmp_path / 'target'), auto_render_bars=False)
    importer.process_all_exports()
    return importer


def _first_tick(tmp_path) -> pd.Series:
    """
    The first stored tick of the one parquet the scratch import wrote.

    Args:
        tmp_path: pytest temporary directory

    Returns:
        The first row
    """
    return pd.read_parquet(find_tick_parquets(tmp_path / 'target')[0]).iloc[0]


class TestTheRuleNotANumber:
    """One rule, two offsets — the US season decides, not a constant."""

    def test_a_winter_tick_moves_back_two_hours(self, tmp_path):
        """US standard time: the server is UTC+2, so 15:00 on the server is 13:00 UTC."""
        _import(tmp_path, 'EURUSD', 'mt5', [_tick('2026.01.15 15:00:00')])

        assert _first_tick(tmp_path)['timestamp'] == pd.Timestamp('2026-01-15 13:00:00')

    def test_a_summer_tick_moves_back_three_hours(self, tmp_path):
        """US daylight time: the server is UTC+3, so 15:00 on the server is 12:00 UTC."""
        _import(tmp_path, 'EURUSD', 'mt5', [_tick('2026.06.15 15:00:00')])

        assert _first_tick(tmp_path)['timestamp'] == pd.Timestamp('2026-06-15 12:00:00')

    def test_the_payroll_release_lands_on_its_utc_minute(self, tmp_path):
        """
        The anchor that exposed the fixed offset: payroll is released at 08:30 New York.

        On 2026-01-09 that is 13:30 UTC; the archive held the spike at 12:30 until the rule
        replaced the constant.
        """
        _import(tmp_path, 'EURUSD', 'mt5', [_tick('2026.01.09 15:30:00')])

        assert _first_tick(tmp_path)['timestamp'] == pd.Timestamp('2026-01-09 13:30:00')

    def test_kraken_stays_as_it_is(self, tmp_path):
        """Kraken stamps in UTC — its rule is UTC plus nothing."""
        data = build_minimal_tick_json(symbol='BTCUSD', broker_type='kraken_spot', tick_count=3)
        write_json_fixture(tmp_path / 'source', 'BTCUSD_ticks.json', data)
        TickDataImporter(source_dir=str(tmp_path / 'source'),
                         target_dir=str(tmp_path / 'target'),
                         auto_render_bars=False).process_all_exports()

        assert _first_tick(tmp_path)['timestamp'].hour == 10

    def test_time_msc_moves_with_the_timestamp(self, tmp_path):
        """Both columns describe one moment, so both move by the same per-tick amount."""
        data = build_minimal_tick_json(symbol='EURUSD', broker_type='mt5', tick_count=3,
                                       start_time='2026.01.15 15:00:00')
        server_first = data['ticks'][0]['time_msc']
        write_json_fixture(tmp_path / 'source', 'EURUSD_ticks.json', data)
        TickDataImporter(source_dir=str(tmp_path / 'source'),
                         target_dir=str(tmp_path / 'target'),
                         auto_render_bars=False).process_all_exports()

        first = _first_tick(tmp_path)
        assert int(first['time_msc']) == server_first - 2 * 3_600_000
        assert first['timestamp'] == pd.Timestamp('2026-01-15 13:00:00')


class TestTheChangedHour:
    """A daylight saving change repeats or skips an hour; a tick inside it has no UTC time."""

    def test_a_tick_in_the_repeated_hour_refuses_the_file(self, tmp_path):
        """
        New York repeats 01:00-01:59 on 2025-11-02 — 08:00-08:59 on this server.

        Forex is closed then, so the archive has none; a file that does is refused rather
        than placed in either reading of the hour.
        """
        importer = _import(tmp_path, 'EURUSD', 'mt5', [_tick('2025.11.02 08:30:00')])

        assert find_tick_parquets(tmp_path / 'target') == []
        assert any('VALIDATION FAILED' in error for error in importer.errors)


class TestTheHeaderSaysWhatWasApplied:
    """The parquet header names the rule and the offset it resolved to for this file."""

    def test_a_winter_file_states_minus_two(self, tmp_path):
        """The per-file offset is what lets a re-import be counted: N winter files at -2."""
        _import(tmp_path, 'EURUSD', 'mt5', [_tick('2026.01.15 15:00:00')])
        meta = pq.read_schema(find_tick_parquets(tmp_path / 'target')[0]).metadata

        assert meta[b'user_time_offset_hours'] == b'-2'
        assert meta[b'server_clock_rule'] == b'America/New_York+7h'
        assert meta[b'utc_conversion_applied'] == b'true'

    def test_a_summer_file_states_minus_three(self, tmp_path):
        """The same rule, the other season."""
        _import(tmp_path, 'EURUSD', 'mt5', [_tick('2026.06.15 15:00:00')])
        meta = pq.read_schema(find_tick_parquets(tmp_path / 'target')[0]).metadata

        assert meta[b'user_time_offset_hours'] == b'-3'


class TestSessionRecalculation:
    """Sessions are labelled from the UTC hour after the conversion."""

    def test_session_recalculated_after_conversion(self, tmp_path):
        """Server midnight in summer is 21:00 UTC — the transition hour."""
        _import(tmp_path, 'GBPUSD', 'mt5', [_tick('2026.06.15 00:00:00', session='sydney_tokyo')])

        assert _first_tick(tmp_path)['session'] == 'transition'

    def test_the_winter_session_is_an_hour_later(self, tmp_path):
        """Server midnight in winter is 22:00 UTC — already the Asian session, not transition."""
        _import(tmp_path, 'GBPUSD', 'mt5', [_tick('2026.01.15 00:00:00', session='transition')])

        assert _first_tick(tmp_path)['session'] == 'sydney_tokyo'

    def test_session_preserved_when_already_utc(self, tmp_path):
        """Kraken needs no conversion, so the collector's label stands."""
        data = build_minimal_tick_json(symbol='BTCUSD', broker_type='kraken_spot', tick_count=3)
        write_json_fixture(tmp_path / 'source', 'BTCUSD_ticks.json', data)
        TickDataImporter(source_dir=str(tmp_path / 'source'),
                         target_dir=str(tmp_path / 'target'),
                         auto_render_bars=False).process_all_exports()

        assert _first_tick(tmp_path)['session'] == '24h'
