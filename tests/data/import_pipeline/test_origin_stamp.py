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

    def test_a_registered_identity_lands_as_production_and_stamped(self, tmp_path, registry_at, import_one):
        registry_at({_INSTANCE: {'class': 'production', 'name': 'collector prod'}})
        parquet = import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        stamp = _stamp(parquet)
        assert stamp['instance_id'] == _INSTANCE
        assert stamp['class'] == 'production'
        assert stamp['evidence'] == 'stamped'

    def test_the_identity_itself_travels_verbatim(self, tmp_path, registry_at, import_one):
        # The resolved class is this side's judgement and can be re-made; the block is the
        # producer's statement and cannot be reconstructed once it is dropped.
        registry_at({_INSTANCE: {'class': 'production'}})
        parquet = import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        block = json.loads(_stamp(parquet)['block'])
        assert block == _ORIGIN_BLOCK

    def test_an_unregistered_identity_lands_as_unknown(self, tmp_path, registry_at, import_one):
        registry_at({})
        parquet = import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')
        stamp = _stamp(parquet)
        assert stamp['instance_id'] == _INSTANCE
        assert stamp['class'] == 'unknown'


class TestAFileWithoutAnIdentity:

    def test_a_legacy_file_under_a_claim_is_attested(self, tmp_path, registry_at, import_one):
        registry_at({}, [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.6.0'},
                          'class': 'production', 'attested_by': 'operator',
                          'attested_at': '2026-09-19', 'basis': 'before the block existed'}])
        stamp = _stamp(import_one(tmp_path, {}, '1.5.0'))
        assert stamp['class'] == 'production'
        assert stamp['evidence'] == 'attested'
        assert stamp['instance_id'] == ''

    def test_a_legacy_file_with_no_claim_is_unknown(self, tmp_path, registry_at, import_one):
        registry_at({})
        stamp = _stamp(import_one(tmp_path, {}, '1.5.0'))
        assert stamp['class'] == 'unknown'
        assert stamp['evidence'] == 'unknown'


class TestTheIndexCarriesWhatWasStamped:

    def test_the_index_reports_the_stamp_rather_than_re_resolving(self, tmp_path, registry_at, import_one):
        registry_at({_INSTANCE: {'class': 'production', 'name': 'collector prod'}})
        import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')

        # The registry now says something ELSE about the same identity. The index must still
        # report what the file was imported under — that is the whole reason it is a stamp.
        registry_at({_INSTANCE: {'class': 'development', 'name': 'reclassified'}})
        index = TickIndexManager(data_dir=str(tmp_path / 'out'))
        index.build_index()
        entry = index.index['kraken_spot']['BTCUSD'][0]

        assert entry['origin_instance_id'] == _INSTANCE
        assert entry['origin_class'] == 'production'
        assert entry['origin_evidence'] == 'stamped'

    def test_an_index_entry_survives_the_parquet_round_trip(self, tmp_path, registry_at, import_one):
        registry_at({_INSTANCE: {'class': 'production'}})
        import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0')

        # Built, persisted, and read back from the index FILE — the step where a column added
        # to the entry but not to the persist list disappears without anything going red.
        TickIndexManager(data_dir=str(tmp_path / 'out')).build_index(force_rebuild=True)
        reloaded = TickIndexManager(data_dir=str(tmp_path / 'out'))
        reloaded.build_index()
        entry = reloaded.index['kraken_spot']['BTCUSD'][0]
        assert entry['origin_class'] == 'production'
        assert entry['origin_evidence'] == 'stamped'


class TestAProducerThatStopsIdentifyingItself:
    """
    Below the boundary a missing identity is history; at or above it, it is a defect.

    The line between the two is the whole reason there are three evidence grades: an
    attestation may cover an archive written before the block existed, and must never reach a
    file whose producer had the field and left it empty.
    """

    def test_a_file_at_the_boundary_without_an_identity_never_reaches_the_archive(
            self, tmp_path, registry_at):
        # A data error is a scenario-level failure and not a crash (§33), so the proof is
        # the ABSENCE of a parquet rather than an exception: the file is reported and does
        # not enter the archive, which is the only thing that matters downstream.
        registry_at({})
        source = tmp_path / 'raw'
        target = tmp_path / 'out'
        source.mkdir(exist_ok=True)
        write_json_fixture(source, 'BTCUSD_ticks.json', build_minimal_tick_json(
            symbol='BTCUSD', broker_type='kraken_spot', data_format_version='1.7.0'))

        TickDataImporter(source_dir=str(source),
                         target_dir=str(target)).process_all_exports()

        written = list(target.rglob('*.parquet')) if target.exists() else []
        assert written == [], f'a 1.7.0 file with no identity was archived: {written}'

    def test_the_same_file_WITH_an_identity_imports(self, tmp_path, registry_at, import_one):
        # The control: the refusal is about the missing block, not about the version.
        registry_at({_INSTANCE: {'class': 'production'}})
        stamp = _stamp(import_one(tmp_path, {'origin': _ORIGIN_BLOCK}, '1.7.0'))

        assert stamp['evidence'] == 'stamped'

    def test_a_file_below_the_boundary_without_an_identity_still_imports(self, tmp_path,
                                                                        registry_at, import_one):
        # 1.5.0 predates the block. Refusing it would reject the entire legacy archive.
        registry_at({})
        stamp = _stamp(import_one(tmp_path, {}, '1.5.0'))

        assert stamp['evidence'] == 'unknown'

    def test_1_6_0_is_below_the_boundary_and_is_NOT_refused(self, tmp_path, registry_at, import_one):
        # 1.6.0 never reaches production — both producers step 1.5.0 -> 1.7.0 in one
        # deployment — but a DEVELOPMENT file at 1.6.0 exists. It must not be refused, and it
        # must not be attested either: it resolves to `unknown`, which is the fail-closed
        # direction the boundary was chosen for.
        registry_at({}, [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.5.0'},
                          'class': 'production', 'attested_by': 'operator',
                          'attested_at': '2026-09-19', 'basis': 'legacy archive'}])
        stamp = _stamp(import_one(tmp_path, {}, '1.6.0'))

        assert stamp['evidence'] == 'unknown'
        assert stamp['class'] == 'unknown'
