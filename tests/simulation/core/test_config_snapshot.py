"""
Config resolvability coverage.

Every run must be able to name the configuration it was commissioned with — it is what makes a
result reproducible, and #475 builds on it. One consumer used not to get it: the CLI path copied
the config into the run directory itself, and the benchmark harness builds its `ScenarioSet`
directly and never did. So the runs carrying the RELEASE BENCHMARK CERTIFICATES were exactly the
ones whose configuration could not be named.

**The copy is gone (#546) and the property is not.** It moved one level up: the run-config store
freezes the content under a `config_id` (#538), the run header carries that id, and a reader
resolves the content through it. That is strictly better — the store is not governed by the file
logging switch the copy was, and it writes once per distinct CONTENT rather than once per run.

This suite pins the surviving property: a run that executes can be resolved to its configuration,
no matter who started it.
"""

import json
from pathlib import Path
from typing import Tuple

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.reporting.builders.broker_report_builder import build_broker_report_from_batch
from python.framework.reporting.store.report_store import ReportStore
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import RunReporting
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.utils.config_fingerprint_utils import to_plain
from python.scenario.scenario_set import ScenarioSet
from python.scenario.scenario_config_loader import ScenarioConfigLoader

SCENARIO_SET = 'backtesting/multi_position_test.json'


@pytest.fixture(scope='module')
def harness() -> Tuple[ScenarioSet, BatchExecutionSummary]:
    """
    A run started the way the benchmark harness starts one: ScenarioSet + orchestrator, directly.

    Deliberately NOT through `initialize_batch_and_run` — that is the CLI path, and it is the
    path that used to be the only one recording the configuration. Reproducing the harness is
    the whole point.
    """
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(SCENARIO_SET), app_config,
                               reporting=RunReporting.NONE)
    summary = BatchOrchestrator(scenario_set, app_config).run()
    return scenario_set, summary


@pytest.fixture(scope='module')
def harness_run(harness) -> ScenarioSet:
    """The harness run's scenario set. Returns: it."""
    return harness[0]


class TestEveryExecutingRunCanNameItsConfig:
    def test_the_harness_path_registers_its_config(self, harness_run):
        snapshot = ReportStore().get_config_snapshot(harness_run.run_id)
        assert snapshot is not None, (
            'a run started without the CLI wrapper cannot be resolved to its configuration — '
            'this is the defect that left every release-benchmark run unidentifiable')
        assert snapshot.config_id, 'the run carries no content id to resolve through'

    def test_what_resolves_is_the_config_that_was_run(self, harness_run):
        """A record that resolves but is empty would pass the check above and help nobody."""
        snapshot = ReportStore().get_config_snapshot(harness_run.run_id)
        assert snapshot.config.get('scenarios'), 'the resolved configuration carries no scenarios'

    def test_the_header_names_the_SOURCE_file(self, harness_run):
        # No longer a file inside the run directory. It is the file the run came FROM, which is
        # what the field's own docstring always claimed and what the copy made ambiguous.
        snapshot = ReportStore().get_config_snapshot(harness_run.run_id)
        assert snapshot.config_snapshot == Path(SCENARIO_SET).name
        assert not (Path(harness_run.logger.get_log_dir()) / 'scenario_config.json').exists(), (
            'the retired copy is back')


class TestEveryExecutingRunFreezesItsBrokerConfiguration:
    """
    A backtest keeps the broker configuration it read, the way a session does (#547). A dynamic
    broker's specifications come from a cache any later fetch overwrites, so a run that only
    hashed them could prove later that they changed and never say what they were.
    """

    def test_the_broker_section_names_a_frozen_copy(self, harness):
        scenario_set, summary = harness
        units = build_broker_report_from_batch(scenario_set.run_id, summary).units
        assert units and all(unit.broker_config_id for unit in units)

    def test_the_frozen_copy_is_what_the_run_read(self, harness):
        _, summary = harness
        store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))
        for info in summary.broker_scenario_map.values():
            frozen_id = info.broker_config.get_frozen_config_id()
            frozen = json.loads(store.frozen_path_of(frozen_id).read_text())
            assert frozen == to_plain(info.broker_config.adapter.broker_config)
