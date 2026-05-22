"""
FiniexTestingIDE - deep_merge list_merge_keys Tests

Unit coverage for the list-merge-by-identifier feature added to
python/framework/utils/config_merge_utils.py::deep_merge.

Scope: drives deep_merge() directly with in-memory dicts — no JSON fixtures,
no loaders involved. Companion to test_execution_config_cascade.py, which
covers the scenario cascade end-to-end.
"""

import pytest

from python.framework.utils.config_merge_utils import deep_merge


class TestDeepMergeListMergeKeys:
    """deep_merge with list_merge_keys parameter — element-wise list merging by ID."""

    def test_atomic_replace_when_no_list_merge_keys(self):
        """Without list_merge_keys, lists are replaced wholesale — backward-compat default."""
        base = {'brokers': [{'broker_type': 'mt5', 'foo': 1}, {'broker_type': 'kraken_spot', 'foo': 2}]}
        override = {'brokers': [{'broker_type': 'kraken_spot', 'foo': 99}]}

        merged = deep_merge(base, override)

        # Old behavior: entire list replaced — mt5 entry lost
        assert merged['brokers'] == [{'broker_type': 'kraken_spot', 'foo': 99}]

    def test_list_merge_by_id_overrides_matching_field(self):
        """With list_merge_keys, matching entries are deep-merged per field."""
        base = {'brokers': [
            {'broker_type': 'mt5', 'market_type': 'forex'},
            {'broker_type': 'kraken_spot', 'market_type': 'crypto', 'dry_run': True},
        ]}
        override = {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': False}]}

        merged = deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

        assert len(merged['brokers']) == 2
        kraken = next(b for b in merged['brokers'] if b['broker_type'] == 'kraken_spot')
        # Field from override
        assert kraken['dry_run'] is False
        # Field preserved from base
        assert kraken['market_type'] == 'crypto'

    def test_list_merge_preserves_base_only_entries(self):
        """Base entries with no override match must stay intact."""
        base = {'brokers': [
            {'broker_type': 'mt5', 'market_type': 'forex'},
            {'broker_type': 'kraken_spot', 'market_type': 'crypto'},
        ]}
        override = {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': False}]}

        merged = deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

        mt5 = next(b for b in merged['brokers'] if b['broker_type'] == 'mt5')
        assert mt5 == {'broker_type': 'mt5', 'market_type': 'forex'}

    def test_list_merge_appends_override_only_entries(self):
        """Override entries with no base match are appended at the end."""
        base = {'brokers': [{'broker_type': 'mt5', 'market_type': 'forex'}]}
        override = {'brokers': [{'broker_type': 'kraken_spot', 'market_type': 'crypto'}]}

        merged = deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

        broker_types = [b['broker_type'] for b in merged['brokers']]
        assert broker_types == ['mt5', 'kraken_spot']

    def test_missing_identifier_in_override_raises(self):
        """Override entry without the identifier field must hard-fail with a clear message."""
        base = {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': True}]}
        override = {'brokers': [{'dry_run': False}]}  # missing broker_type

        with pytest.raises(ValueError, match=r"missing required 'broker_type' identifier"):
            deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

    def test_nested_dict_inside_list_entry_deep_merges(self):
        """Nested dicts inside a matched list entry must merge per-field, not replace."""
        base = {'brokers': [{
            'broker_type': 'kraken_spot',
            'broker_transport': {
                'api_base_url': 'https://api.kraken.com',
                'rate_limit_interval_s': 1.0,
                'poll_interval_ms': 5000,
            },
        }]}
        override = {'brokers': [{
            'broker_type': 'kraken_spot',
            'broker_transport': {'poll_interval_ms': 1000},
        }]}

        merged = deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

        transport = merged['brokers'][0]['broker_transport']
        # Field overridden
        assert transport['poll_interval_ms'] == 1000
        # Other fields preserved from base
        assert transport['api_base_url'] == 'https://api.kraken.com'
        assert transport['rate_limit_interval_s'] == 1.0

    def test_atomic_keys_still_works_alongside_list_merge_keys(self):
        """atomic_keys must continue to override wholesale even when list_merge_keys is set."""
        base = {
            'trade_simulator_config': {'balances': {'EUR': 10000, 'USD': 5000}},
            'brokers': [{'broker_type': 'mt5', 'market_type': 'forex'}],
        }
        override = {
            'trade_simulator_config': {'balances': {'JPY': 50000}},
            'brokers': [{'broker_type': 'mt5', 'market_type': 'updated'}],
        }

        merged = deep_merge(
            base,
            override,
            atomic_keys={'balances'},
            list_merge_keys={'brokers': 'broker_type'},
        )

        # balances replaced wholesale (atomic)
        assert merged['trade_simulator_config']['balances'] == {'JPY': 50000}
        # brokers merged element-wise
        assert merged['brokers'][0]['market_type'] == 'updated'

    def test_inputs_are_not_mutated(self):
        """deep_merge must return a new dict — base and override must stay untouched."""
        base = {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': True}]}
        override = {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': False}]}

        deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})

        assert base == {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': True}]}
        assert override == {'brokers': [{'broker_type': 'kraken_spot', 'dry_run': False}]}
