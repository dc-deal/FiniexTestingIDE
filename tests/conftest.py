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
from copy import deepcopy

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

    `global_log_dir` joins them here (#476 part b). It is the one write plane nothing bounds
    — no rotation, no retention, not per-run: every process of every run in both pipelines
    appends to the same file forever. Measured 2026-09-21: 2,606,808 lines / 197 MB in the
    operator's working tree, against 161,068 lines / 12 MB measured on 2026-08-31, i.e. a
    sixteenfold growth in three weeks, of which the suite is a large part. Bounding the file
    itself — rotation plus a retention window — is the other half and stays with #476/#357.
    """
    root = tmp_path_factory.mktemp('run_tree')
    real = AppConfigManager().get_file_logging_config_object()
    isolated = real.model_copy(update={
        'run_logs': RunLogPaths(simulation=root / 'simulation', live=root / 'live'),
        # Same naming rule as production (#486): <store_id>_index.parquet
        'run_index': root / store_index_filename(StoreId.RUNS),
        'global_log_dir': root / 'global',
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


@pytest.fixture(scope='session', autouse=True)
def _isolate_run_config_store(tmp_path_factory):
    """
    Redirect the run-config store to a throwaway dir for the whole test session.

    The third sibling of the two fixtures above, and it was missing for exactly one suite run:
    every test that lists scenario sets or builds a ScenarioSet registers its config, so the
    OPERATOR's store filled with test fixtures — measured 2026-09-22, **28 of 50 registered
    versions** came from `tests/fixtures/` and `configs/scenario_sets/backtesting/`, one of them
    with a run count of 16. Tests must never write production data (§34).

    Redirecting rather than switching registration off, for the same reason as the run tree: the
    store still works under test, so its own suite exercises the real path and an integration
    test can read back what it registered — just under tmp.
    """
    store_dir = tmp_path_factory.mktemp('run_configs')
    mp = pytest.MonkeyPatch()
    mp.setattr(AppConfigManager, 'get_run_configs_path', lambda self: str(store_dir))
    yield
    mp.undo()


@pytest.fixture(scope='session', autouse=True)
def _isolate_run_patch_store(tmp_path_factory):
    """
    Redirect the run-patch store to a throwaway dir for the whole test session.

    Added with the store rather than after its first leak. The suite runs from a working tree
    that is dirty whenever somebody is working on it, so every test that captures a code identity
    with a patch sink would file the tree's diff in the OPERATOR's `run_patches/` — patches of
    code that was under test, not code that ran (§34).
    """
    store_dir = tmp_path_factory.mktemp('run_patches')
    mp = pytest.MonkeyPatch()
    mp.setattr(AppConfigManager, 'get_run_patches_path', lambda self: str(store_dir))
    yield
    mp.undo()


@pytest.fixture(scope='session', autouse=True)
def _isolate_carry_over_stores(tmp_path_factory):
    """
    Redirect BOTH carry-over stores to a throwaway dir for the whole test session.

    The fourth sibling, and the one that was missing longest: every AutoTrader integration
    session writes its cold-start document — the open position book, the position-counter
    high-water mark, the session keys its orders were sent under — and until 2026-09-22 it wrote
    them into the OPERATOR's `data/runtime/cold_start_state/`, beside the document of a live bot
    holding a real position. Measured that day: 16 of 19 documents there came from test profiles.
    Tests must never write production data (§34).

    It surfaced only because the key SHAPE changed and the same bots suddenly appeared twice,
    under both spellings. A polluted store is not something any test asserts against, which is
    exactly why this class of defect needs a fixture rather than a check.

    Both paths come out of one config section, so both are redirected in one patch — and
    `state_persistence` is included although the algo store is opt-in: an opt-in that is taken
    once writes production data once.
    """
    root = tmp_path_factory.mktemp('carry_over')
    real = AppConfigManager().get_autotrader_defaults()
    isolated = deepcopy(real)
    isolated.setdefault('cold_start', {})['path'] = str(root / 'cold_start_state')
    isolated.setdefault('state_persistence', {})['path'] = str(root / 'session_state')
    mp = pytest.MonkeyPatch()
    mp.setattr(AppConfigManager, 'get_autotrader_defaults', lambda self: deepcopy(isolated))
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

        # `unit` and `integration` used to be applied here too. They were the only two rules
        # that needed a maintained LIST rather than falling out of the tree, they were the only
        # two that were measurably wrong (8 of 14 autotrader suites were missing from the unit
        # list, so `-m unit` silently under-reported), and nothing ever selected on them — not
        # the runner, which picks by directory, not test_config.json, not launch.json. A mark
        # with no consumer and a list that goes stale on every new suite is dead config (§19).
