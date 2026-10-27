"""
The composite key builder: one account's rendered document projected onto its key and its parts (#615).

The documents here are small hand-built sections in the shape the renderer writes; the projection
over every shipped document is `test_composite_catalog_completeness.py`'s.
"""

import copy
from typing import Any, Dict

import pytest

from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.framework.types.composite_types import CompositeDocument, CompositePart, RunAxes
from python.framework.types.config_types.composite_definition_config_types import CompositeTier
from python.framework.types.run_kind_types import OrdersTo, TicksFrom
from python.framework.utils.composite_key_builder import (
    KEY_VERSION,
    build_composite_key,
    composite_groups,
    config_changes,
)

_BACKTEST = RunAxes('simulation', TicksFrom.ARCHIVE, OrdersTo.SIMULATED)


@pytest.fixture(scope='module')
def rules() -> Dict[str, CompositeTier]:
    CompositeDefinitionManager.reload()
    return CompositeDefinitionManager(config_path='configs/composite_definition.json').get_rules(
        CompositeDocument.SCENARIO)


def _scenario(**changes: Any) -> Dict[str, Any]:
    document = {
        'symbol': 'EURUSD', 'data_broker_type': 'mt5', 'data_sentiment_type': '',
        'start_date': '2025-01-06T00:00:00+00:00', 'end_date': '2025-01-07T00:00:00+00:00',
        'max_ticks': None, 'role': 'unassigned', 'regime': '', 'session': '',
        'strategy_config': {
            'decision_logic_type': 'CORE/aggressive_trend',
            'decision_logic_config': {'decision_logic_type': 'CORE/aggressive_trend', 'fast_period': 12},
            'worker_instances': {'rsi_fast': 'CORE/rsi'},
            'workers': {'rsi_fast': {'worker_type': 'CORE/rsi', 'periods': {'M5': 14}}},
        },
        'execution_config': {'heartbeat_interval_ms': 1000, 'parallel_workers': False},
        'trade_simulator_config': {'balances': {'USD': 10000}, 'inbound_latency_min_ms': 20,
                                   'seeds': {'inbound_latency_seed': 42}},
        'order_guard_config': {'cooldown_seconds': 60.0},
        'stress_test_config': {'reject_open_order': {'enabled': False, 'seed': 999,
                                                     'probability': 0.7},
                               'stale_data_stress': None},
        'market_entry': {'trading_model': 'margin', 'price_formation': 'quote_driven',
                         'credentials_file': ''},
        'market_rules': {'weekend_closure': True},
    }
    for path, value in changes.items():
        node = document
        segments = path.split('__')
        for segment in segments[:-1]:
            node = node[segment]
        node[segments[-1]] = value
    return document


def _key(rules, document, currency='USD', axes=_BACKTEST, fingerprint='definition-a'):
    return build_composite_key(document, CompositeDocument.SCENARIO, rules, fingerprint, currency, axes)


class TestWhatJoinsAndWhatSplits:

    def test_the_same_document_gives_the_same_key(self, rules):
        assert _key(rules, _scenario()) == _key(rules, _scenario())

    @pytest.mark.parametrize('change', [
        {'symbol': 'GBPUSD'},
        {'start_date': '2025-02-03T00:00:00+00:00'},
        {'max_ticks': 5000},
        {'trade_simulator_config__balances': {'USD': 500}},
        {'trade_simulator_config__seeds': {'inbound_latency_seed': 7}},
        {'execution_config__parallel_workers': True},
        {'market_entry__credentials_file': 'venues/other.json'},
    ])
    def test_a_neutral_value_joins(self, rules, change):
        assert _key(rules, _scenario(**change)).composite_key == _key(rules, _scenario()).composite_key

    def test_a_defining_parameter_splits_the_strategy_part_only(self, rules):
        base = _key(rules, _scenario())
        changed = _key(rules, _scenario(strategy_config__decision_logic_config={'fast_period': 20}))
        assert changed.composite_key != base.composite_key
        assert changed.composite_strategy != base.composite_strategy
        assert changed.composite_conditions == base.composite_conditions

    def test_a_defining_condition_splits_the_conditions_part_only(self, rules):
        base = _key(rules, _scenario())
        changed = _key(rules, _scenario(execution_config__heartbeat_interval_ms=500))
        assert changed.composite_conditions != base.composite_conditions
        assert changed.composite_strategy == base.composite_strategy

    def test_the_floor_splits_whatever_the_definition_relaxes(self, rules):
        relaxed = {**rules, 'strategy_config': CompositeTier.NEUTRAL}
        base = _key(relaxed, _scenario())
        other_logic = _key(relaxed, _scenario(strategy_config__decision_logic_type='CORE/cautious_macd'))
        assert other_logic.composite_strategy != base.composite_strategy

    def test_the_account_part_takes_the_resolved_currency_and_the_account_model(self, rules):
        base = _key(rules, _scenario())
        assert _key(rules, _scenario(), currency='EUR').composite_account != base.composite_account
        spot = _key(rules, _scenario(market_entry__trading_model='spot'))
        assert spot.composite_account != base.composite_account

    def test_the_run_axes_split_a_rehearsal_from_real_money(self, rules):
        rehearsal = _key(rules, _scenario(), axes=RunAxes('autotrader', TicksFrom.VENUE, OrdersTo.SIMULATED))
        real = _key(rules, _scenario(), axes=RunAxes('autotrader', TicksFrom.VENUE, OrdersTo.VENUE))
        assert rehearsal.composite_run_axes != real.composite_run_axes
        assert rehearsal.composite_strategy == real.composite_strategy

    def test_an_unclassified_value_splits_rather_than_joins(self, rules):
        document = _scenario()
        document['a_block_no_rule_knows'] = {'setting': 1}
        assert _key(rules, document).composite_key != _key(rules, _scenario()).composite_key

    def test_the_key_carries_the_definition_and_the_version(self, rules):
        first = _key(rules, _scenario(), fingerprint='definition-a')
        second = _key(rules, _scenario(), fingerprint='definition-b')
        assert first.composite_key != second.composite_key
        assert first.composite_strategy == second.composite_strategy
        assert first.composite_key_version == KEY_VERSION


class TestWhatReadsAlike:

    def test_an_integer_and_a_float_of_one_value_read_alike(self, rules):
        as_float = _scenario(strategy_config__decision_logic_config={'fast_period': 12.0})
        as_int = _scenario(strategy_config__decision_logic_config={'fast_period': 12})
        assert _key(rules, as_float).composite_key == _key(rules, as_int).composite_key

    def test_a_bool_is_no_number(self, rules):
        flag = _scenario(order_guard_config={'cooldown_seconds': True})
        one = _scenario(order_guard_config={'cooldown_seconds': 1})
        assert _key(rules, flag).composite_key != _key(rules, one).composite_key

    def test_a_comment_at_any_level_is_no_value(self, rules):
        document = _scenario()
        document['strategy_config']['_comment'] = 'why the thresholds stand here'
        document['strategy_config']['decision_logic_config']['_comment'] = 'and here'
        assert _key(rules, document).composite_key == _key(rules, _scenario()).composite_key

    def test_a_resolved_absolute_type_path_reads_like_the_configured_one(self, rules):
        # Documents frozen before 2026-10-25 carry the path one machine resolved in the parameters.
        old = _scenario()
        old['strategy_config']['decision_logic_config']['decision_logic_type'] = '/app/user_algos/x/logic.py'
        old['strategy_config']['workers']['rsi_fast']['worker_type'] = '/app/user_algos/x/rsi.py'
        assert _key(rules, old).composite_key == _key(rules, _scenario()).composite_key
        assert config_changes(old, _scenario(), CompositeDocument.SCENARIO, rules) == []


class TestTheExcludedMember:

    def test_a_switched_on_stress_test_excludes_without_moving_the_key(self, rules):
        stressed = _scenario(stress_test_config={'reject_open_order': {
            'enabled': True, 'seed': 1, 'probability': 0.5}, 'stale_data_stress': None})
        key = _key(rules, stressed)
        assert key.excluded_member is True
        assert key.composite_key == _key(rules, _scenario()).composite_key

    def test_a_stress_block_with_every_test_off_is_no_stress_test(self, rules):
        assert _key(rules, _scenario()).excluded_member is False

    def test_an_excluded_member_belongs_to_no_composite(self, rules):
        stressed = _scenario(stress_test_config={'reject_open_order': {
            'enabled': True, 'seed': 1, 'probability': 0.5}})
        keys = {'window_a': _key(rules, _scenario()), 'window_b': _key(rules, _scenario()),
                'stressed': _key(rules, stressed),
                'probe': _key(rules, _scenario(strategy_config__decision_logic_type='CORE/probe'))}
        groups = composite_groups(keys)
        assert sorted(groups.values()) == [['probe'], ['window_a', 'window_b']]


class TestTheChangedValues:

    def test_every_differing_value_is_named_with_its_tier_and_part(self, rules):
        changes = config_changes(
            _scenario(),
            _scenario(symbol='GBPUSD', execution_config__heartbeat_interval_ms=500),
            CompositeDocument.SCENARIO, rules)
        by_setting = {change.setting: change for change in changes}
        assert list(by_setting) == sorted(by_setting)
        heartbeat = by_setting['execution_config.heartbeat_interval_ms']
        assert (heartbeat.before, heartbeat.after) == (1000, 500)
        assert heartbeat.composite_tier is CompositeTier.DEFINING
        assert heartbeat.composite_part is CompositePart.CONDITIONS
        symbol = by_setting['symbol']
        assert symbol.composite_tier is CompositeTier.NEUTRAL and symbol.composite_part is None

    def test_a_value_only_one_side_holds_is_a_change(self, rules):
        after = _scenario()
        after['strategy_config']['decision_logic_config']['slow_period'] = 26
        (change,) = config_changes(_scenario(), after, CompositeDocument.SCENARIO, rules)
        assert change.setting == 'strategy_config.decision_logic_config.slow_period'
        assert (change.before, change.after) == (None, 26)


class TestASessionDocument:

    def test_its_blocks_are_lifted_and_its_kind_is_no_value(self):
        CompositeDefinitionManager.reload()
        session_rules = CompositeDefinitionManager(
            config_path='configs/composite_definition.json').get_rules(CompositeDocument.SESSION)
        document = {
            'kind': 'autotrader_rendered',
            'blocks': {'safety': {'max_drawdown_pct': 25.0}, 'symbol': 'BTCUSD',
                       'tick_source': {'type': 'mock', 'ws_url': ''}},
            'strategy_config': {'decision_logic_type': 'CORE/aggressive_trend'},
            'market_entry': {'trading_model': 'spot'},
        }
        tightened = copy.deepcopy(document)
        tightened['blocks']['safety']['max_drawdown_pct'] = 12.0
        renamed = copy.deepcopy(document)
        renamed['kind'] = 'something_else'
        axes = RunAxes('autotrader', TicksFrom.ARCHIVE, OrdersTo.SIMULATED)

        def key(doc):
            return build_composite_key(doc, CompositeDocument.SESSION, session_rules, 'd', 'USD', axes)
        assert key(tightened).composite_conditions != key(document).composite_conditions
        assert key(renamed).composite_key == key(document).composite_key
        (change,) = config_changes(document, tightened, CompositeDocument.SESSION, session_rules)
        assert change.setting == 'safety.max_drawdown_pct'
