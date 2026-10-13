"""
Test the Kraken Trade History Converter (#561).

Every test builds its own small Kraken archive in a temporary folder — a zip with Kraken's
manifest and a few pair files, cut into byte-range parts with a checksum list, exactly as Kraken
publishes the real one. Nothing binary is committed and no real archive is read.
"""

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pyarrow.parquet as pq
import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.external.kraken_history_commands import (
    KrakenHistoryCommands,
)
from python.data_management.importers.external.kraken_trade_history_archive import (
    CHECKSUMS_FILE,
    EXPECTED_COLUMNS,
    KrakenTradeHistoryArchive,
)
from python.data_management.importers.external.kraken_trade_history_converter import (
    CONVERTER_IDENTITY,
    KrakenTradeHistoryConverter,
)
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.exceptions.external_history_errors import ExternalArchiveError
from python.framework.types.data_deficiency_types import DataDeficiency
from tests.data.import_pipeline.conftest import find_tick_parquets

_HEADER = ','.join(EXPECTED_COLUMNS)
# Kraken's first BTCUSD trade, and two more a little later
_XBT_ROWS = [
    '1381095256.0907948,122.00000,0.10000000,s,l,,1',
    '1381095300.5000000,123.50000,0.20000000,b,m,,2',
    '1381096000.0000000,124.00000,0.30000000,b,l,,3',
]


def _manifest(schema: int = 2) -> Dict:
    return {'product': 'trades', 'schema_version': schema, 'release': 'r1',
            'columns': EXPECTED_COLUMNS, 'has_header': True,
            'coverage': {'start': '2013-09-11', 'end': '2026-09-30'}}


def _fetch(tmp_path: Path, members: Dict[str, List[str]], schema: int = 2,
           header: str = _HEADER, parts: int = 3) -> Path:
    """
    Build a fetch folder: the zip cut into byte-range parts, and Kraken's checksum list.

    Args:
        tmp_path: pytest's directory
        members: Pair file name → data rows
        schema: The vendor manifest's schema version
        header: The header row every member carries
        parts: How many parts to cut the zip into

    Returns:
        The fetch folder
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('MANIFEST.json', json.dumps(_manifest(schema)))
        for name, rows in members.items():
            archive.writestr(name, '\n'.join([header] + rows) + '\n')
    data = buffer.getvalue()

    fetch = tmp_path / 'fetch'
    fetch.mkdir()
    step = len(data) // parts + 1
    lines = []
    for index in range(parts):
        chunk = data[index * step:(index + 1) * step]
        name = f'Kraken_Trades_Full.zip.part{index:02d}'
        (fetch / name).write_bytes(chunk)
        lines.append(f'{hashlib.sha256(chunk).hexdigest()}  {name}')
    (fetch / CHECKSUMS_FILE).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return fetch


def _convert(fetch: Path, raw: Path, cut_at=None, cap: int = 500_000, base: str = 'BTC',
             symbol: str = 'BTCUSD'):
    archive = KrakenTradeHistoryArchive(fetch)
    archive.verify()
    return KrakenTradeHistoryConverter(archive, raw, max_ticks_per_file=cap).convert(
        symbol, base, 'USD', cut_at)


def _ticks(raw: Path) -> List[Dict]:
    return [tick for path in sorted(raw.glob('*_ticks.json'))
            for tick in json.loads(path.read_text())['ticks']]


class TestTheArchiveIsCheckedBeforeItIsRead:

    def test_verified_parts_are_recorded_with_their_checksums(self, tmp_path):
        manifest = KrakenTradeHistoryArchive(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})).verify()
        assert len(manifest.parts) == 3
        assert all(part.matches_published() for part in manifest.parts)
        assert manifest.vendor_manifest['schema_version'] == 2

    def test_a_part_that_differs_from_its_checksum_is_refused(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})
        part = fetch / 'Kraken_Trades_Full.zip.part01'
        part.write_bytes(part.read_bytes()[:-1] + b'\x00')
        with pytest.raises(ExternalArchiveError, match='differ from Kraken'):
            KrakenTradeHistoryArchive(fetch).verify()

    def test_another_manifest_schema_is_refused(self, tmp_path):
        # Schema 1 led with the price: read positionally it gives a price for a timestamp
        with pytest.raises(ExternalArchiveError, match='schema 1'):
            KrakenTradeHistoryArchive(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS}, schema=1)).verify()

    def test_nothing_is_converted_from_unverified_parts(self, tmp_path):
        archive = KrakenTradeHistoryArchive(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS}))
        with pytest.raises(ExternalArchiveError, match='verify step first'):
            KrakenTradeHistoryConverter(archive, tmp_path / 'raw').convert(
                'BTCUSD', 'BTC', 'USD', None)


class TestAPairIsReadExactly:

    def test_btcusd_is_the_member_xbtusd_and_never_a_near_match(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSDC.csv': ['1381095256.0,999.0,1.0,b,l,,7'],
                                  'XBTUSD.csv': _XBT_ROWS})
        result = _convert(fetch, tmp_path / 'raw')
        assert result.member == 'XBTUSD.csv'
        assert [tick['trade_id'] for tick in _ticks(tmp_path / 'raw')] == [1, 2, 3]

    def test_a_header_in_another_order_is_refused(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSD.csv': ['122.0,1381095256.0,0.1,s,l,,1']},
                       header='price,timestamp,volume,type,order_type,misc,trade_id')
        with pytest.raises(ExternalArchiveError, match='another schema'):
            _convert(fetch, tmp_path / 'raw')

    def test_a_pair_the_archive_does_not_hold_is_refused(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})
        with pytest.raises(ExternalArchiveError, match='no member'):
            _convert(fetch, tmp_path / 'raw', base='DOT', symbol='DOTUSD')


class TestEachTradeBecomesOneTick:

    def test_the_first_bitcoin_trade(self, tmp_path):
        _convert(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS}), tmp_path / 'raw')
        first = _ticks(tmp_path / 'raw')[0]
        assert first['timestamp'] == '2013.10.06 21:34:16'
        assert first['time_msc'] == 1381095256090
        assert first['bid'] == first['ask'] == first['last'] == 122.0
        assert first['real_volume'] == 0.1
        assert first['tick_flags'] == 'SELL'
        assert first['trade_id'] == 1
        assert 'collected_msc' not in first   # nothing was received: the absence is the statement

    def test_the_file_names_its_converter_and_its_material(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})
        result = _convert(fetch, tmp_path / 'raw')
        document = json.loads((tmp_path / 'raw' / result.files[0].name).read_text())
        metadata = document['metadata']
        assert result.files[0].name == 'BTCUSD_20131006_213416_ticks.json'
        assert metadata['data_format_version'] == '1.7.0'
        assert metadata['origin']['instance_id'] == CONVERTER_IDENTITY
        assert metadata['external_source']['member'] == 'XBTUSD.csv'
        assert len(metadata['external_source']['parts']) == 3
        assert document['summary']['total_ticks'] == 3

    def test_the_same_parts_give_the_same_ticks(self, tmp_path):
        fetch = _fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})
        _convert(fetch, tmp_path / 'first')
        _convert(fetch, tmp_path / 'second')
        assert _ticks(tmp_path / 'first') == _ticks(tmp_path / 'second')


class TestWhereConversionStopsAndSplits:

    def test_conversion_stops_before_our_first_collected_tick(self, tmp_path):
        # External data lives only BEFORE our collection, never inside it
        cut = datetime(2013, 10, 6, 21, 40, tzinfo=timezone.utc)
        result = _convert(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS}), tmp_path / 'raw', cut_at=cut)
        assert result.rows_converted == 2
        assert [tick['trade_id'] for tick in _ticks(tmp_path / 'raw')] == [1, 2]

    def test_one_file_per_month_and_never_more_than_the_cap(self, tmp_path):
        october = [f'{1381095256 + i},100.0,1.0,b,l,,{i + 1}' for i in range(5)]
        november = [f'{1383696000 + i},101.0,1.0,s,l,,{i + 6}' for i in range(2)]
        result = _convert(_fetch(tmp_path, {'XBTUSD.csv': october + november}),
                          tmp_path / 'raw', cap=3)
        assert [file.tick_count for file in result.files] == [3, 2, 2]
        assert [file.first_tick.month for file in result.files] == [10, 10, 11]


class TestTheImportStampsItExternal:

    def test_a_converted_file_imports_as_external_stamped_and_deficient(self, tmp_path):
        # Through the real import: the origin resolves from the converter's own identity, and
        # the index measures what such data lacks — a quote and an arrival time
        raw, target = tmp_path / 'raw', tmp_path / 'target'
        _convert(_fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS}), raw)
        importer = TickDataImporter(source_dir=str(raw), target_dir=str(target),
                                    auto_render_bars=False)
        importer.process_all_exports()
        assert importer.processed_files == 1 and importer.errors == []

        header = pq.read_schema(find_tick_parquets(target)[0]).metadata
        assert header[b'origin_class'] == b'external'
        assert header[b'origin_evidence'] == b'stamped'
        assert json.loads(header[b'source_meta_external_source'])['vendor'] == 'kraken'

        index = TickIndexManager(data_dir=str(target))
        index.build_index()
        entry = index.get_symbol_entries('kraken_spot', 'BTCUSD')[0]
        assert entry['deficiencies'] == [DataDeficiency.NO_QUOTE, DataDeficiency.NO_ARRIVAL_TIME]
        assert entry['trade_id_gaps'].missing == 0


class TestTheArchiveStepProvesBeforeItRemoves:
    """
    The archive step deletes the loose copies of source files, so it removes nothing it has not
    proven imported and zipped intact — the four checks of the import protocol, then the zip.
    """

    def _commands(self, tmp_path: Path, monkeypatch) -> KrakenHistoryCommands:
        """Commands over a verified fetch, with the archive's paths pointed into tmp_path."""
        fetch = _fetch(tmp_path, {'XBTUSD.csv': _XBT_ROWS})
        KrakenTradeHistoryArchive(fetch).verify()
        target, finished = tmp_path / 'target', tmp_path / 'finished'
        target.mkdir()

        def index(force: bool = False) -> TickIndexManager:
            manager = TickIndexManager(data_dir=str(target))
            manager.build_index(force_rebuild=True)
            return manager

        monkeypatch.setattr(KrakenHistoryCommands, '_tick_index', staticmethod(index))
        monkeypatch.setattr(ImportConfigManager, 'get_data_finished_path',
                            lambda self: str(finished))
        return KrakenHistoryCommands(fetch)

    def _import(self, tmp_path: Path) -> None:
        TickDataImporter(source_dir=str(tmp_path / 'raw'), target_dir=str(tmp_path / 'target'),
                         finished_dir=str(tmp_path / 'finished'), move_processed_files=True,
                         auto_render_bars=False).process_all_exports()

    def test_an_imported_conversion_is_zipped_and_its_loose_copies_removed(
            self, tmp_path, monkeypatch):
        commands = self._commands(tmp_path, monkeypatch)
        assert commands.convert(['BTCUSD'], raw_dir=tmp_path / 'raw') == 0
        self._import(tmp_path)
        loose = list((tmp_path / 'finished').glob('*_ticks.json'))
        assert len(loose) == 1

        assert commands.archive('history_v1.zip') == 0

        with zipfile.ZipFile(tmp_path / 'finished' / 'Archives' / 'history_v1.zip') as archive:
            assert archive.namelist() == [loose[0].name]
            assert archive.testzip() is None
        assert not loose[0].exists()
        record = json.loads(next((tmp_path / 'fetch' / 'conversions').glob('*.json')).read_text())
        assert record['archived_to'] == 'history_v1.zip'

    def test_a_conversion_not_imported_is_refused_and_nothing_is_removed(
            self, tmp_path, monkeypatch, capsys):
        commands = self._commands(tmp_path, monkeypatch)
        commands.convert(['BTCUSD'], raw_dir=tmp_path / 'raw')

        assert commands.archive('history_v1.zip') == 1

        out = capsys.readouterr().out
        assert 'not proven imported' in out and 'inbox: 1 file(s)' in out
        assert not (tmp_path / 'finished' / 'Archives' / 'history_v1.zip').exists()
        assert len(list((tmp_path / 'raw').glob('*_ticks.json'))) == 1
