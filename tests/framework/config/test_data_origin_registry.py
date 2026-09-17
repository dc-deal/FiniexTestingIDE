"""
FiniexTestingIDE - Data Origin Registry Tests

Guards the one judgement #518 rests on: what a file's stated identity MEANS here, and how well
that meaning is known.

The distinction these tests exist to protect is between the two grades, not between the
classes. `production` resolved from a producer's own stamp and `production` resolved from a
claim we recorded are the same word and a different fact — and only the second one is a
sentence somebody wrote. A resolver that collapses them lets a claim present itself as a
measurement, which is the property the whole provenance contract is built to keep.
"""

import json
from pathlib import Path

import pytest

from python.configuration.data_origin_registry import DataOriginRegistry
from python.framework.types.config_types.data_origin_config_types import (
    OriginClass,
    OriginEvidence,
)

_PROD_ID = 'a7f21c0b4e88'
_DEV_ID = '9c3fa4c80d95'


def _registry(tmp_path: Path, origins: dict, attestations: list) -> DataOriginRegistry:
    """
    Build a registry over a temporary file, isolated from the repository's own.

    Args:
        tmp_path: pytest's per-test directory
        origins: The `origins` mapping to write
        attestations: The `attestations` list to write

    Returns:
        A registry reading only that file
    """
    path = tmp_path / 'data_origins.json'
    path.write_text(json.dumps({'origins': origins, 'attestations': attestations}),
                    encoding='utf-8')
    DataOriginRegistry.reload()
    return DataOriginRegistry(config_path=str(path),
                              user_config_path=str(tmp_path / 'absent.json'))


@pytest.fixture(autouse=True)
def _drop_cache():
    """The registry caches at class level — a leftover would answer the next test's question."""
    DataOriginRegistry.reload()
    yield
    DataOriginRegistry.reload()


class TestAStatedIdentityIsJudgedByItself:

    def test_a_registered_identity_resolves_stamped(self, tmp_path):
        registry = _registry(tmp_path, {_PROD_ID: {'class': 'production'}}, [])
        result = registry.resolve(DataOriginRegistry.read_nested_instance_id(
            {'origin': {'instance_id': _PROD_ID}}),
            '1.7.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.PRODUCTION
        assert result.evidence == OriginEvidence.STAMPED
        assert result.is_admissible_for_measurement

    def test_a_development_identity_is_not_admissible(self, tmp_path):
        registry = _registry(tmp_path, {_DEV_ID: {'class': 'development'}}, [])
        result = registry.resolve(DataOriginRegistry.read_nested_instance_id(
            {'origin': {'instance_id': _DEV_ID}}),
            '1.7.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.DEVELOPMENT
        assert not result.is_admissible_for_measurement

    def test_an_unregistered_identity_does_NOT_fall_back_to_an_attestation(self, tmp_path):
        # The case that decides the design. A file carrying an identity nobody registered is a
        # gap in THIS file, not a legacy file — falling back would hand it a claim written for
        # a different era and hide the very instance somebody needs to hear about.
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '9.9.9'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'covers everything'}])
        result = registry.resolve(DataOriginRegistry.read_nested_instance_id(
            {'origin': {'instance_id': 'f00df00df00d'}}),
            '1.5.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.UNKNOWN
        assert result.evidence == OriginEvidence.UNKNOWN


class TestAFileWithoutAnIdentityFallsToTheClaim:

    def test_a_file_below_the_boundary_is_attested_never_stamped(self, tmp_path):
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.6.0'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'written before the block existed'}])
        result = registry.resolve(None, '1.5.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.PRODUCTION
        assert result.evidence == OriginEvidence.ATTESTED
        # The whole point: honest enough to explore with, never enough to measure against.
        assert not result.is_admissible_for_measurement

    def test_the_boundary_is_inclusive(self, tmp_path):
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.6.0'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'inclusive'}])
        assert registry.resolve(None, '1.6.0', broker_type='kraken_spot').evidence == OriginEvidence.ATTESTED

    def test_a_file_above_the_boundary_is_unknown(self, tmp_path):
        # This is what closes the claim by itself: once the producer ships the block, every new
        # file is above the boundary and none of them can fall under a claim again.
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '1.6.0'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'closed'}])
        result = registry.resolve(None, '1.7.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.UNKNOWN

    def test_a_claim_does_not_cross_archives(self, tmp_path):
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '9.9.9'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'kraken only'}])
        assert registry.resolve(None, '1.5.0', broker_type='mt5').origin_class == OriginClass.UNKNOWN

    def test_an_unreadable_version_matches_nothing(self, tmp_path):
        # A claim is bounded by a version. A file whose version cannot be read cannot be shown
        # to fall inside that bound, and 'probably' is not a grade this vocabulary has.
        registry = _registry(
            tmp_path, {},
            [{'scope': {'broker_type': 'kraken_spot', 'up_to_format': '9.9.9'},
              'class': 'production', 'attested_by': 'operator',
              'attested_at': '2026-09-19', 'basis': 'covers everything readable'}])
        assert registry.resolve(None, 'unknown', broker_type='kraken_spot').origin_class == OriginClass.UNKNOWN


class TestTheDefaultAnswerIsUnknown:

    def test_an_empty_registry_resolves_unknown(self, tmp_path):
        registry = _registry(tmp_path, {}, [])
        result = registry.resolve(None, '1.5.0', broker_type='kraken_spot')
        assert result.origin_class == OriginClass.UNKNOWN
        assert result.instance_id is None

    def test_a_malformed_origin_block_is_treated_as_absent(self, tmp_path):
        registry = _registry(tmp_path, {_PROD_ID: {'class': 'production'}}, [])
        for block in ({'origin': 'a7f2'}, {'origin': {}}, {'origin': {'instance_id': ''}}):
            assert registry.resolve(DataOriginRegistry.read_nested_instance_id(block), '1.5.0',
                                    broker_type='kraken_spot').instance_id is None


class TestTheRegistryRefusesAFileItCannotTrust:

    def test_an_unknown_class_fails_at_parse_time(self, tmp_path):
        # At config-parse time rather than as an unexplainable answer later: the vocabulary is
        # closed, and a fourth value is how a deployment label creeps back into this judgement.
        registry = _registry(tmp_path, {_PROD_ID: {'class': 'staging'}}, [])
        with pytest.raises(ValueError, match='Invalid data origin registry'):
            registry.resolve(None, '1.5.0', broker_type='kraken_spot')

    def test_an_unknown_key_fails_rather_than_being_dropped(self, tmp_path):
        registry = _registry(tmp_path, {_PROD_ID: {'class': 'production', 'clas': 'typo'}}, [])
        with pytest.raises(ValueError, match='Invalid data origin registry'):
            registry.resolve(None, '1.5.0', broker_type='kraken_spot')

    def test_an_identity_key_that_could_never_match_is_refused(self, tmp_path):
        # The quietest way to get this wrong: an entry that LOOKS registered and matches
        # nothing, so its files stay `unknown` for good with no error anywhere. Uppercase is
        # the realistic case — one registry holding three producers cannot be case-insensitive.
        for bad in ('EE5AA1436824', 'ee5aa14368', 'collector-prod', ''):
            registry = _registry(tmp_path, {bad: {'class': 'production'}}, [])
            with pytest.raises(ValueError, match='12 lowercase hex'):
                registry.resolve(None, '1.5.0', broker_type='kraken_spot')


class TestTheTrackedFileIsSafeByItself:

    def test_the_repository_registry_admits_nothing_for_measurement(self):
        # The tracked copy carries the schema and development examples only. A fresh clone that
        # silently admitted production data would be the one failure this file must not have.
        registry = DataOriginRegistry(user_config_path='user_configs/absent_on_purpose.json')
        for version in ('1.0.0', '1.5.0', '1.6.0', '2.0.0'):
            result = registry.resolve(None, version, broker_type='kraken_spot')
            assert not result.is_admissible_for_measurement


class TestAClaimIsBoundToOneArchive:
    """
    The signal archive was the case the first scope could not describe at all.

    A tick file without an origin block still names its broker; a signal envelope names neither
    a broker nor a `data_format_version` — it carries a pipeline and a `schema_version`. A scope
    with one key therefore covered one archive and silently matched nothing in the other, which
    is the exact failure mode the key-shape validator exists to prevent one level up: an entry
    that looks present and can never fire.
    """

    def test_a_signal_claim_covers_a_signal_envelope(self, tmp_path):
        registry = _registry(tmp_path, {}, [
            {'scope': {'pipeline_id': 'crypto_sentiment', 'up_to_format': '2.1.0'},
             'class': 'production', 'attested_by': 'operator', 'attested_at': '2026-09-17',
             'basis': 'one engine wrote this archive before the field existed'}])

        result = registry.resolve(None, '2.0.0', pipeline_id='crypto_sentiment')

        assert result.origin_class == OriginClass.PRODUCTION
        assert result.evidence == OriginEvidence.ATTESTED
        assert not result.is_admissible_for_measurement

    def test_a_signal_claim_does_not_reach_a_tick_file(self, tmp_path):
        registry = _registry(tmp_path, {}, [
            {'scope': {'pipeline_id': 'crypto_sentiment', 'up_to_format': '9.9.9'},
             'class': 'production', 'attested_by': 'operator', 'attested_at': '2026-09-17',
             'basis': 'deliberately unbounded, to prove the archive key is what stops it'}])

        result = registry.resolve(None, '1.5.0', broker_type='kraken_spot')

        assert result.origin_class == OriginClass.UNKNOWN

    def test_a_tick_claim_does_not_reach_a_signal_envelope(self, tmp_path):
        registry = _registry(tmp_path, {}, [
            {'scope': {'broker_type': 'kraken_spot', 'up_to_format': '9.9.9'},
             'class': 'production', 'attested_by': 'operator', 'attested_at': '2026-09-17',
             'basis': 'deliberately unbounded, to prove the archive key is what stops it'}])

        result = registry.resolve(None, '2.0.0', pipeline_id='crypto_sentiment')

        assert result.origin_class == OriginClass.UNKNOWN

    def test_an_identity_still_beats_the_archive_it_came_from(self, tmp_path):
        """A stated identity is judged alone — the claim is only ever the fallback."""
        registry = _registry(tmp_path, {_PROD_ID: {'class': 'production'}}, [
            {'scope': {'pipeline_id': 'crypto_sentiment', 'up_to_format': '9.9.9'},
             'class': 'development', 'attested_by': 'operator', 'attested_at': '2026-09-17',
             'basis': 'would disagree with the identity if it were consulted'}])

        result = registry.resolve(_PROD_ID, '2.0.0', pipeline_id='crypto_sentiment')

        assert result.origin_class == OriginClass.PRODUCTION
        assert result.evidence == OriginEvidence.STAMPED
        assert result.is_admissible_for_measurement

    def test_a_scope_naming_no_archive_is_refused(self, tmp_path):
        registry = _registry(tmp_path, {}, [
            {'scope': {'up_to_format': '1.5.0'}, 'class': 'production',
             'attested_by': 'operator', 'attested_at': '2026-09-17', 'basis': 'names nothing'}])

        with pytest.raises(ValueError, match='exactly one archive'):
            registry.resolve(None, '1.5.0', broker_type='kraken_spot')

    def test_a_scope_naming_both_archives_is_refused(self, tmp_path):
        registry = _registry(tmp_path, {}, [
            {'scope': {'broker_type': 'kraken_spot', 'pipeline_id': 'crypto_sentiment',
                       'up_to_format': '1.5.0'},
             'class': 'production', 'attested_by': 'operator', 'attested_at': '2026-09-17',
             'basis': 'two archives whose version numbers mean different things'}])

        with pytest.raises(ValueError, match='exactly one archive'):
            registry.resolve(None, '1.5.0', broker_type='kraken_spot')
