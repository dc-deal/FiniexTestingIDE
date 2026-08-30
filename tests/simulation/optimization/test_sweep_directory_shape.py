"""
Sweep directory shape.

A sweep loads its data ONCE and reuses it across every combination. That load used to go through
a `ScenarioSet` built only to produce the mount — and constructing one opens a run directory as a
side effect. So every sweep left behind a directory shaped like a run that no run ever wrote to:
logs, no artifacts, and a row in the API's run index carrying `has_reports: false`, which a
consumer cannot tell apart from a legitimate log-only session.

The fix is not to stop logging. The record is the only place the sweep's data window, tick count,
warmup bars and broker configuration are written down, and no combination's own log repeats it.
The fix is to put sweep-level output at sweep level.

Both halves are pinned here, and the second matters as much as the first: the directory is gone
AND the record survived.
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.optimization.optimization_runner import OptimizationRunner
from python.framework.types.log_layout_types import MOUNT_BUILD_LOG

MINI_GRID = 'tests/fixtures/optimization/btcusd_mini_grid.json'


def _sweep_dir(sweep_id: str) -> Path:
    root = AppConfigManager().get_file_logging_config_object().run_logs.sweeps
    return Path(root) / sweep_id


@pytest.fixture(scope='module')
def sweep_id() -> str:
    """One real sweep for the whole module — both assertions read the same tree."""
    return OptimizationRunner().run(MINI_GRID)


class TestASweepLeavesOneDirectoryPerCombination:
    """The count is the contract: a sweep directory holds runs, and a data load is not one."""

    def test_every_child_directory_is_a_combination(self, sweep_id):
        sweep_dir = _sweep_dir(sweep_id)

        children = sorted(d.name for d in sweep_dir.iterdir() if d.is_dir())
        assert children, 'the sweep produced no combination directories at all'
        # Every directory is a combination — named for the base set plus the sweep's own label.
        for name in children:
            assert name.startswith(f'btcusd_mini_set__{sweep_id}_c'), (
                f"'{name}' is not a combination directory — a sweep directory must not collect "
                f'anything else, or the run index counts it as a run')

    def test_the_mount_record_survives_at_sweep_level(self, sweep_id):
        record = _sweep_dir(sweep_id) / MOUNT_BUILD_LOG

        assert record.exists(), (
            f'{MOUNT_BUILD_LOG} is missing — the shared data load is the only record of what '
            f'the whole sweep ran against, and moving it must not mean losing it')
        body = record.read_text(encoding='utf-8')
        assert 'ticks in RAM' in body, 'the record exists but carries no data-load content'
        # Written flat, so it is not itself mistaken for a run.
        assert record.parent == _sweep_dir(sweep_id)
