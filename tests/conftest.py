"""
FiniexTestingIDE - Root Test Configuration

Auto-marks all tests by their filesystem path so that pytest marks
stay centralized — no marks needed in individual test files.

Mark taxonomy (see docs/tests/test_taxonomy.md):
- simulation    tests/simulation/
- autotrader    tests/autotrader/
- parity        tests/parity/
- framework     tests/framework/
- data          tests/data/
- live_adapter  tests/live_adapters/
- benchmark     tests/simulation/benchmark/
- live_field_study tests/live_field_study/
- integration   tests with 'integration' in their path
- unit          order_guard, live_executor, safety, bar_rendering, workers, etc.

Config isolation: FINIEX_CONFIG_ISOLATION=1 is set at module import (before any
configuration loaders run) so user_configs/*.json overrides are skipped during
pytest. Tests must be deterministic across developers — the personal workspace
must not bleed in. setdefault() allows manual override (e.g. for debugging a
specific failing test against a user config).
"""

import os

os.environ.setdefault('FINIEX_CONFIG_ISOLATION', '1')

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.store.abstract_store_index import store_index_filename
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.store_types import StoreId


@pytest.fixture(scope='session', autouse=True)
def _isolate_run_tree(tmp_path_factory):
    """
    Redirect the run tree AND its index to a throwaway dir for the whole test session.

    The sibling of `_isolate_run_results_ledger` below, for the other two output planes.
    Without it every test that builds a ScenarioSet or runs an AutoTrader session registers
    a run in the OPERATOR's `runs/` tree and in the index the API serves — measured
    2026-08-30: 134 of 138 index rows came from two suite runs. Tests must never write
    production data (§34).

    Redirecting rather than switching file logging off, deliberately: the tree still exists,
    so a test may read its own run directory (two integration tests assert on artifacts, and
    §36 diagnosis stays possible), and a failing test's log is still there to look at — just
    under tmp.
    """
    root = tmp_path_factory.mktemp('run_tree')
    real = AppConfigManager().get_file_logging_config_object()
    isolated = real.model_copy(update={
        'run_logs': RunLogPaths(simulation=root / 'simulation', live=root / 'live'),
        # Same naming rule as production (#486): <store_id>_index.parquet
        'run_index': root / store_index_filename(StoreId.RUNS),
    })
    mp = pytest.MonkeyPatch()
    mp.setattr(AppConfigManager, 'get_file_logging_config_object', lambda self: isolated)
    yield
    mp.undo()


@pytest.fixture(scope='session', autouse=True)
def _isolate_run_results_ledger(tmp_path_factory):
    """
    Redirect the run-results ledger to a throwaway dir for the whole test session.

    End-to-end runs (the AutoTrader integration sessions, any full report coordinator) append
    to the ledger via AppConfigManager().get_run_ledger_path(); without this the real
    runs/ledger/ would collect test fragments. Tests must never write production data
    (§34) — the optimization unit tests isolate via their own tmp_ledger; this covers the
    coordinator-driven writes globally.
    """
    ledger_dir = tmp_path_factory.mktemp('run_results_ledger')
    mp = pytest.MonkeyPatch()
    mp.setattr(AppConfigManager, 'get_run_ledger_path', lambda self: str(ledger_dir))
    yield
    mp.undo()


def pytest_collection_modifyitems(items):
    """Auto-apply pipeline domain marks based on test file path."""
    for item in items:
        path = str(item.fspath)

        # Pipeline domain
        if '/tests/parity/' in path:
            item.add_marker(pytest.mark.parity)
        if '/tests/simulation/' in path:
            item.add_marker(pytest.mark.simulation)
        if '/tests/autotrader/' in path:
            item.add_marker(pytest.mark.autotrader)
        if '/tests/framework/' in path:
            item.add_marker(pytest.mark.framework)
        if '/tests/data/' in path:
            item.add_marker(pytest.mark.data)

        # Live broker adapter tests (excluded from normal runner — require real account)
        if '/tests/live_adapters/' in path:
            item.add_marker(pytest.mark.live_adapter)

        # Benchmark (subset of simulation — excluded from normal runner)
        if '/tests/simulation/benchmark/' in path:
            item.add_marker(pytest.mark.benchmark)

        # Live Field Study (excluded from normal runner — operator-driven live release gate)
        if '/tests/live_field_study/' in path:
            item.add_marker(pytest.mark.live_field_study)

        # Live Signal Feed (excluded from normal runner — operator-driven live release gate)
        if '/tests/live_signal_feed/' in path:
            item.add_marker(pytest.mark.live_signal_feed)

        # Integration: full-pipeline end-to-end runs
        if '/integration/' in path:
            item.add_marker(pytest.mark.integration)

        # Unit: isolated component tests (no full pipeline)
        _UNIT_PATHS = (
            '/tests/autotrader/order_guard/',
            '/tests/autotrader/live_executor/',
            '/tests/autotrader/loop_cadence/',
            '/tests/autotrader/safety/',
            '/tests/autotrader/state_persistence/',
            '/tests/framework/algo_clock_validator/',
            '/tests/framework/bar_rendering/',
            '/tests/framework/live_telemetry/',
            '/tests/framework/batch_validations/',
            '/tests/framework/worker_tests/',
            '/tests/framework/market_compatibility/',
            '/tests/framework/signal_coverage/',
            '/tests/framework/discovery_validity/',
            '/tests/framework/static_analysis/',
            '/tests/framework/store/',
            '/tests/framework/tick_parquet_reader/',
            '/tests/framework/user_namespace/',
            '/tests/simulation/optimization/',
            '/tests/simulation/robustness/',
        )
        if any(p in path for p in _UNIT_PATHS):
            item.add_marker(pytest.mark.unit)
