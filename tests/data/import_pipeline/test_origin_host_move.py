"""
One identity seen on two producing hosts.

An identity belongs to a DATA ROOT, never to a machine, so a move is legitimate — which is
exactly why it must be visible. A RESTORE continues one series on new hardware; a COPY makes
two writers assert one identity, and that is the failure the whole provenance contract exists
to prevent. Nothing in the data separates them, so the only honest answer is to report the
observation and let the operator say which it was.

The derivation lives at the INDEX rather than at import: the index sees every file for an
identity at once, where an importer mid-batch compares against a stale picture and misses a
change that arrived inside its own run.
"""

import pyarrow.parquet as pq

from python.configuration.data_origin_registry import DataOriginRegistry
from python.data_management.index.tick_index_manager import TickIndexManager


def _block(host: str, instance_id: str = 'a7f21c0b4e88') -> dict:
    """
    An origin block naming one host.

    Args:
        host: What the producer calls the machine it ran on
        instance_id: The identity, shared across hosts unless overridden

    Returns:
        The metadata fragment
    """
    return {'origin': {'instance_id': instance_id, 'collected_on': host,
                       'producer': 'finiex-data-collector', 'producer_version': '1.2.1'}}


class TestTheHostReachesTheIndex:
    """
    Read from the producer's VERBATIM block, never from a stamp of its own.

    The identity, class and evidence ARE stamped, because two of them are judgements that must
    not be re-derived. The host is neither judged nor decided on, so a second copy would be the
    same fact twice — and it would answer nothing on a file imported before that copy existed,
    where the block has carried the value all along.
    """

    def test_it_reaches_the_index_from_the_block(self, tmp_path, registry_at, import_one):
        registry_at({'a7f21c0b4e88': {'class': 'production'}})
        import_one(tmp_path, _block('vmd188386'), '1.7.0')

        index = TickIndexManager(data_dir=str(tmp_path / 'out'))
        index.build_index(force_rebuild=True)
        entry = index.index['kraken_spot']['BTCUSD'][0]

        assert entry['origin_collected_on'] == 'vmd188386'
        # And the block itself is untouched — the index reads it, it does not replace it.
        parquet = next((tmp_path / 'out' / 'kraken_spot' / 'ticks' / 'BTCUSD')
                       .glob('*.parquet'))
        block = pq.ParquetFile(parquet).metadata.metadata.get(b'source_meta_origin')
        assert b'vmd188386' in block

    def test_a_file_without_a_host_reads_empty_rather_than_raising(self, tmp_path,
                                                                  registry_at, import_one):
        # The producer may omit it; '' is the honest reading and is what keeps every caller
        # from having to distinguish a missing key from a missing value.
        registry_at({'a7f21c0b4e88': {'class': 'production'}})
        block = _block('x')
        del block['origin']['collected_on']
        import_one(tmp_path, block, '1.7.0')

        index = TickIndexManager(data_dir=str(tmp_path / 'out'))
        index.build_index(force_rebuild=True)

        assert index.index['kraken_spot']['BTCUSD'][0]['origin_collected_on'] == ''


class TestTheMoveIsReported:

    def test_two_hosts_under_one_identity_are_named(self):
        index = TickIndexManager(data_dir='/nonexistent')
        index.index = {'kraken_spot': {'BTCUSD': [
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd188386'},
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd999999'},
        ]}}

        assert index.identities_on_several_hosts() == {
            'a7f21c0b4e88': ['vmd188386', 'vmd999999']}

    def test_one_identity_on_one_host_is_silent(self):
        index = TickIndexManager(data_dir='/nonexistent')
        index.index = {'kraken_spot': {'BTCUSD': [
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd188386'},
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd188386'},
        ]}}

        assert index.identities_on_several_hosts() == {}

    def test_two_identities_on_two_hosts_are_silent(self):
        # The normal case: two producers, two machines. It is one identity on several hosts
        # that carries the question, never several identities.
        index = TickIndexManager(data_dir='/nonexistent')
        index.index = {'kraken_spot': {'BTCUSD': [
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd188386'},
            {'origin_instance_id': 'b8e32d1c5f99', 'origin_collected_on': 'vmd999999'},
        ]}}

        assert index.identities_on_several_hosts() == {}

    def test_a_missing_host_is_not_a_second_host(self):
        # The legacy archive carries no host at all. Counting '' as a value would report
        # every identity that predates the field as having moved.
        index = TickIndexManager(data_dir='/nonexistent')
        index.index = {'kraken_spot': {'BTCUSD': [
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': 'vmd188386'},
            {'origin_instance_id': 'a7f21c0b4e88', 'origin_collected_on': ''},
        ]}}

        assert index.identities_on_several_hosts() == {}


class TestTheReaderIsStrict:

    def test_a_block_that_is_not_a_dict_yields_empty(self):
        assert DataOriginRegistry.read_nested_collected_on({'origin': 'vmd188386'}) == ''

    def test_a_non_string_host_yields_empty(self):
        assert DataOriginRegistry.read_nested_collected_on(
            {'origin': {'collected_on': 12345}}) == ''
