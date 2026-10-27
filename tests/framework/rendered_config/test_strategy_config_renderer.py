"""
One strategy renderer for both pipelines names each component the way its configuration does
(#547).

A factory injects the type it RESOLVED, and for a component configured as a relative file path
that is an absolute path: it depends on the working directory and on the code tree a run started
from. A record carrying it would name one strategy differently on every machine and in every
release tree, and a live session could never be matched with its backtest. These tests hold the
renderer to the configured name, the factory to its resolved one, and the session's start check
to the same projection the renderer applies.
"""
import shutil
from pathlib import Path

from python.framework.autotrader.run_record.rendered_profile_builder import (
    assert_rendered_parameters_match,
)
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.strategy_config_renderer import (
    LOGIC_TYPE_KEY,
    WORKER_TYPE_KEY,
    render_strategy_config,
)
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger

_PROBE = 'tests/fixtures/run_entry_census/census_probe_decision.py'
_STRATEGY = {
    'decision_logic_type': _PROBE,
    'worker_instances': {'rsi_fast': 'CORE/rsi'},
    'workers': {'rsi_fast': {'periods': {'M5': 14}}},
    'decision_logic_config': {'open_tick': 20, 'close_tick': 60, 'lot_size': 0.001},
}


class TestAComponentIsNamedAsConfigured:

    def test_a_file_logic_keeps_its_relative_path(self):
        rendered = render_strategy_config(_STRATEGY)

        assert rendered['decision_logic_config'][LOGIC_TYPE_KEY] == _PROBE

    def test_a_core_worker_keeps_its_core_name(self):
        rendered = render_strategy_config(_STRATEGY)

        assert rendered['workers']['rsi_fast'][WORKER_TYPE_KEY] == 'CORE/rsi'

    def test_the_factory_still_injects_the_path_it_resolved(self):
        # Performance tracking reads the injected key; the renderer projects, it never changes
        # what the factory hands a component.
        parameters = DecisionLogicFactory(get_global_logger()).resolve_parameters(_PROBE, {})

        assert Path(parameters[LOGIC_TYPE_KEY]).is_absolute()

    def test_the_same_strategy_renders_alike_in_another_tree(self, monkeypatch, tmp_path):
        here = render_strategy_config(_STRATEGY)
        probe_copy = tmp_path / _PROBE
        probe_copy.parent.mkdir(parents=True)
        shutil.copy(_PROBE, probe_copy)
        monkeypatch.chdir(tmp_path)

        elsewhere = render_strategy_config(_STRATEGY)
        resolved = DecisionLogicFactory(get_global_logger()).resolve_parameters(_PROBE, {})

        assert elsewhere == here
        assert resolved[LOGIC_TYPE_KEY] == str(probe_copy.resolve())


class TestTheStartCheckProjectsTheBuiltSide:

    def test_a_file_logic_built_with_its_resolved_path_matches_the_record(self):
        logger = get_global_logger()
        rendered = {'strategy_config': render_strategy_config(_STRATEGY)}
        workers = WorkerFactory(logger).create_workers_from_config(strategy_config=_STRATEGY)
        logic = DecisionLogicFactory(logger).create_logic(
            logic_type=_PROBE, logger=logger, logic_config=_STRATEGY['decision_logic_config'])

        assert Path(logic.config[LOGIC_TYPE_KEY]).is_absolute()
        assert_rendered_parameters_match(rendered, logic, workers)
