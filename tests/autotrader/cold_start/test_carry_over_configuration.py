"""
Cold Start — An Open Position Under a Changed Configuration (#626)

A restart that takes over what the venue still holds finishes a trade an earlier session opened.
When it runs another composite than that session, the trade ends under a configuration that did
not open it. The boot compares the two — both rendered profiles projected onto the composite key
under the definition the start runs with — and refuses while something is open, unless this one
start declares `--accept-composite-change`.

Four layers are pinned here: the comparison, where a changed value comes from, the refusal's
text, and the decision in the boot step itself, in both account models.
"""

import copy
import json
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.autotrader.profile_value_origin import value_origins
from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.configuration.config_file_loader import ConfigFileLoader
from python.configuration.market_config_loader import MarketConfigFileLoader
from python.framework.autotrader.cold_start.cold_start_adopter import ColdStartAdopter
from python.framework.autotrader.run_record.rendered_profile_builder import (
    render_autotrader_profile,
)
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.autotrader_types.cold_start_types import (
    ConfigurationVerdict,
    CurrentConfiguration,
)
from python.framework.types.composite_types import CompositePart, RunAxes, ValueOrigin
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.persistence_types import (
    ColdStartPayload,
    ConfigurationCarryOver,
    PositionCarryOver,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_kind_types import OrdersTo, TicksFrom
from python.framework.utils.run_id_utils import build_client_order_id
from python.framework.validators import carry_over_configuration_validator
from python.framework.validators.carry_over_configuration_validator import (
    compare_carried_configuration,
    composite_change_refusal,
)
from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY, make_broker_order

PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'
_WRITER_RUN = '20261104_180012_ab12cd34'
_DEPLOYMENT = 'deploy_20261103_090000_1a2b'


@pytest.fixture(scope='module')
def rendered() -> Dict[str, Any]:
    """What the mock profile runs with — the document both sides are rendered as."""
    return render_autotrader_profile(load_autotrader_config(PROFILE))


def _register(document: Dict[str, Any]) -> str:
    """
    Freeze a rendered profile the way a session's header does.

    Args:
        document: The rendered profile

    Returns:
        Its content id in the run-config store
    """
    store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))
    return store.register_document(document, RunConfigKind.AUTOTRADER_RENDERED).config_id


def _changed(document: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    """
    A copy of a rendered profile with one value set.

    Args:
        document: The rendered profile
        path: The value's keys, dot-separated
        value: What it becomes

    Returns:
        The copy
    """
    changed = copy.deepcopy(document)
    node = changed
    keys = path.split('.')
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return changed


def _current(document: Optional[Dict[str, Any]], account_currency: str = 'USD',
             carry_over_from: str = '') -> CurrentConfiguration:
    """What a starting session of the mock profile runs with."""
    return CurrentConfiguration(
        profile_name='btcusd_test', carry_over_from=carry_over_from, deployment_id=_DEPLOYMENT,
        rendered_profile=document, account_currency=account_currency,
        run_axes=RunAxes(run_type=RUN_TYPE_AUTOTRADER, ticks_from=TicksFrom.VENUE,
                         orders_to=OrdersTo.VENUE),
        profile_path=PROFILE, broker_type='kraken_spot')


def _carried(config_id: str, account_currency: str = 'USD',
             profile_name: str = 'btcusd_test') -> ConfigurationCarryOver:
    """The configuration an earlier real-money session wrote its claims under."""
    return ConfigurationCarryOver(
        run_id=_WRITER_RUN, profile_name=profile_name, account_currency=account_currency,
        ticks_from=TicksFrom.VENUE, orders_to=OrdersTo.VENUE, rendered_config_id=config_id)


def _payload(carried: Optional[ConfigurationCarryOver], claims: bool = True) -> ColdStartPayload:
    """A carry-over holding a session key — a claim on the venue — and its configuration."""
    return ColdStartPayload(session_keys=[PREVIOUS_SESSION_KEY] if claims else [],
                            venue_claims_configuration=carried)


class TestTheComparison:
    """Both sides projected onto the composite key under the definition the start runs with."""

    def test_the_same_configuration_is_one_composite(self, rendered):
        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.SAME

    def test_a_strategy_parameter_changes_the_strategy_part(self, rendered):
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(after))

        assert comparison.verdict is ConfigurationVerdict.CHANGED
        assert comparison.changed_parts == [CompositePart.STRATEGY]
        [change] = comparison.changes
        assert (change.setting, change.before, change.after) == (
            'strategy_config.decision_logic_config.rsi_oversold', 30, 35)
        assert comparison.origins[change.setting] is ValueOrigin.PROFILE

    def test_a_safety_limit_changes_the_conditions_part(self, rendered):
        after = _changed(rendered, 'blocks.safety.max_drawdown_pct', 12.0)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(after))

        assert comparison.changed_parts == [CompositePart.CONDITIONS]
        assert comparison.changes[0].setting == 'safety.max_drawdown_pct'
        # The mock profile sets no safety block: the value is the shipped base layer's
        assert comparison.origins['safety.max_drawdown_pct'] is ValueOrigin.APP_CONFIG

    def test_a_neutral_value_is_no_change(self, rendered):
        after = _changed(rendered, 'blocks.display.enabled', not rendered['blocks']['display']['enabled'])

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(after))

        assert comparison.verdict is ConfigurationVerdict.SAME

    def test_the_account_currency_is_a_change_of_the_account_part(self, rendered):
        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered), account_currency='USD')),
            _current(rendered, account_currency='EUR'))

        assert comparison.changed_parts == [CompositePart.ACCOUNT]
        [change] = comparison.changes
        assert (change.setting, change.before, change.after) == ('account_currency', 'USD', 'EUR')
        assert comparison.origins['account_currency'] is ValueOrigin.RESOLVED

    def test_a_declared_partner_is_exempt(self, rendered):
        after = _changed(rendered, 'blocks.safety.max_drawdown_pct', 12.0)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered), profile_name='stage_soft')),
            _current(after, carry_over_from='stage_soft'))

        assert comparison.verdict is ConfigurationVerdict.PARTNER

    def test_claims_without_a_record_are_not_compared(self, rendered):
        comparison = compare_carried_configuration(_payload(None), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.NOT_COMPARED
        assert 'written before' in comparison.reason

    def test_nothing_carried_is_nothing_to_compare(self, rendered):
        comparison = compare_carried_configuration(_payload(None, claims=False), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.NOTHING_CARRIED

    def test_a_record_the_store_does_not_hold_is_not_compared(self, rendered):
        comparison = compare_carried_configuration(
            _payload(_carried('0' * 64)), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.NOT_COMPARED
        assert 'cannot be read' in comparison.reason

    def test_a_session_that_could_not_be_rendered_is_not_compared(self, rendered):
        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(None))

        assert comparison.verdict is ConfigurationVerdict.NOT_COMPARED


    def test_a_definition_that_cannot_be_loaded_is_not_compared(self, rendered, monkeypatch):
        """A broken definition in the workspace must not end a restart over an open position."""
        class _Broken(CompositeDefinitionManager):
            def __init__(self):
                raise ValueError('Invalid composite definition: unknown tier "definig"')
        monkeypatch.setattr(carry_over_configuration_validator, 'CompositeDefinitionManager',
                            _Broken)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.NOT_COMPARED
        assert 'composite definition could not be loaded' in comparison.reason

    def test_a_field_an_update_added_is_a_change_only_one_side_holds(self, rendered):
        """Case 3 of the rule: a release moved what the profile does not set — strict on purpose."""
        before = copy.deepcopy(rendered)
        del before['blocks']['safety']['baseline_mode']

        comparison = compare_carried_configuration(
            _payload(_carried(_register(before))), _current(rendered))

        assert comparison.verdict is ConfigurationVerdict.CHANGED
        assert comparison.one_sided == ['safety.baseline_mode']
        text = composite_change_refusal(comparison, ['a long position of 0.01 BTC'])
        # Written the way a profile writes it
        assert 'not set → "fixed"' in text
        assert 'an update added or dropped it' in text
        assert "cannot be restored in a file" in text


class TestWhereAValueComesFrom:
    """The layer that sets a value now — what the operator edits to undo a change."""

    @pytest.fixture
    def layers(self, monkeypatch, tmp_path):
        profile = tmp_path / 'my_bot.json'
        profile.write_text(json.dumps({
            'safety': {'max_drawdown_pct': 25.0},
            'strategy_config': {'decision_logic_config': {'rsi_oversold': 35}},
        }))
        monkeypatch.setattr(ConfigFileLoader, 'get_layers', staticmethod(lambda: (
            {'autotrader': {'execution': {'heartbeat_interval_ms': 500},
                            'session_end': {'orders': 'cancel'}}},
            {'autotrader': {'session_end': {'orders': 'leave'}}})))
        monkeypatch.setattr(MarketConfigFileLoader, 'get_layers', staticmethod(lambda: (
            {'brokers': [{'broker_type': 'kraken_spot', 'price_formation': 'order_book',
                          'market_type': 'crypto'}]},
            {'brokers': [{'broker_type': 'kraken_spot', 'market_type': 'crypto_spot'}]})))
        return str(profile)

    @pytest.mark.parametrize('setting, origin', [
        ('safety.max_drawdown_pct', ValueOrigin.PROFILE),
        ('strategy_config.decision_logic_config.rsi_oversold', ValueOrigin.PROFILE),
        ('strategy_config.decision_logic_config.rsi_overbought', ValueOrigin.CODE_DEFAULT),
        ('session_end.orders', ValueOrigin.APP_CONFIG_WORKSPACE),
        ('execution.heartbeat_interval_ms', ValueOrigin.APP_CONFIG),
        ('execution.market_data_stale_after_s', ValueOrigin.CODE_DEFAULT),
        ('market_entry.market_type', ValueOrigin.MARKET_CONFIG_WORKSPACE),
        ('market_entry.price_formation', ValueOrigin.MARKET_CONFIG),
        ('market_entry.server_clock', ValueOrigin.CODE_DEFAULT),
        ('account_currency', ValueOrigin.RESOLVED),
    ])
    def test_the_layer_that_wins_the_merge_is_named(self, layers, setting, origin):
        assert value_origins([setting], layers, 'kraken_spot')[setting] is origin


    def test_a_layer_that_cannot_be_read_leaves_the_origin_unknown(self, rendered, monkeypatch):
        """The origin labels the message; failing to read a layer must not undo the start."""
        def unreadable():
            raise OSError('app_config.json is gone')
        monkeypatch.setattr(ConfigFileLoader, 'get_layers', staticmethod(unreadable))
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(after))

        assert comparison.verdict is ConfigurationVerdict.CHANGED
        assert comparison.origins == {}
        assert 'origin unknown' in composite_change_refusal(comparison, ['a long position'])


class TestTheRefusalSaysEverythingNeededToDecide:

    @pytest.fixture
    def text(self, rendered) -> str:
        after = _changed(_changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35),
                         'blocks.safety.max_drawdown_pct', 12.0)
        comparison = compare_carried_configuration(
            _payload(_carried(_register(rendered))), _current(after))
        return composite_change_refusal(comparison, [
            "a long position of 0.004 BTC (pos_btcusd_47, this bot's position book)",
            '1 resting order(s) of ours — a sell limit of 0.004 at 71200.0'])

    def test_it_names_the_deployment_and_the_run_it_continues(self, text):
        assert _DEPLOYMENT in text and 'bot btcusd_test' in text and _WRITER_RUN in text

    def test_every_changed_value_with_before_after_part_and_origin(self, text):
        line = next(line for line in text.splitlines() if 'rsi_oversold' in line)
        assert '30 → 35' in line and 'strategy' in line and 'your profile' in line
        line = next(line for line in text.splitlines() if 'max_drawdown_pct' in line)
        # Values as the key compares them: an integral float reads as an integer
        assert '0 → 12' in line and 'conditions' in line and 'app_config.json as shipped' in line

    def test_what_is_still_open(self, text):
        assert '0.004 BTC' in text and 'a sell limit of 0.004 at 71200.0' in text

    def test_the_three_ways_on(self, text):
        assert 'Undo the change' in text
        assert 'Finish under the old configuration' in text
        assert '--accept-composite-change' in text


def _note() -> PositionCarryOver:
    """One remembered spot position, as the session that opened it wrote it down."""
    return PositionCarryOver(
        position_id='pos_btcusd_47', symbol='BTCUSD', direction='long', lots=0.01,
        original_lots=0.01, entry_price=61200.0, entry_time='2026-11-04T09:00:00+00:00',
        entry_type='market', contract_size=1)


def _adopter(executor, store, logger, current: CurrentConfiguration,
             accept: Optional[str] = None, dry_run: bool = False) -> ColdStartAdopter:
    """The boot step, wired to compare against what the carry-over recorded."""
    return ColdStartAdopter(
        executor=executor, store=store, config=ColdStartDefaults(adoption_mode='auto'),
        symbol='BTCUSD', logger=logger, dry_run=dry_run,
        compare_configuration=partial(compare_carried_configuration, current=current),
        accept_composite_change=accept)


def _open_book(spot_executor, store, carried: ConfigurationCarryOver) -> None:
    """A spot bot that left a position: its note, the coins at the venue, the configuration."""
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=47,
               open_positions=[_note()], venue_claims_configuration=carried)
    spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01, 'ZUSD': 1000.0})


def _resting_order(executor, store, carried: ConfigurationCarryOver) -> None:
    """A bot that left a limit order resting at the venue under its key."""
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=47,
               venue_claims_configuration=carried)
    executor.broker.adapter.set_broker_orders([make_broker_order(
        'OQ7X2A-OLD', build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47'))])


def _new_key(store_or_payload, after: Dict[str, Any],
             carried: Optional[ConfigurationCarryOver] = None) -> str:
    """
    The eight characters a refusal prints for the configuration a start would run with.

    Args:
        store_or_payload: The carry-over store the start reads, or a payload in its place
        after: The rendered profile the start runs with
        carried: The recorded configuration, when a payload stands in for the store

    Returns:
        The key's first eight characters
    """
    payload = (store_or_payload if isinstance(store_or_payload, ColdStartPayload)
               else store_or_payload.load())
    if carried is not None:
        payload = ColdStartPayload(session_keys=[PREVIOUS_SESSION_KEY],
                                   venue_claims_configuration=carried)
    return compare_carried_configuration(payload, _current(after)).current_key[:8]


class TestTheStartDecides:
    """The rule at the boot: refuse while something is open, unless this one start accepts it."""

    def test_spot_a_changed_strategy_over_an_open_position_is_refused(
            self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried(_register(rendered)))
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        adopter = _adopter(spot_executor, store, logger, _current(after))

        assert adopter.run() is False
        assert 'CONFIGURATION CHANGED WHILE POSITIONS ARE OPEN' in adopter.get_refusal()
        assert spot_executor.get_open_positions() == [], 'a refused boot applied the book'

    def test_margin_a_changed_condition_over_a_resting_order_is_refused(
            self, executor, store, logger, rendered):
        _resting_order(executor, store, _carried(_register(rendered)))
        after = _changed(rendered, 'blocks.safety.max_drawdown_pct', 12.0)
        adopter = _adopter(executor, store, logger, _current(after))

        assert adopter.run() is False
        assert 'resting order(s) of ours' in adopter.get_refusal()
        assert executor.get_active_orders() == [], 'a refused boot adopted the order'

    @pytest.mark.parametrize('model', ['executor', 'spot_executor'])
    def test_the_flag_lets_this_start_through_and_leaves_a_record(
            self, model, request, store, logger, rendered):
        executor = request.getfixturevalue(model)
        carried = _carried(_register(rendered))
        if model == 'spot_executor':
            _open_book(executor, store, carried)
        else:
            _resting_order(executor, store, carried)
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        key = _new_key(store, after)
        adopter = _adopter(executor, store, logger, _current(after), accept=key)

        assert adopter.run() is True
        assert f'CONFIGURATION CHANGE ACCEPTED (--accept-composite-change {key})' in (
            adopter.get_acceptance_notice())
        finding = adopter.get_configuration_finding()
        assert finding is not None and finding.check == 'composite_change_accepted'
        assert 'rsi_oversold' in finding.message and 'two series' in finding.message

    def test_the_refusal_prints_the_flag_that_accepts_exactly_this_change(
            self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried(_register(rendered)))
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        adopter = _adopter(spot_executor, store, logger, _current(after))

        assert adopter.run() is False
        assert f'--accept-composite-change {_new_key(store, after)}' in adopter.get_refusal()

    def test_a_key_that_names_another_composite_is_refused(
            self, spot_executor, store, logger, rendered):
        """The configuration changed again after the refusal printed its key."""
        _open_book(spot_executor, store, _carried(_register(rendered)))
        first = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        stale_key = _new_key(store, first)
        again = _changed(first, 'blocks.safety.max_drawdown_pct', 12.0)
        adopter = _adopter(spot_executor, store, logger, _current(again), accept=stale_key)

        assert adopter.run() is False
        assert f'--accept-composite-change {stale_key} does not name' in adopter.get_refusal()

    def test_a_flag_left_behind_never_accepts_the_next_change(
            self, spot_executor, store, logger, rendered):
        """Left in a service unit, the key accepted once keeps naming that one composite."""
        first = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        key = _new_key(ColdStartPayload(), first, carried=_carried(_register(rendered)))
        # The accepting start wrote the carry-over under its own configuration
        _open_book(spot_executor, store, _carried(_register(first)))
        second = _changed(first, 'blocks.safety.max_drawdown_pct', 12.0)
        adopter = _adopter(spot_executor, store, logger, _current(second), accept=key)

        assert adopter.run() is False, 'a standing flag accepted a second change unseen'

    @pytest.mark.parametrize('model', ['executor', 'spot_executor'])
    def test_a_change_with_nothing_open_begins_a_new_stretch(
            self, model, request, store, logger, rendered):
        executor = request.getfixturevalue(model)
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=47,
                   venue_claims_configuration=_carried(_register(rendered)))
        executor.broker.adapter.set_broker_orders([])
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        adopter = _adopter(executor, store, logger, _current(after))

        assert adopter.run() is True
        assert any('begins a new stretch' in line for line in logger.infos)
        assert adopter.get_configuration_finding() is None

    def test_the_flag_without_a_change_is_ignored_with_a_note(
            self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried(_register(rendered)))
        adopter = _adopter(spot_executor, store, logger, _current(rendered), accept='7f3a9c2e')

        assert adopter.run() is True
        assert any('--accept-composite-change 7f3a9c2e: nothing to accept' in line
                   for line in logger.infos)
        assert adopter.get_acceptance_notice() == ''

    def test_an_open_position_that_could_not_be_compared_starts_with_a_finding(
            self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried('0' * 64))
        adopter = _adopter(spot_executor, store, logger, _current(rendered))

        assert adopter.run() is True
        finding = adopter.get_configuration_finding()
        assert finding is not None and finding.check == 'carried_configuration_not_compared'

    def test_a_declared_partner_continues_across_its_changes(
            self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried(_register(rendered), profile_name='stage_soft'))
        after = _changed(rendered, 'blocks.safety.spot_liquidate_to_quote', True)
        adopter = _adopter(spot_executor, store, logger,
                           _current(after, carry_over_from='stage_soft'))

        assert adopter.run() is True
        assert any('declared partner' in line for line in logger.infos)

    def test_a_dry_run_compares_nothing(self, spot_executor, store, logger, rendered):
        _open_book(spot_executor, store, _carried(_register(rendered)))
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)
        adopter = _adopter(spot_executor, store, logger, _current(after), dry_run=True)

        assert adopter.run() is True
        assert adopter.get_configuration_comparison() is None


class TestTheNextStartNeedsNothing:
    """An accepted start writes the carry-over under its own configuration (#626)."""

    def test_the_store_keeps_what_the_claims_were_made_under(self, store, rendered):
        carried = _carried(_register(rendered))
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=1,
                   venue_claims_configuration=carried)
        store.save(session_key='', highest_position_counter=0)

        assert store.load().venue_claims_configuration == carried

    def test_the_successor_compares_against_the_accepting_start(self, rendered):
        after = _changed(rendered, 'strategy_config.decision_logic_config.rsi_oversold', 35)

        comparison = compare_carried_configuration(
            _payload(_carried(_register(after))), _current(after))

        assert comparison.verdict is ConfigurationVerdict.SAME
