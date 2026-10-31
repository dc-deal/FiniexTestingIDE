"""
Order Features (#625) — a logic declares what it uses, a run refuses what its venue does not hold.

A venue's API holds a stop or a target for a position, or it does not, and the framework does not
rebuild what it lacks. So the answer has to come before the run: an adapter states what its API
takes, a logic states what it uses, and the two are compared in the batch's main process, at a
scenario's own start and at a session's. A backtest may declare an exception, narrowly, recorded
with the run. Whatever gets past the start is refused at order time, on every entry point.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.decision_logic.core.cautious_macd import CautiousMacd
from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.decision_logic.core.test_probes.deterministic_probe import DeterministicProbe
from python.framework.decision_logic.core.trend_channel_reference import TrendChannelReference
from python.framework.exceptions.order_feature_errors import OrderFeatureRefusedError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.persistence.cold_start_state_store import (
    ColdStartStateStore,
    carried_level_refusal,
)
from python.framework.reporting.store.run_provenance_builder import consumption_record
from python.framework.reporting.store.run_results_ledger import (
    COLUMN_REDUCTION,
    LEDGER_COLUMNS,
    RunResultsLedger,
)
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.api.report_types import RunHeader
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.run_results_types import Reduction
from python.framework.types.persistence_types import CarryOverLoadOutcome
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.types.trading_env_types.order_types import (
    ModificationRejectionReason,
    ModificationResult,
    ModificationStatus,
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
from python.scenario.scenario_strategy_runner import initialize_batch_and_run
from tests.shared.recording_logger import RecordingLogger

_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
_BOTH = frozenset({OrderFeature.STOP_LOSS, OrderFeature.TAKE_PROFIT})
_SIMULATION_CARRIES = _BOTH
_STORED_POSITION = {
    'position_id': 'pos_1', 'symbol': 'BTCUSD', 'direction': 'long', 'lots': 0.01,
    'original_lots': 0.01, 'entry_price': 50000.0, 'entry_time': '2026-10-01T00:00:00+00:00',
    'entry_type': 'market', 'entry_tick_value': 1.0, 'entry_bid': 49999.0, 'entry_ask': 50001.0,
    'stop_loss': None, 'take_profit': None, 'broker_ref': 'OABC-123', 'comment': '',
    'status': 'open', 'digits': 1, 'contract_size': 1.0, 'pip_size': 0.1, 'price_unit': 'USD',
    'entry_tick_index': 0, 'mae_pnl': 0.0, 'mfe_pnl': 0.0, 'mae_price': 0.0, 'mfe_price': 0.0,
}


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
        self.undeclared_warned: List[List[OrderFeature]] = []

    def get_supported_order_types(self) -> frozenset:
        return frozenset(OrderType)

    def get_supported_order_features(self) -> frozenset:
        return self._carried

    def warn_undeclared_level(self, undeclared) -> None:
        self.undeclared_warned.append(list(undeclared))

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

    def test_an_adapter_cannot_leave_it_out(self):
        """A stated empty set and a forgotten one would read alike, so there is no default."""
        with pytest.raises(TypeError, match='offered_order_features'):
            OrderCapabilities(market_orders=True)

    def test_a_capability_answers_for_each_feature(self):
        capabilities = OrderCapabilities(offered_order_features=frozenset({OrderFeature.STOP_LOSS}))

        assert capabilities.offers_order_feature(OrderFeature.STOP_LOSS)
        assert not capabilities.offers_order_feature(OrderFeature.TAKE_PROFIT)

    def test_the_runtime_guard_knows_every_feature(self):
        """A feature with no branch in the guard would pass at order time without a word."""
        api, _ = _api()

        result = _send(api, stop_loss=95.0, take_profit=105.0)

        assert all(feature.value in result.rejection_message for feature in OrderFeature)


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

    def test_the_field_study_declares_none(self):
        assert LiveFieldStudy.get_required_order_features({}) == []


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
    scenario = SimpleNamespace(
        name=name, broker_type=BrokerType.KRAKEN_SPOT, validation_result=[],
        features_beyond_venue=list(beyond),
        strategy_config={'decision_logic_type': 'CORE/test_probes/deterministic_probe',
                         'decision_logic_config': config})
    scenario.is_valid = lambda: all(result.is_valid for result in scenario.validation_result)
    return scenario


def _excluded(scenario) -> Any:
    """
    Exclude a scenario for a reason of its own, as an earlier check does.

    Args:
        scenario: The scenario

    Returns:
        The scenario
    """
    scenario.validation_result.append(ValidationResult(scenario.name, [ValidationFinding(
        severity=Severity.ERROR, check='some_earlier_check', domain=ValidationDomain.BROKER,
        message='no end date', scope=scenario.name)]))
    return scenario


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

    def test_a_scenario_excluded_for_its_own_reason_still_counts_as_needing_it(self):
        """Told that no scenario needs it, an innocent scenario would be refused with a falsehood."""
        needs = _excluded(_scenario('with_stop', _WITH_STOP, beyond=[OrderFeature.STOP_LOSS]))
        does_not = _scenario('without', {}, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([needs, does_not], _NullLogger(), _broker_map())

        assert _checks_of(needs) == ['some_earlier_check'], 'one reason is enough for an excluded one'
        assert _checks_of(does_not) == []

    def test_a_need_that_cannot_be_told_leaves_the_exception_unjudged(self):
        unknown = _scenario('unknown', {}, beyond=[OrderFeature.STOP_LOSS])
        unknown.strategy_config['decision_logic_type'] = 'CORE/no_such_logic'
        does_not = _scenario('without', {}, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([unknown, does_not], _NullLogger(), _broker_map())

        assert _checks_of(does_not) == []

    def test_the_exception_is_warned_about_before_the_run(self):
        logger = RecordingLogger()
        needs = _scenario('with_stop', _WITH_STOP, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features([needs], logger, _broker_map())

        warned = [text for level, text in logger.lines if level == 'warning']
        assert len(warned) == 1 and 'BEYOND THE VENUE' in warned[0] and 'stop_loss' in warned[0]

    def test_a_set_without_one_is_not_warned_about(self):
        logger = RecordingLogger()

        ScenarioValidator.validate_order_features(
            [_scenario('without', {})], logger, _broker_map())

        assert not [text for level, text in logger.lines if level == 'warning']

    def test_a_sweep_does_not_judge_it_per_combination(self):
        """One arm of a grid may need what another does not."""
        does_not = _scenario('without', {}, beyond=[OrderFeature.STOP_LOSS])

        ScenarioValidator.validate_order_features(
            [does_not], _NullLogger(), _broker_map(), exception_held_narrow=False)

        assert _checks_of(does_not) == []

    def test_a_sweep_still_refuses_what_its_venue_lacks(self):
        needs = _scenario('with_stop', _WITH_STOP)

        ScenarioValidator.validate_order_features(
            [needs], _NullLogger(), _broker_map(), exception_held_narrow=False)

        assert _checks_of(needs) == ['order_feature_refused']


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


class TestTheExceptionThroughARealBatch:
    """
    Only a real batch crosses the process boundary and writes the header, the warning and the
    ledger row: a lost line in that chain leaves every test of its parts green.
    """

    _SET = 'tests/fixtures/scenario_sets/order_features/exception_declared.json'
    _NAME = 'order_features_exception_declared'

    @pytest.fixture(scope='class')
    @classmethod
    def summary(cls):
        return initialize_batch_and_run(ScenarioConfigLoader().load_config(cls._SET),
                                        AppConfigManager())

    def _run_dir(self) -> Path:
        root = Path(AppConfigManager().get_file_logging_config_object().run_logs.simulation)
        return [header.parent for header in sorted(root.rglob('header.json'))
                if self._NAME in str(header) and (header.parent / 'io').exists()][-1]

    def test_the_scenario_runs(self, summary):
        assert summary.get_exit_code() == 0
        assert all(result.success for result in summary.process_result_list), (
            'the scenario process did not receive the exception')

    def test_the_header_records_it(self, summary):
        header = RunHeader.model_validate_json(
            (self._run_dir() / 'header.json').read_text(encoding='utf-8'))

        assert header.features_beyond_venue == [OrderFeature.STOP_LOSS]

    def test_the_report_warns_about_it(self, summary):
        report = (self._run_dir() / 'io' / 'warnings_errors.json').read_text(encoding='utf-8')

        assert 'features_beyond_venue' in report and 'BEYOND THE VENUE' in report

    def test_the_ledger_row_records_it(self, summary):
        rows = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path())).read_rows(
            scenario_set_name=self._NAME)

        assert rows and all(row.features_beyond_venue == 'stop_loss' for row in rows)


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


class TestALiveExecutorHoldsNoLevel:
    """The backstop behind the start and the trading API: a level accepted here would be recorded
    on the position and enforced by nobody."""

    def _executor(self, spot_mode: bool = False) -> tuple:
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = LiveTradeExecutor(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0, account_currency='USD',
            logger=GlobalLogger('OrderFeatures'), spot_mode=spot_mode,
            initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None)
        mock.feed_tick(executor, bid=50000.0, ask=50001.0)
        return mock, executor

    @pytest.mark.parametrize('spot_mode', [False, True])
    @pytest.mark.parametrize('level', ['stop_loss', 'take_profit'])
    def test_an_order_with_a_level_is_denied(self, spot_mode, level):
        _, executor = self._executor(spot_mode)

        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, **{level: 49000.0}))

        assert result.status == OrderStatus.DENIED
        assert result.rejection_reason == RejectionReason.ORDER_FEATURE_NOT_DECLARED

    def test_a_level_on_a_position_is_refused(self):
        mock, executor = self._executor()
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.01))
        mock.await_submit_confirmation(executor)
        position = executor.get_open_positions()[0]

        result = executor.modify_position(position.position_id, new_stop_loss=49000.0)

        assert result.success is False
        assert result.rejection_reason is ModificationRejectionReason.ORDER_FEATURE_NOT_DECLARED
        assert position.stop_loss is None

    @pytest.mark.parametrize('level', ['new_stop_loss', 'new_take_profit'])
    def test_a_level_on_a_stop_order_is_refused(self, level):
        _, executor = self._executor()

        result = executor.modify_stop_order('any_order', **{level: 49000.0})

        assert result.success is False
        assert result.rejection_reason is ModificationRejectionReason.ORDER_FEATURE_NOT_DECLARED

    def test_removing_a_level_from_a_stop_order_is_not_refused(self):
        _, executor = self._executor()

        result = executor.modify_stop_order('any_order', new_stop_loss=None)

        assert result.rejection_reason is not ModificationRejectionReason.ORDER_FEATURE_NOT_DECLARED

    def test_a_breached_price_closes_nothing_on_its_own(self):
        """The level check is the simulation's: a session's exits are its logic's."""
        mock, executor = self._executor()
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG, lots=0.01))
        mock.await_submit_confirmation(executor)
        position = executor.get_open_positions()[0]
        position.stop_loss = 49000.0

        mock.feed_tick(executor, bid=48000.0, ask=48001.0)

        assert not executor.is_pending_close(position.position_id)
        assert executor.get_execution_stats().sl_tp_triggered == 0


class TestASimulationEnforcesTheLevelsPositionsCarry:

    def _simulator(self) -> TradeSimulator:
        return TradeSimulator(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=10000.0, account_currency='USD',
            logger=GlobalLogger('OrderFeatures'), seeds={'inbound_latency_seed': 42},
            inbound_latency_min_ms=0, inbound_latency_max_ms=0)

    def _tick(self, bid: float, ask: float) -> TickData:
        return TickData(timestamp=_T0, symbol='BTCUSD', bid=bid, ask=ask, volume=1.0)

    @pytest.mark.parametrize('direction, breach, level, reason', [
        (OrderDirection.LONG, (48900.0, 48901.0), {'stop_loss': 49000.0}, CloseReason.SL_TRIGGERED),
        (OrderDirection.LONG, (51100.0, 51101.0), {'take_profit': 51000.0}, CloseReason.TP_TRIGGERED),
    ])
    def test_it_fills_at_the_level_itself(self, direction, breach, level, reason):
        sim = self._simulator()
        sim.on_tick(self._tick(50000.0, 50001.0))
        sim.open_order(OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                                        direction=direction, lots=0.01, **level))
        sim.on_tick(self._tick(50000.0, 50001.0))
        assert len(sim.get_open_positions()) == 1

        sim.on_tick(self._tick(*breach))

        trade, = sim.portfolio.get_trade_history()
        assert trade.close_reason == reason
        assert trade.exit_price == pytest.approx(next(iter(level.values())))
        assert trade.exit_level == pytest.approx(next(iter(level.values())))
        assert sim.get_execution_stats().sl_tp_triggered == 1


class TestAStoredBookWithALevelIsRefused:

    _PATH = Path('my_book.json')

    def test_a_clean_book_passes(self):
        snapshot = {'open_positions': [{'position_id': 'pos_1', 'stop_loss': None,
                                        'protective_order_id': None}]}

        assert carried_level_refusal(snapshot, self._PATH) is None

    def test_an_empty_book_passes(self):
        assert carried_level_refusal({}, self._PATH) is None

    def test_a_stop_resting_at_the_venue_is_named_with_what_to_do(self):
        snapshot = {'open_positions': [{'position_id': 'pos_1',
                                        'protective_broker_ref': 'OABC-123'}]}

        message = carried_level_refusal(snapshot, self._PATH)

        assert 'pos_1' in message and 'OABC-123' in message
        assert 'cancel' in message and 'my_book.json' in message

    def test_a_reference_not_confirmed_yet_is_named_too(self):
        snapshot = {'open_positions': [{'position_id': 'pos_1', 'protective_order_id': 'ord_7'}]}

        assert 'ord_7' in carried_level_refusal(snapshot, self._PATH)

    def test_a_level_on_the_position_is_named(self):
        snapshot = {'open_positions': [{'position_id': 'pos_1', 'stop_loss': 49000.0,
                                        'take_profit': 51000.0}]}

        message = carried_level_refusal(snapshot, self._PATH)

        assert 'stop_loss and take_profit' in message

    @staticmethod
    def _store_holding(tmp_path, open_positions) -> ColdStartStateStore:
        """
        A carry-over store over a stored book, written as a session writes it.

        Args:
            tmp_path: Where the document goes
            open_positions: What its book holds

        Returns:
            The store, not read yet
        """
        document = tmp_path / f"{carry_over_key('my_profile', 'BTCUSD', '')}.json"
        document.write_text(json.dumps({
            'schema_version': 1, 'store_id': 'cold_start_state',
            'saved_at_utc': '2026-10-01T00:00:00+00:00', 'written_by_run_id': 'my_run',
            'profile': 'my_profile', 'symbol': 'BTCUSD', 'bot_id': '', 'carry_over_from': '',
            'snapshot': {'open_positions': open_positions}}), encoding='utf-8')
        return ColdStartStateStore(tmp_path, 'my_profile', 'BTCUSD', RecordingLogger())

    def test_the_load_carries_the_refusal_and_the_book_stays_readable(self, tmp_path):
        store = self._store_holding(tmp_path, [dict(_STORED_POSITION, stop_loss=49000.0)])

        payload = store.load()

        assert len(payload.open_positions) == 1
        assert store.get_last_load().outcome is CarryOverLoadOutcome.ACCEPTED
        assert 'stop_loss' in store.get_last_load().level_refusal

    def test_a_clean_book_carries_none(self, tmp_path):
        store = self._store_holding(tmp_path, [_STORED_POSITION])

        store.load()

        assert store.get_last_load().level_refusal is None

    def test_a_book_written_before_the_rebuilt_stop_went_still_loads(self, tmp_path):
        """The upgrade: its removed keys stand at their empty values, and its position counts."""
        store = self._store_holding(tmp_path, [dict(
            _STORED_POSITION, protective_order_id=None, protective_broker_ref=None,
            protective_client_order_id=None, protective_applied_lots=0.0,
            protective_applied_fee=0.0, protective_applied_value=0.0)])

        payload = store.load()

        assert len(payload.open_positions) == 1
        assert store.get_last_load().level_refusal is None

    @pytest.mark.parametrize('open_positions', [5, 'abc', {'a': 1}, [1], ['x'], [None], [[1]]])
    def test_a_malformed_book_reads_as_unreadable_and_does_not_crash(self, tmp_path, open_positions):
        """The refusal asks for a hand edit, which makes such a slip likelier."""
        store = self._store_holding(tmp_path, open_positions)

        store.load()

        assert store.get_last_load().outcome is CarryOverLoadOutcome.UNREADABLE


class TestARefusedModifyLeavesATrace:
    """The result is the only other trace, and a logic that does not read it believes in a stop."""

    def test_the_api_reports_each_refused_modify(self):
        api, executor = _api()

        api.modify_position('pos_1', stop_loss=95.0)

        assert executor.undeclared_warned == [[OrderFeature.STOP_LOSS]]

    def test_removing_a_level_is_not_reported(self):
        api, executor = _api()

        api.modify_position('pos_1', stop_loss=None)

        assert executor.undeclared_warned == []

    @pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
    def test_it_is_logged_once_per_feature(self, spot_mode):
        """A trailing logic is refused on every tick."""
        logger = RecordingLogger()
        executor = LiveTradeExecutor(
            broker_config=BrokerConfig(
                BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
            initial_balance=100000.0, account_currency='USD', logger=logger, spot_mode=spot_mode,
            initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None)
        api = DecisionTradingApi(executor, required_order_types=[], required_order_features=[],
                                 features_beyond_venue=[], logic_name='my_algo')

        for _ in range(3):
            api.modify_position('pos_1', stop_loss=95.0)
        api.modify_position('pos_1', stop_loss=95.0, take_profit=105.0)

        warned = [text for level, text in logger.lines
                  if level == 'warning' and 'get_required_order_features' in text]
        assert len(warned) == 2
        assert 'stop_loss' in warned[0] and 'take_profit' not in warned[0]
        assert 'take_profit' in warned[1] and 'stop_loss' not in warned[1]


class TestARefusedModifyReadsAsOne:
    """Success and status never disagree, whichever of the two a refusal's builder stated."""

    def test_a_refusal_that_states_no_status_reads_rejected(self):
        result = ModificationResult(
            success=False, rejection_reason=ModificationRejectionReason.INVALID_SL_LEVEL)

        assert result.status is ModificationStatus.REJECTED

    def test_an_accepted_one_keeps_its_status(self):
        assert ModificationResult(success=True).status is ModificationStatus.SUCCESS
        assert ModificationResult(
            success=True, status=ModificationStatus.PENDING).status is ModificationStatus.PENDING


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
