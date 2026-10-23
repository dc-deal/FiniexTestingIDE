"""
The run entry census (#621): real runs, made once for the suite, whose directories the tests hold
to the run store's declaration.

A small sweep — two combinations, each of two short windows with the robustness mode on, traded
by a probe logic that writes a diagnostics row and opens and closes one position — and a mock
AutoTrader session that records its state frames. Everything lands in the suite's isolated run
tree and is removed afterwards.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.constants.run_layout_constants import RUN_HEADER_ARTIFACT
from python.framework.optimization.optimization_report import render_sweep_report
from python.framework.optimization.optimization_runner import OptimizationRunner
from tests.shared.fixture_helpers import remove_run_dir

CENSUS_GRID = 'tests/fixtures/run_entry_census/census_grid.json'
CENSUS_PROFILE = 'configs/autotrader_profiles/mock/run_entry_census_test.json'


@dataclass(frozen=True)
class CensusSweep:
    """
    One sweep the census made.

    Args:
        sweep_dir: The sweep's own directory
        run_dirs: Its combinations' run directories
    """
    sweep_dir: Path
    run_dirs: List[Path]


@pytest.fixture(scope='module')
def census_sweep() -> CensusSweep:
    """A real sweep with its ranking written, as the report command writes it."""
    sweep_id = OptimizationRunner().run(CENSUS_GRID)
    render_sweep_report(sweep_id)
    sweep_dir = Path(AppConfigManager().get_file_logging_config_object().run_logs.sweeps) / sweep_id
    run_dirs = sorted(header.parent for header in sweep_dir.rglob(RUN_HEADER_ARTIFACT))
    yield CensusSweep(sweep_dir, run_dirs)
    shutil.rmtree(sweep_dir, ignore_errors=True)


@pytest.fixture(scope='module')
def census_session() -> Path:
    """A real mock session; its run directory."""
    trader = AutotraderMain(load_autotrader_config(CENSUS_PROFILE))
    trader.run()
    yield trader._run_dir
    remove_run_dir(trader._run_dir)


@pytest.fixture(scope='module')
def census_session_without_log_files() -> Path:
    """The same session with the log switch off; its run directory."""
    real = AppConfigManager().get_file_logging_config_object()
    switched_off = real.model_copy(
        update={'scenario': real.scenario.model_copy(update={'enabled': False})})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(AppConfigManager, 'get_file_logging_config_object',
                      lambda self: switched_off)
        trader = AutotraderMain(load_autotrader_config(CENSUS_PROFILE))
        trader.run()
    yield trader._run_dir
    remove_run_dir(trader._run_dir)
