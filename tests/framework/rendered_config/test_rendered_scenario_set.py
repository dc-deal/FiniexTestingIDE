"""
A backtest records what it RAN with, not only what it was given (#547).

A scenario set is the input to a cascade: the app layer under its global block, the global block
under each scenario, a schema default for every parameter no file sets. The rendered document is
the result, frozen where the run's header is written and named by it. These tests hold it to what
the run reads: every layer is in it, each scenario's section is what that scenario's subprocess
is configured with — over every shipped scenario set — a sweep combination renders its own, the
run never fails for it, and a session and a backtest given the same strategy record the same
strategy section.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.run_record.rendered_profile_builder import (
    assert_rendered_parameters_match,
    render_autotrader_profile,
)
from python.framework.config_directory.config_directory_builder import config_kind_of
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import RunHeader, RunReporting
from python.framework.types.config_directory_types import ConfigKind
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.scenario_types.scenario_set_types import (
    LoadedScenarioConfig,
    SingleScenario,
)
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint, to_plain
from python.scenario.rendered_scenario_set_builder import render_scenario, render_scenario_set
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet

_ROOT = Path(__file__).resolve().parents[3]
_FOLDERS = [_ROOT / 'configs', _ROOT / 'tests' / 'fixtures']
_CENSUS_SET = 'tests/fixtures/run_entry_census/census_set.json'
_PROFILE = 'configs/autotrader_profiles/mock/minimal_warmup_test.json'


def _shipped_sets() -> List[Path]:
    """
    Every shipped scenario set the loader accepts — a set built to be refused has its own test.

    Returns:
        Their paths
    """
    accepted = []
    for folder in _FOLDERS:
        for path in sorted(folder.rglob('*.json')):
            if config_kind_of(path) is not ConfigKind.SCENARIO_SET:
                continue
            try:
                ScenarioConfigLoader().load_config(str(path))
            except Exception:
                continue
            accepted.append(path)
    return accepted


def _load(path: str) -> LoadedScenarioConfig:
    """
    Load one scenario set.

    Args:
        path: The set's path

    Returns:
        The loaded set, cascaded
    """
    return ScenarioConfigLoader().load_config(path)


def _render(loaded: LoadedScenarioConfig) -> Dict[str, Any]:
    """
    Render a loaded set the way the run does.

    Args:
        loaded: The loaded set

    Returns:
        The rendered document
    """
    return render_scenario_set(loaded.scenarios, loaded.run_purpose,
                               loaded.robustness or RobustnessConfig(), AppConfigManager())


def _process_config(scenario: SingleScenario, app_config: AppConfigManager) -> ProcessScenarioConfig:
    """
    The configuration the scenario's subprocess is given, as the batch builds it.

    Args:
        scenario: A loaded scenario
        app_config: Application configuration

    Returns:
        The subprocess configuration
    """
    # Phase 0 sets the broker type from the broker configuration; the two names agree
    scenario.broker_type = BrokerType(scenario.data_broker_type)
    return ProcessScenarioConfig.from_scenario(
        scenario, app_config, 0, 'rendered_config_test', datetime.now(timezone.utc),
        'rendered_config_test', None)


def _header(scenario_set: ScenarioSet) -> RunHeader:
    """
    The header the scenario set wrote.

    Args:
        scenario_set: A scenario set built for a run

    Returns:
        Its run header
    """
    return RunDirReader(Path(scenario_set.logger.get_log_dir())).read_header()


def _frozen(config_id: str) -> Dict[str, Any]:
    """
    A frozen document, read back from the run-config store.

    Args:
        config_id: Its id

    Returns:
        The document
    """
    store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))
    return json.loads(store.frozen_path_of(config_id).read_text())


class TestTheDocumentCarriesEveryLayer:

    def test_a_default_no_file_sets_is_in_the_document(self):
        assert 'heartbeat_interval_ms' not in Path(_CENSUS_SET).read_text()

        section = next(iter(_render(_load(_CENSUS_SET))['scenarios'].values()))

        assert section['execution_config']['heartbeat_interval_ms'] == (
            AppConfigManager().get_scenario_execution_defaults()['heartbeat_interval_ms'])
        assert 'cooldown_seconds' in section['order_guard_config']

    def test_the_market_entry_and_rules_are_recorded(self):
        scenario = _load(_CENSUS_SET).scenarios[0]
        market_config = MarketConfigManager()

        section = render_scenario(
            scenario, AppConfigManager().get_scenario_execution_defaults(), market_config)

        assert section['market_entry'] == to_plain(
            market_config.get_broker_entry(scenario.data_broker_type))
        assert section['market_rules'] == to_plain(
            market_config.get_market_rules_for_broker(scenario.data_broker_type))

    def test_the_run_level_settings_are_recorded(self):
        app_config = AppConfigManager()
        document = _render(_load(_CENSUS_SET))

        assert document['kind'] == RunConfigKind.SCENARIO_SET_RENDERED.value
        assert document['backtesting']['history']['bar_max_history'] == (
            app_config.get_bar_max_history())
        assert document['backtesting']['data_validation'] == to_plain(
            app_config.get_data_validation_config())

    def test_the_same_set_renders_the_same_document(self):
        first = _render(_load(_CENSUS_SET))
        second = _render(_load(_CENSUS_SET))

        assert generate_config_fingerprint(first) == generate_config_fingerprint(second)
        json.dumps(first)


class TestTheRecordIsWhatTheRunReads:
    """
    The backtest's counterpart of the session's start-time check, held as a test: both sides use
    one helper per block, so a difference can only be a defect, and it surfaces when the code
    changes — which is when this runs.
    """

    def test_the_shipped_sets_are_found(self):
        assert _shipped_sets()

    @pytest.mark.parametrize('path', _shipped_sets(), ids=lambda path: path.stem)
    def test_each_section_is_what_its_subprocess_reads(self, path):
        app_config = AppConfigManager()
        loaded = _load(str(path))
        document = _render(loaded)
        built_strategies = set()

        for scenario in loaded.scenarios:
            section = document['scenarios'][scenario.name]
            process = _process_config(scenario, app_config)
            execution = section['execution_config']
            simulator = section['trade_simulator_config']

            assert (process.parallel_workers, process.parallel_threshold,
                    process.strict_parameter_validation, process.tick_processing_budget_ms,
                    process.heartbeat_interval_ms) == (
                execution['parallel_workers'], execution['worker_parallel_threshold_ms'],
                execution['strict_parameter_validation'], execution['tick_processing_budget_ms'],
                execution['heartbeat_interval_ms']), scenario.name
            assert (process.worker_decision_tracking, process.tick_loop_profiling) == (
                execution['performance_tracking']['worker_decision_tracking'],
                execution['performance_tracking']['tick_loop_profiling']), scenario.name
            assert (process.inbound_latency_min_ms, process.inbound_latency_max_ms,
                    process.balances) == (
                simulator['inbound_latency_min_ms'], simulator['inbound_latency_max_ms'],
                simulator['balances']), scenario.name
            assert to_plain(process.order_guard_config) == section['order_guard_config']
            assert to_plain(process.stress_test_config) == section['stress_test_config']
            assert process.trading_model.value == section['market_entry']['trading_model']
            assert process.market_type.value == section['market_entry']['market_type']
            assert process.inter_tick_gap_threshold_s == (
                section['market_rules']['inter_tick_gap_threshold_s'])
            assert process.bar_max_history == document['backtesting']['history']['bar_max_history']

            strategy_key = generate_config_fingerprint(process.strategy_config)
            if strategy_key in built_strategies:
                continue
            built_strategies.add(strategy_key)
            try:
                logic, workers = _built_components(process)
            except ValueError:
                # A strategy its factories refuse is refused by the run as well — a fixture
                # built for loading only; its blocks were still compared above
                continue
            assert_rendered_parameters_match(
                {'strategy_config': section['strategy_config']}, logic, workers)


def _built_components(process: ProcessScenarioConfig):
    """
    Build the decision logic and the workers the way the subprocess does.

    Args:
        process: The subprocess configuration

    Returns:
        (decision logic, workers by instance name)
    """
    logger = get_global_logger()
    workers = WorkerFactory(logger).create_workers_from_config(
        strategy_config=process.strategy_config)
    logic = DecisionLogicFactory(logger).create_logic(
        logic_type=process.decision_logic_type, logger=logger,
        logic_config=process.decision_logic_config)
    return logic, workers


class TestTheHeaderNamesTheDocument:

    def test_a_run_freezes_its_document_and_names_it(self):
        scenario_set = ScenarioSet(_load(_CENSUS_SET), AppConfigManager(),
                                   reporting=RunReporting.NONE)

        header = _header(scenario_set)

        assert header.rendered_config_id
        assert header.rendered_config_id != header.config_id
        assert _frozen(header.rendered_config_id)['kind'] == (
            RunConfigKind.SCENARIO_SET_RENDERED.value)

    def test_a_sweep_combination_renders_its_own_document(self):
        base = _load(_CENSUS_SET)
        combination = copy.deepcopy(base)
        for scenario in combination.scenarios:
            scenario.strategy_config['decision_logic_config']['open_tick'] = 25

        base_id = _header(ScenarioSet(base, AppConfigManager(),
                                      reporting=RunReporting.NONE)).rendered_config_id
        combination_id = _header(ScenarioSet(combination, AppConfigManager(),
                                             reporting=RunReporting.NONE)).rendered_config_id

        assert base_id != combination_id
        for section in _frozen(combination_id)['scenarios'].values():
            assert section['strategy_config']['decision_logic_config']['open_tick'] == 25

    def test_a_mount_build_renders_nothing(self, monkeypatch):
        calls = []
        monkeypatch.setattr('python.scenario.scenario_set.render_scenario_set',
                            lambda *args: calls.append(args))

        ScenarioSet(_load(_CENSUS_SET), AppConfigManager(), mount_only=True,
                    reporting=RunReporting.NONE)

        assert not calls


class TestTheRunNeverFailsForIt:

    def test_a_document_that_cannot_be_rendered_leaves_the_id_empty(self, monkeypatch):
        def refuse(*args):
            raise RuntimeError('cannot resolve the strategy')
        monkeypatch.setattr('python.scenario.scenario_set.render_scenario_set', refuse)

        header = _header(ScenarioSet(_load(_CENSUS_SET), AppConfigManager(),
                                     reporting=RunReporting.NONE))

        assert header.rendered_config_id == ''
        assert header.config_id

    def test_a_store_that_cannot_be_written_leaves_the_id_empty(self, monkeypatch):
        def refuse(*args):
            raise OSError('read-only volume')
        monkeypatch.setattr(RunConfigStore, 'register_document', refuse)

        header = _header(ScenarioSet(_load(_CENSUS_SET), AppConfigManager(),
                                     reporting=RunReporting.NONE))

        assert header.rendered_config_id == ''


class TestOneStrategyReadsAlikeInBothPipelines:
    """
    The precondition for matching a live session with its backtest: given the same strategy, the
    session's rendered profile and the backtest's rendered scenario set record the same strategy
    section, field by field.
    """

    def test_a_session_and_a_backtest_record_one_strategy_section(self, tmp_path):
        profile = load_autotrader_config(_PROFILE)
        set_file = tmp_path / 'one_strategy_set.json'
        set_file.write_text(json.dumps({
            'scenario_set_name': 'one_strategy_set',
            'run_purpose': 'fixture',
            'global': {
                'strategy_config': profile.strategy_config,
                'trade_simulator_config': {'balances': {'USD': 10000.0}},
            },
            'scenarios': [{
                'scenario_name': 'one_window',
                'symbol': profile.symbol,
                'data_broker_type': profile.broker_type,
                'start_date': '2026-01-05T00:00:00+00:00',
                'end_date': '2026-01-06T00:00:00+00:00',
            }],
        }))

        backtest = next(iter(_render(_load(str(set_file)))['scenarios'].values()))
        session = render_autotrader_profile(profile)

        assert backtest['strategy_config'] == session['strategy_config']
