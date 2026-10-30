"""
Order Features (#625) — a logic declares what it uses, a run refuses what its venue does not hold.

A venue's API holds a stop or a target for a position, or it does not, and the framework does not
rebuild what it lacks. So the answer has to come before the run: an adapter states what its API
takes, a logic states what it uses, and the two are compared in the batch's main process, at a
scenario's own start and at a session's. A backtest may declare an exception, narrowly, recorded
with the run. Whatever gets past the start is refused at order time, on all four entry points.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.decision_logic.core.cautious_macd import CautiousMacd
from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.decision_logic.core.test_probes.deterministic_probe import DeterministicProbe
from python.framework.decision_logic.core.trend_channel_reference import TrendChannelReference
from python.framework.exceptions.order_feature_errors import OrderFeatureRefusedError
from python.framework.reporting.store.run_provenance_builder import consumption_record
from python.framework.reporting.store.run_results_ledger import COLUMN_REDUCTION, LEDGER_COLUMNS
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_results_types import Reduction
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    ModificationRejectionReason,
    ModificationResult,
    OpenOrderRequest,
    OrderAction,
    OrderCapabilities,
    OrderDirection,
    OrderFeature,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectionReason,
)
from python.framework.validators.decision_logic_hook_validator import check_order_feature_declaration
from python.framework.validators.order_feature_validator import (
    features_the_venue_lacks,
    order_feature_refusal,
)
from python.framework.validators.scenario_validator import ScenarioValidator
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
_BOTH = frozenset({OrderFeature.STOP_LOSS, OrderFeature.TAKE_PROFIT})
_SIMULATION_CARRIES = _BOTH


class _FakeExecutor:
    """What the trading API asks of an executor, recording what reaches it."""

    def __init__(self, offered=frozenset(), carried=_SIMULATION_CARRIES):
        self.broker = SimpleNamespace(
            get_order_capabilities=lambda: OrderCapabilities(offered_order_features=offered),
            adapter=SimpleNamespace(get_capability_label=lambda: 'some_venue'),
        )
        self._carried = carried
        self.opened: List[OpenOrderRequest] = []
        self.modifies: List[str] = []
        self.guard_rejections: List[OrderResult] = []

    def get_supported_order_types(self) -> frozenset:
        return frozenset(OrderType)

    def get_supported_order_features(self) -> frozenset:
        return self._carried

    def add_order_outcome_listener(self, listener) -> None:
        pass

    def resolve_order_side(self, side: OrderSide) -> OrderDirection:
        return OrderDirection.LONG

    def get_current_time(self) -> datetime:
        return _T0

    def get_market_data_status(self):
        return None

    def get_unresolved_at_ceiling(self) -> set:
        return set()

    def record_guard_rejection(self, result: OrderResult) -> None:
        self.guard_rejections.append(result)

    def open_order(self, request: OpenOrderRequest) -> OrderResult:
        self.opened.append(request)
        return OrderResult(order_id='ord_1', status=OrderStatus.PENDING, action=OrderAction.OPEN)

    def modify_position(self, position_id: str, **kwargs) -> ModificationResult:
        self.modifies.append(position_id)
        return ModificationResult(success=True)

    def modify_limit_order(self, order_id: str, **kwargs) -> ModificationResult:
        self.modifies.append(order_id)
        return ModificationResult(success=True)

    def modify_stop_order(self, order_id: str, **kwargs) -> ModificationResult:
        self.modifies.append(order_id)
        return ModificationResult(success=True)


def _api(declared=(), offered=frozenset(), beyond=(), carried=_SIMULATION_CARRIES) -> tuple:
    """
    A trading API over a stand-in executor.

    Args:
        declared: What the logic declared
        offered: What the venue states
        beyond: What the set declared as an exception
        carried: What the pipeline carries

    Returns:
        (the API, its executor)
    """
    executor = _FakeExecutor(offered, carried)
    api = DecisionTradingApi(executor, required_order_types=[], required_order_features=declared,
                             features_beyond_venue=beyond, logic_name='my_algo')
    return api, executor


def _send(api: DecisionTradingApi, **levels) -> OrderResult:
    return api.send_order(symbol='EURUSD', order_type=OrderType.MARKET, side=OrderSide.BUY,
                          lots=0.1, **levels)


class TestWhatAnAdapterStates:

    def test_a_venue_that_holds_both_levels_on_the_position(self):
        assert Mt5Adapter.get_order_capabilities(_adapter_shell()).offered_order_features == _BOTH

    @pytest.mark.parametrize('adapter_class', [KrakenAdapter, MockBrokerAdapter])
    def test_a_venue_whose_api_holds_none(self, adapter_class):
        capabilities = adapter_class.get_order_capabilities(_adapter_shell())

        assert capabilities.offered_order_features == frozenset()

    def test_a_capability_answers_for_each_feature(self):
        capabilities = OrderCapabilities(offered_order_features=frozenset({OrderFeature.STOP_LOSS}))

        assert capabilities.offers_order_feature(OrderFeature.STOP_LOSS)
        assert not capabilities.offers_order_feature(OrderFeature.TAKE_PROFIT)

    def test_the_runtime_guard_knows_every_feature(self):
        """A feature with no branch in the guard would pass at order time without a word."""
        api, _ = _api()

        guarded = set(api._undeclared_features(1.0, 1.0))

        assert guarded == set(OrderFeature)


class TestALogicHasToAnswer:

    def test_a_logic_that_does_not_override_is_refused(self):
        class _Silent(AbstractDecisionLogic):
            pass

        message = check_order_feature_declaration(_Silent)

        assert 'get_required_order_features' in message
        assert '_Silent' in message

    @pytest.mark.parametrize('logic_class', [CautiousMacd, TrendChannelReference])
    def test_a_logic_keeping_its_own_exits_declares_none(self, logic_class):
        assert check_order_feature_declaration(logic_class) is None
        assert logic_class.get_required_order_features({}) == []

    def test_the_probe_declares_what_its_sequences_send(self):
        config = {'trade_sequence': [{'tick_number': 1, 'stop_loss': 95.0}],
                  'modify_sequence': [{'tick_number': 5, 'take_profit': 105.0}]}

        assert set(DeterministicProbe.get_required_order_features(config)) == _BOTH

    def test_the_probe_declares_only_those(self):
        config = {'trade_sequence': [{'tick_number': 1, 'stop_loss': 95.0}]}

        assert DeterministicProbe.get_required_order_features(config) == [OrderFeature.STOP_LOSS]

    def test_the_probe_keeping_its_own_levels_declares_none(self):
        config = {'exit_mode': 'own', 'trade_sequence': [{'tick_number': 1, 'stop_loss': 95.0}]}

        assert DeterministicProbe.get_required_order_features(config) == []

    def test_the_field_study_declares_its_stop_only_with_the_phase(self):
        with_phase = {'phase_sequence': [{'phase_type': 'protective_level'}]}
        without = {'phase_sequence': [{'phase_type': 'idle'}]}

        assert LiveFieldStudy.get_required_order_features(with_phase) == [OrderFeature.STOP_LOSS]
        assert LiveFieldStudy.get_required_order_features(without) == []


class TestTheSentence:

    def test_it_names_the_logic_the_venue_and_the_way_out(self):
        message = order_feature_refusal(
            'CORE/my_algo', 'some_venue', [OrderFeature.STOP_LOSS, OrderFeature.TAKE_PROFIT])

        assert 'CORE/my_algo declares stop_loss, take_profit' in message
        assert 'some_venue holds no stop_loss, take_profit for a position' in message
        assert 'OwnExitLevels' in message
        assert 'features_beyond_venue' not in message

    def test_only_a_backtest_is_told_of_the_exception(self):
        message = order_feature_refusal(
            'my_algo', 'some_venue', [OrderFeature.STOP_LOSS], exception_possible=True)

        assert 'features_beyond_venue' in message

    def test_nothing_short_is_no_refusal(self):
        assert order_feature_refusal('my_algo', 'some_venue', []) is None

    def test_the_venue_lacks_what_it_does_not_state(self):
        capabilities = OrderCapabilities(offered_order_features=frozenset({OrderFeature.STOP_LOSS}))

        assert features_the_venue_lacks(sorted(_BOTH, key=lambda f: f.value), capabilities) == [
            OrderFeature.TAKE_PROFIT]


class TestTheStartRefuses:

    def test_a_declared_feature_the_venue_lacks(self):
        with pytest.raises(OrderFeatureRefusedError, match='some_venue holds no stop_loss'):
            _api(declared=[OrderFeature.STOP_LOSS])

    def test_a_declared_feature_the_venue_holds_passes(self):
        _api(declared=[OrderFeature.STOP_LOSS], offered=_BOTH)

    def test_an_exception_lets_a_backtest_through(self):
        _api(declared=[OrderFeature.STOP_LOSS], beyond=[OrderFeature.STOP_LOSS])

    def test_an_exception_for_the_other_feature_does_not(self):
        with pytest.raises(OrderFeatureRefusedError):
            _api(declared=[OrderFeature.STOP_LOSS], beyond=[OrderFeature.TAKE_PROFIT])

    def test_a_venue_that_holds_it_on_a_pipeline_that_does_not(self):
        """A session on such a venue: no live path passes a level to its venue."""
        with pytest.raises(OrderFeatureRefusedError, match='pipeline does not carry stop_loss'):
            _api(declared=[OrderFeature.STOP_LOSS], offered=_BOTH, carried=frozenset())

    def test_no_declaration_needs_nothing(self):
        _api(carried=frozenset())


class TestAnUndeclaredLevelIsRefusedAtOrderTime:

    def test_on_an_order(self):
        api, executor = _api()

        result = _send(api, stop_loss=95.0)

        assert result.status == OrderStatus.DENIED
        assert result.rejection_reason == RejectionReason.ORDER_FEATURE_NOT_DECLARED
        assert 'stop_loss' in result.rejection_message
        assert executor.opened == []
        assert executor.guard_rejections == [result]

    def test_it_names_each_one(self):
        api, _ = _api()

        result = _send(api, stop_loss=95.0, take_profit=105.0)

        assert 'stop_loss, take_profit' in result.rejection_message

    def test_a_declared_level_goes_out(self):
        api, executor = _api(declared=[OrderFeature.STOP_LOSS], offered=_BOTH)

        result = _send(api, stop_loss=95.0)

        assert result.status == OrderStatus.PENDING
        assert len(executor.opened) == 1

    def test_the_other_level_is_still_refused(self):
        api, executor = _api(declared=[OrderFeature.STOP_LOSS], offered=_BOTH)

        result = _send(api, take_profit=105.0)

        assert result.rejection_reason == RejectionReason.ORDER_FEATURE_NOT_DECLARED
        assert executor.opened == []

    def test_an_order_without_a_level_goes_out(self):
        api, executor = _api()

        assert _send(api).status == OrderStatus.PENDING
        assert len(executor.opened) == 1

    def test_it_arms_no_cooldown(self):
        """A decision bug, not a venue that refuses: the next clean order must not be blocked."""
        api, executor = _api()

        _send(api, stop_loss=95.0)

        assert _send(api).status == OrderStatus.PENDING
        assert len(executor.opened) == 1

    @pytest.mark.parametrize('entry_point', [
        'modify_position', 'modify_limit_order', 'modify_stop_order'])
    def test_on_every_modify(self, entry_point):
        api, executor = _api()

        result = getattr(api, entry_point)('ord_1', stop_loss=95.0)

        assert result.success is False
        assert result.rejection_reason == ModificationRejectionReason.ORDER_FEATURE_NOT_DECLARED
        assert result.order_id == 'ord_1'
        assert executor.modifies == []

    @pytest.mark.parametrize('entry_point', [
        'modify_position', 'modify_limit_order', 'modify_stop_order'])
    def test_removing_a_level_always_passes(self, entry_point):
        api, executor = _api()

        result = getattr(api, entry_point)('ord_1', stop_loss=None, take_profit=None)

        assert result.success is True
        assert executor.modifies == ['ord_1']

    @pytest.mark.parametrize('entry_point', [
        'modify_position', 'modify_limit_order', 'modify_stop_order'])
    def test_leaving_a_level_alone_passes(self, entry_point):
        api, executor = _api()

        assert getattr(api, entry_point)('ord_1').success is True
        assert executor.modifies == ['ord_1']


def _scenario(name: str, config: Dict[str, Any], beyond=()) -> SingleScenario:
    """
    A scenario on the stand-in broker, running the probe with the given configuration.

    Args:
        name: Its name
        config: The probe's decision_logic_config
        beyond: What its set declared as an exception

    Returns:
        The scenario
    """
    return SimpleNamespace(
        name=name, broker_type=BrokerType.KRAKEN_SPOT, validation_result=[],
        features_beyond_venue=list(beyond),
        strategy_config={'decision_logic_type': 'CORE/test_probes/deterministic_probe',
                         'decision_logic_config': config})


def _broker_map(offered=frozenset()) -> Dict[BrokerType, Any]:
    broker_config = SimpleNamespace(
        get_order_capabilities=lambda: OrderCapabilities(offered_order_features=offered),
        adapter=SimpleNamespace(get_capability_label=lambda: 'some_venue'))
    return {BrokerType.KRAKEN_SPOT: SimpleNamespace(broker_config=broker_config)}


def _checks_of(scenario) -> List[str]:
    return [finding.check for result in scenario.validation_result
            for finding in result.findings]


class _NullLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_WITH_STOP = {'trade_sequence': [{'tick_number': 1, 'stop_loss': 95.0}]}


class TestTheBatchRefusesInItsMainProcess:

    def test_a_scenario_whose_venue_lacks_it_is_excluded(self):
        refused = _scenario('with_stop', _WITH_STOP)
        clean = _scenario('without', {})

        ScenarioValidator.validate_order_features([refused, clean], _NullLogger(), _broker_map())

        assert _checks_of(refused) == ['order_feature_refused']
        assert _checks_of(clean) == []

    def test_the_sentence_tells_a_backtest_of_the_exception(self):
        refused = _scenario('with_stop', _WITH_STOP)

        ScenarioValidator.validate_order_features([refused], _NullLogger(), _broker_map())

        assert 'features_beyond_venue' in refused.validation_result[0].findings[0].message

    def test_a_venue_that_holds_it_passes(self):
        scenario = _scenario('with_stop', _WITH_STOP)

        ScenarioValidator.validate_order_features([scenario], _NullLogger(), _broker_map(_BOTH))

        assert _checks_of(scenario) == []

    def test_the_exception_lets_it_through(self):
        scenario = _scenario('with_stop', _WITH_STOP, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([scenario], _NullLogger(), _broker_map())

        assert _checks_of(scenario) == []

    def test_an_exception_no_logic_needs_is_refused(self):
        scenario = _scenario('without', {}, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([scenario], _NullLogger(), _broker_map())

        assert _checks_of(scenario) == ['feature_beyond_venue_unneeded']

    def test_an_exception_for_what_the_venue_holds_is_refused(self):
        scenario = _scenario('with_stop', _WITH_STOP, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([scenario], _NullLogger(), _broker_map(_BOTH))

        assert _checks_of(scenario) == ['feature_beyond_venue_unneeded']

    def test_one_scenario_needing_it_is_enough_for_the_set(self):
        needs = _scenario('with_stop', _WITH_STOP, beyond=[OrderFeature.STOP_LOSS])
        does_not = _scenario('without', {}, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([needs, does_not], _NullLogger(), _broker_map())

        assert _checks_of(needs) == []
        assert _checks_of(does_not) == []


_FIXTURE = Path('tests/fixtures/scenario_sets/cascade/global_overrides_app.json')


def _load_set(tmp_path: Path, **top_level) -> Any:
    """
    Load a fixture scenario set with keys added at its top level.

    Args:
        tmp_path: Where the changed copy is written
        top_level: The keys to add

    Returns:
        The loaded configuration
    """
    data = json.loads(_FIXTURE.read_text(encoding='utf-8'))
    data.update(top_level)
    path = tmp_path / 'my_set.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return ScenarioConfigLoader().load_config(str(path))


class TestTheSetDeclaresTheException:

    def test_nothing_declared_is_empty(self, tmp_path):
        loaded = _load_set(tmp_path)

        assert loaded.features_beyond_venue == []
        assert all(s.features_beyond_venue == [] for s in loaded.scenarios)

    def test_it_reaches_every_scenario(self, tmp_path):
        loaded = _load_set(tmp_path, features_beyond_venue=['stop_loss', 'take_profit'])

        assert set(loaded.features_beyond_venue) == _BOTH
        assert loaded.scenarios
        assert all(set(s.features_beyond_venue) == _BOTH for s in loaded.scenarios)

    def test_an_unknown_name_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="features_beyond_venue names \\['stop_lose'\\]"):
            _load_set(tmp_path, features_beyond_venue=['stop_lose'])

    def test_a_misspelt_key_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match='features_beyond_venu'):
            _load_set(tmp_path, features_beyond_venu=['stop_loss'])


class TestItIsRecorded:

    def test_the_run_header_serves_the_values(self):
        header = RunHeader(run_id='run_1', start_time=_T0, run_type='simulation', run_name='my_set',
                           features_beyond_venue=[OrderFeature.STOP_LOSS])

        assert header.model_dump(mode='json')['features_beyond_venue'] == ['stop_loss']

    def test_the_ledger_row_says_it_over_all_scenarios(self):
        scenarios = [_consumption_scenario([OrderFeature.TAKE_PROFIT, OrderFeature.STOP_LOSS]),
                     _consumption_scenario([OrderFeature.STOP_LOSS])]

        record = consumption_record(scenarios)

        assert record['features_beyond_venue'] == 'stop_loss,take_profit'

    def test_a_run_without_one_records_nothing(self):
        assert consumption_record([_consumption_scenario([])])['features_beyond_venue'] == ''

    def test_the_ledger_column_is_declared_with_how_it_combines(self):
        assert 'features_beyond_venue' in LEDGER_COLUMNS
        assert COLUMN_REDUCTION['features_beyond_venue'] is Reduction.UNION


def _consumption_scenario(features: List[OrderFeature]) -> Any:
    """
    A scenario as the ledger's consumption fields read it.

    Args:
        features: What its set declared as an exception

    Returns:
        The stand-in
    """
    return SimpleNamespace(data_format_versions=[], origin_classes=[], origin_evidence_grades=[],
                           price_bases=[], features_beyond_venue=features)


class TestASessionCannotDeclareIt:

    def test_the_profile_key_is_refused(self, tmp_path):
        profile = json.loads(Path(
            'configs/autotrader_profiles/mock/sl_triggered_test.json').read_text(encoding='utf-8'))
        profile['features_beyond_venue'] = ['stop_loss']
        path = tmp_path / 'my_profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')

        with pytest.raises(ValueError, match='features_beyond_venue'):
            load_autotrader_config(str(path))


def _adapter_shell() -> Any:
    """What the adapters' get_order_capabilities reads of themselves, without a connection."""
    return SimpleNamespace(_hedging_allowed=False, _charges_fee_per_fill=lambda: False)
