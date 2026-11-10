"""
Recorded Inputs — a backtest of a session's window is built from what the session recorded (#512).

Three seams carry the session's record into the backtest: the scenario set the comparison builds
from the rendered profile, the loader that reads the recorded warmup a scenario names, and the
batch's broker preparation, which trades with the broker configuration the session froze. Every
block of a rendered profile is declared once — where it goes, or why it has no counterpart.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.exceptions.rendered_config_errors import RecordedScenarioRefusedError
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.factory.recorded_scenario_builder import (
    RENDERED_BLOCK_TARGETS,
    RENDERED_BLOCKS_WITHOUT_COUNTERPART,
    RENDERED_EXECUTION_TARGETS,
    RENDERED_EXECUTION_WITHOUT_COUNTERPART,
    RecordedScenarioBuilder,
)
from python.framework.factory.strategy_config_renderer import LOGIC_TYPE_KEY, WORKER_TYPE_KEY
from python.framework.store.broker_config_freeze import (
    freeze_broker_config,
    read_frozen_broker_config,
)
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.config_types.backtesting_config_types import (
    DefaultScenarioExecutionConfig,
    TradeSimulatorDefaults,
)
from python.framework.types.market_types.warmup_history_types import (
    WarmupHistoryRecord,
    WarmupSource,
)
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.recorded_scenario_types import RecordedScenarioInputs
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.scenario.scenario_config_loader import ScenarioConfigLoader

UTC = timezone.utc
START = datetime(2026, 11, 24, 0, 5, 1, tzinfo=UTC)
END = datetime(2026, 12, 24, 0, 0, tzinfo=UTC)


def _rendered_profile(bar_max_history: int) -> dict:
    """A rendered AutoTrader profile as a session freezes it — only what the builder reads."""
    return {
        'kind': 'autotrader_rendered',
        'blocks': {
            'symbol': 'ETHUSD',
            'broker_type': 'kraken_spot',
            'scenario_settings': None,
            'execution': {'parallel_workers': False, 'bar_max_history': bar_max_history,
                          'heartbeat_interval_ms': 500},
            'order_guard': {'cooldown_seconds': 45.0, 'max_consecutive_rejections': 3,
                            'block_stale_market_data': True},
        },
        'strategy_config': {
            'decision_logic_type': 'CORE/example_logic',
            'decision_logic_config': {'threshold': 0.4, LOGIC_TYPE_KEY: 'CORE/example_logic'},
            'worker_instances': {'trend': 'CORE/example_worker'},
            'workers': {'trend': {'period': 20, WORKER_TYPE_KEY: 'CORE/example_worker'}},
        },
        'market_entry': {},
    }


def _inputs(rendered: dict, run_purpose: RunPurpose = RunPurpose.FIXTURE) -> RecordedScenarioInputs:
    return RecordedScenarioInputs(
        scenario_set_name='divergence_example_segment_1', scenario_name='segment_1',
        rendered_profile=rendered, warmup_from_run='20261124_000501_ab12cd34',
        broker_config_document='3f1d' * 16, start=START, end=END,
        balances={'USD': 1000.0, 'ETH': 0.0}, run_purpose=run_purpose)


class TestEveryBlockIsDeclared:
    """A block nobody declared would silently go missing from the comparison."""

    def test_every_rendered_block_has_one_answer(self):
        targets, without = set(RENDERED_BLOCK_TARGETS), set(RENDERED_BLOCKS_WITHOUT_COUNTERPART)
        assert not targets & without, f'declared twice: {sorted(targets & without)}'
        assert targets | without == RecordedScenarioBuilder.get_rendered_blocks()

    def test_every_execution_field_has_one_answer(self):
        targets = set(RENDERED_EXECUTION_TARGETS)
        without = set(RENDERED_EXECUTION_WITHOUT_COUNTERPART)
        assert not targets & without, f'declared twice: {sorted(targets & without)}'
        assert targets | without == RecordedScenarioBuilder.get_execution_fields()

    def test_every_declaration_says_why(self):
        for name, reason in {**RENDERED_BLOCK_TARGETS, **RENDERED_BLOCKS_WITHOUT_COUNTERPART,
                             **RENDERED_EXECUTION_TARGETS,
                             **RENDERED_EXECUTION_WITHOUT_COUNTERPART}.items():
            assert reason.strip(), f'{name}: no reason given'


class TestTheScenarioSet:
    """The set the comparison writes carries the session's record and the release's simulator."""

    @pytest.fixture
    def document(self) -> dict:
        depth = AppConfigManager().get_bar_max_history()
        return RecordedScenarioBuilder.build(_inputs(_rendered_profile(depth)))

    def test_the_scenario_names_the_session_s_record(self, document):
        scenario = document['scenarios'][0]
        assert scenario['symbol'] == 'ETHUSD'
        assert scenario['data_broker_type'] == 'kraken_spot'
        assert scenario['warmup_from_run'] == '20261124_000501_ab12cd34'
        assert scenario['broker_config_document'] == '3f1d' * 16
        assert (scenario['start_date'], scenario['end_date']) == (START.isoformat(), END.isoformat())

    def test_the_strategy_is_the_rendered_one_without_projected_type_keys(self, document):
        strategy = document['global']['strategy_config']
        assert strategy['decision_logic_config'] == {'threshold': 0.4}
        assert strategy['workers'] == {'trend': {'period': 20}}
        assert strategy['worker_instances'] == {'trend': 'CORE/example_worker'}

    def test_the_session_s_cadence_and_guard_are_carried(self, document):
        execution = document['global']['execution_config']
        assert execution['parallel_workers'] is False
        assert execution['heartbeat_interval_ms'] == 500
        assert execution['state_frames'] == {'enabled': True, 'cadence_timeframe': 'M1'}
        assert document['global']['order_guard']['cooldown_seconds'] == 45.0

    def test_every_execution_field_is_written(self, document):
        """A field left to the cascade could come from a workspace override of the defaults."""
        execution = document['global']['execution_config']
        assert set(execution) == set(DefaultScenarioExecutionConfig.model_fields)
        defaults = DefaultScenarioExecutionConfig().model_dump(mode='json')
        assert execution['tick_processing_budget_ms'] == defaults['tick_processing_budget_ms']

    def test_the_frames_follow_the_session_s_cadence(self):
        """The backtest records its frames on the slots the session recorded on."""
        rendered = _rendered_profile(AppConfigManager().get_bar_max_history())
        rendered['blocks']['state_frames'] = {'enabled': True, 'cadence_timeframe': 'M5'}
        document = RecordedScenarioBuilder.build(_inputs(rendered))
        assert document['global']['execution_config']['state_frames'] == {
            'enabled': True, 'cadence_timeframe': 'M5'}

    def test_the_simulator_is_the_release_s_model_written_out(self, document):
        simulator = document['global']['trade_simulator_config']
        assert set(simulator) == set(TradeSimulatorDefaults.model_fields)
        assert simulator == TradeSimulatorDefaults(
            balances={'USD': 1000.0, 'ETH': 0.0}).model_dump(mode='json')
        assert simulator['account_currency'] is None   # derived from the quote, as the session's

    def test_a_regular_comparison_declares_no_purpose(self):
        depth = AppConfigManager().get_bar_max_history()
        document = RecordedScenarioBuilder.build(_inputs(_rendered_profile(depth), RunPurpose.REGULAR))
        assert 'run_purpose' not in document

    def test_another_history_depth_is_refused(self):
        depth = AppConfigManager().get_bar_max_history()
        with pytest.raises(RecordedScenarioRefusedError, match='bars per timeframe'):
            RecordedScenarioBuilder.build(_inputs(_rendered_profile(depth + 1)))

    def test_a_document_without_blocks_is_refused(self):
        with pytest.raises(RecordedScenarioRefusedError, match='not a rendered AutoTrader profile'):
            RecordedScenarioBuilder.build(_inputs({'kind': 'scenario_set'}))


class TestTheLoaderReadsTheRecordedWarmup:
    """A scenario naming `warmup_from_run` carries that run's record into the batch."""

    @staticmethod
    def _record(symbol: str) -> WarmupHistoryRecord:
        return WarmupHistoryRecord(
            symbol=symbol, source=WarmupSource.VENUE, read_at=START.isoformat(),
            selected_for=START.isoformat(), first_tick=START.isoformat(),
            reread_at_first_tick=[], timeframes={})

    def _load(self, tmp_path, monkeypatch, recorded_symbol: str):
        monkeypatch.setattr('python.scenario.scenario_config_loader.read_warmup_record',
                            lambda run_id, reader=None: self._record(recorded_symbol))
        monkeypatch.setattr('python.scenario.scenario_config_loader.read_frozen_broker_config',
                            lambda document_id, store=None: {'frozen': document_id})
        depth = AppConfigManager().get_bar_max_history()
        path = RecordedScenarioBuilder.write(
            RecordedScenarioBuilder.build(_inputs(_rendered_profile(depth))), tmp_path)
        return ScenarioConfigLoader().load_config(str(path))

    def test_the_record_rides_on_the_scenario(self, tmp_path, monkeypatch):
        scenario = self._load(tmp_path, monkeypatch, 'ETHUSD').scenarios[0]
        assert scenario.warmup_from_run == '20261124_000501_ab12cd34'
        assert scenario.warmup_record == self._record('ETHUSD')
        assert scenario.broker_config_document == '3f1d' * 16
        assert scenario.broker_config_document_content == {'frozen': '3f1d' * 16}

    def test_a_record_of_another_symbol_is_refused(self, tmp_path, monkeypatch):
        with pytest.raises(ValueError, match='recorded BTCUSD'):
            self._load(tmp_path, monkeypatch, 'BTCUSD')

    def test_a_copy_of_the_records_is_read_through_what_the_loader_was_given(self, tmp_path,
                                                                                 monkeypatch):
        seen = {}

        def warmup(run_id, reader=None):
            seen['reader'] = reader
            return self._record('ETHUSD')

        def broker(document_id, store=None):
            seen['store'] = store
            return {'frozen': document_id}
        monkeypatch.setattr('python.scenario.scenario_config_loader.read_warmup_record', warmup)
        monkeypatch.setattr('python.scenario.scenario_config_loader.read_frozen_broker_config',
                            broker)
        depth = AppConfigManager().get_bar_max_history()
        path = RecordedScenarioBuilder.write(
            RecordedScenarioBuilder.build(_inputs(_rendered_profile(depth))), tmp_path)
        reader, store = RunEntryReader(tmp_path / 'copy_index.parquet'), RunConfigStore(tmp_path)
        ScenarioConfigLoader(reader, store).load_config(str(path))
        assert seen == {'reader': reader, 'store': store}


class TestTheFrozenBrokerDocument:
    """The batch trades with the configuration the session froze, never the file of today.

    The loader reads the document before the batch begins; the preparation builds from its content
    and reads no store, which keeps the store out of the backtest's path.
    """

    @staticmethod
    def _frozen_id() -> str:
        seed = BrokerConfigFactory.build_broker_config(
            MarketConfigManager().get_broker_config_path('kraken_spot'))
        freeze_broker_config(seed, MagicMock())
        return seed.get_frozen_config_id()

    @staticmethod
    def _scenario(name: str, document: str = '', content: dict = None) -> SingleScenario:
        return SingleScenario(
            name=name, scenario_index=0, symbol='ETHUSD', data_broker_type='kraken_spot',
            start_date=START, broker_config_document=document,
            broker_config_document_content=content)

    def test_a_scenario_naming_the_document_trades_with_it(self):
        document = self._frozen_id()
        scenario = self._scenario('segment_1', document, read_frozen_broker_config(document))
        preparator = BrokerDataPreparator([scenario], MagicMock())

        configs = preparator.prepare()

        assert scenario.is_valid()
        (info,) = preparator.get_broker_scenario_map().values()
        assert document in info.config_path
        assert 'ETHUSD' in next(iter(configs.values()))['symbols']

    def test_an_unknown_document_is_refused_at_load(self):
        with pytest.raises(FileNotFoundError, match='not in the run-config store'):
            read_frozen_broker_config('f' * 64)

    def test_a_document_is_read_from_the_store_it_is_given(self, tmp_path):
        document = self._frozen_id()
        configured = Path(AppConfigManager().get_run_configs_path())
        copy = tmp_path / 'copy_run_configs'
        shutil.copytree(configured, copy)
        assert read_frozen_broker_config(document, RunConfigStore(copy)) == \
            read_frozen_broker_config(document)
        with pytest.raises(FileNotFoundError, match='not in the run-config store'):
            read_frozen_broker_config(document, RunConfigStore(tmp_path / 'empty_store'))

    def test_a_document_that_cannot_be_built_is_the_scenario_s_error(self):
        scenario = self._scenario('segment_1', 'f' * 64, {'symbols': {}})

        BrokerDataPreparator([scenario], MagicMock()).prepare()

        assert not scenario.is_valid()
        checks = [finding.check for result in scenario.validation_result
                  for finding in result.findings]
        assert checks == ['broker_config_document_unavailable']

    def test_one_broker_from_two_sources_is_refused(self, monkeypatch):
        seed = BrokerConfigFactory.build_broker_config(
            MarketConfigManager().get_broker_config_path('kraken_spot'))
        cache = json.loads(json.dumps(seed.adapter.broker_config))
        cache['broker_type'] = 'kraken_spot'
        monkeypatch.setattr(
            'python.framework.data_preparation.broker_data_preparator.load_runtime_cache',
            lambda broker_type: cache)
        document = self._frozen_id()
        documented = self._scenario('segment_1', document, read_frozen_broker_config(document))
        other = self._scenario('today')

        BrokerDataPreparator([documented, other], MagicMock()).prepare()

        assert documented.is_valid()
        checks = [finding.check for result in other.validation_result for finding in result.findings]
        assert checks == ['broker_config_sources_differ']
