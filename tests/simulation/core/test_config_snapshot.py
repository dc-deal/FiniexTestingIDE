"""
Config snapshot coverage.

Every run directory should carry the config it was commissioned with — it is the closest thing the
project has to a run header, and #475 builds on it. One consumer did not get it: the CLI path
called `copy_config_snapshot()` itself, and the benchmark harness builds its `ScenarioSet` directly
and never did. So the runs carrying the RELEASE BENCHMARK CERTIFICATES were exactly the ones
without a snapshot.

The call moved into `BatchOrchestrator.run()`, where every consumer passes. This suite pins that:
a run that executes gets a snapshot, no matter who started it.
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.api.report_types import RunReporting
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.scenario.scenario_config_loader import ScenarioConfigLoader

SCENARIO_SET = 'backtesting/multi_position_test.json'


@pytest.fixture(scope='module')
def harness_run_dir() -> Path:
    """
    A run started the way the benchmark harness starts one: ScenarioSet + orchestrator, directly.

    Deliberately NOT through `initialize_batch_and_run` — that is the CLI path, and it is the path
    that used to be the only one taking the snapshot. Reproducing the harness is the whole point.
    """
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(SCENARIO_SET), app_config,
                                reporting=RunReporting.NONE)
    BatchOrchestrator(scenario_set, app_config).run()
    return scenario_set.logger.get_log_dir()


class TestEveryExecutingRunCarriesItsConfig:
    def test_the_harness_path_gets_a_snapshot(self, harness_run_dir):
        snapshot = harness_run_dir / 'scenario_config.json'
        assert snapshot.exists(), (
            'a run started without the CLI wrapper has no config snapshot — this is the defect '
            'that left every release-benchmark run unidentifiable')

    def test_the_snapshot_is_the_config_that_was_run(self, harness_run_dir):
        """A snapshot that is present but empty would pass the existence check and help nobody."""
        import json
        body = json.loads((harness_run_dir / 'scenario_config.json').read_text(encoding='utf-8'))
        assert body.get('scenarios'), 'the snapshot carries no scenarios'
