"""
FiniexTestingIDE - Origin Stamp Import Tests

Guards the chain #518 rests on: a producer's stated identity enters the importer, is resolved
ONCE against this side's registry, and is stamped into the parquet and carried into the index.

Two properties are what these tests are actually for. The identity travels VERBATIM beside the
resolved class, because a class can only ever be re-derived later if the identity survived. And
the class is read back from the STAMP rather than re-resolved — a registry is a judgement that
can be edited, so a surface asking it again would report today's meaning against a file
imported under the meaning of the day it arrived.
"""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from python.configuration.data_origin_registry import DataOriginRegistry
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.tick_index_manager import TickIndexManager
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    write_json_fixture,
)

_INSTANCE = 'a7f21c0b4e88'

_ORIGIN_BLOCK = {
    'instance_id': _INSTANCE,
    'collected_on': 'collector-prod',
    'producer': 'finiex-data-collector',
    'producer_version': '1.1.0',
}


@pytest.fixture
def registry_at(tmp_path, monkeypatch):
    """
    Point the registry at a temporary file for the duration of one test.

    The paths are module constants resolved at call time, so redirecting them reaches every
    `DataOriginRegistry()` the importer builds without handing one in.
    """
    def _install(origins: dict, attestations: list = None) -> Path:
        path = tmp_path / 'registry.json'
        path.write_text(json.dumps({'origins': origins,
                                    'attestations': attestations or []}), encoding='utf-8')
        monkeypatch.setattr(
            'python.configuration.data_origin_registry._CONFIG_PATH', str(path))
        monkeypatch.setattr(
            'python.configuration.data_origin_registry._USER_CONFIG_PATH',
            str(tmp_path / 'absent.json'))
        DataOriginRegistry.reload()
        return path

    yield _install
    DataOriginRegistry.reload()


def _import_one(tmp_path: Path, metadata_extra: dict, version: str) -> Path:
    """
    Import a single synthetic tick file into a scratch archive.

    Args:
        tmp_path: pytest's per-test directory
        metadata_extra: Extra metadata merged into the file header
        version: The `data_format_version` the file declares

    Returns:
        The written tick parquet
    """
    source = tmp_path / 'raw'
    target = tmp_path / 'out'
    source.mkdir(exist_ok=True)
    data = build_minimal_tick_json(symbol='BTCUSD', broker_type='kraken_spot',
                                   data_format_version=version,
                                   extra_metadata=metadata_extra)
    write_json_fixture(source, 'BTCUSD_ticks.json', data)
    TickDataImporter(source_dir=str(source), target_dir=str(target)).process_all_exports()
    return next((target / 'kraken_spot' / 'ticks' / 'BTCUSD').glob('*.parquet'))


def _stamp(parquet_path: Path) -> dict:
    """
    Read the origin stamp back out of a written parquet.

    Args:
        parquet_path: The file to read

    Returns:
        The three stamped values plus the verbatim block, decoded
    """
    meta = pq.ParquetFile(parquet_path).metadata.metadata
    return {
        'instance_id': meta.get(b'origin_instance_id', b'').decode('utf-8'),
        'class': meta.get(b'origin_class', b'').decode('utf-8'),
        'evidence': meta.get(b'origin_evidence', b'').decode('utf-8'),
        'block': meta.get(b'source_meta_origin'),
    }


class TestAStatedIdentityIsResolvedAndStamped:

    def test_a_registered_identity_lands_as_production_and_stamped(self, tmp_path, registry_at):
        registry_at({_INSTANCE: {'class': 'production', 'name': 'collector prod'}})
        parquet = _import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        stamp = _stamp(parquet)
        assert stamp['instance_id'] == _INSTANCE
        assert stamp['class'] == 'production'
        assert stamp['evidence'] == 'stamped'

    def test_the_identity_itself_travels_verbatim(self, tmp_path, registry_at):
        # The resolved class is this side's judgement and can be re-made; the block is the
        # producer's statement and cannot be reconstructed once it is dropped.
        registry_at({_INSTANCE: {'class': 'production'}})
        parquet = _import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        block = json.loads(_stamp(parquet)['block'])
        assert block == _ORIGIN_BLOCK

    def test_an_unregistered_identity_lands_as_unknown(self, tmp_path, registry_at):
        registry_at({})
        parquet = _import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        stamp = _stamp(parquet)
        assert stamp['instance_id'] == _INSTANCE
        assert stamp['class'] == 'unknown'


class TestAFileWithoutAnIdentity:

    def test_a_legacy_file_under_a_claim_is_attested(self, tmp_path, registry_at):
        registry_at({}, [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.6.0'},
                          'class': 'production', 'attested_by': 'operator',
                          'attested_at': '2026-09-19', 'basis': 'before the block existed'}])
        stamp = _stamp(_import_one(tmp_path, {}, '1.5.0'))
        assert stamp['class'] == 'production'
        assert stamp['evidence'] == 'attested'
        assert stamp['instance_id'] == ''

    def test_a_legacy_file_with_no_claim_is_unknown(self, tmp_path, registry_at):
        registry_at({})
        stamp = _stamp(_import_one(tmp_path, {}, '1.5.0'))
        assert stamp['class'] == 'unknown'
        assert stamp['evidence'] == 'unknown'


class TestTheIndexCarriesWhatWasStamped:

    def test_the_index_reports_the_stamp_rather_than_re_resolving(self, tmp_path, registry_at):
        registry_at({_INSTANCE: {'class': 'production', 'name': 'collector prod'}})
        _import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')

        # The registry now says something ELSE about the same identity. The index must still
        # report what the file was imported under — that is the whole reason it is a stamp.
        registry_at({_INSTANCE: {'class': 'development', 'name': 'reclassified'}})
        index = TickIndexManager(data_dir=str(tmp_path / 'out'))
        index.build_index()
        entry = index.index['kraken_spot']['BTCUSD'][0]

        assert entry['origin_instance_id'] == _INSTANCE
        assert entry['origin_class'] == 'production'
        assert entry['origin_evidence'] == 'stamped'

    def test_an_index_entry_survives_the_parquet_round_trip(self, tmp_path, registry_at):
        registry_at({_INSTANCE: {'class': 'production'}})
        _import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')

        # Built, persisted, and read back from the index FILE — the step where a column added
        # to the entry but not to the persist list disappears without anything going red.
        TickIndexManager(data_dir=str(tmp_path / 'out')).build_index(force_rebuild=True)
        reloaded = TickIndexManager(data_dir=str(tmp_path / 'out'))
        reloaded.build_index()
        entry = reloaded.index['kraken_spot']['BTCUSD'][0]
        assert entry['origin_class'] == 'production'
        assert entry['origin_evidence'] == 'stamped'
