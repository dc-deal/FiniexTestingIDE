"""
Every long-running entry point enters itself while its work runs and leaves when it ends (#603).

A witness wraps the registry: at the moment an entry point registers, it reads the list and keeps
what it found. The work around it is the real one where that is cheap — the two-by-two mini sweep, an
AutoTrader session refused at its start — and a stand-in where the work itself is not under test.
"""

from contextlib import contextmanager

import pytest

from python.cli import api_server_cli as api_server_cli_module
from python.cli import test_runner_cli as test_runner_cli_module
from python.cli.api_server_cli import ApiServerCli
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.data_management.importers.import_coordinator import (
    ImportCoordinator,
    import_paths_from_config,
)
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.optimization.optimization_runner import OptimizationRunner
from python.framework.types.activity_registry_types import ActivityKind
from python.framework.types.import_types.import_progress_types import ImportOutcome
from python.framework.types.run_origin_types import RunChannel

MINI_GRID = 'tests/fixtures/optimization/btcusd_mini_grid.json'
PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'


@pytest.fixture
def witness(registry_dir, monkeypatch):
    """
    Record every registration together with what the list held at that moment.

    Returns:
        A list of (kind, label, the entry as listed or None)
    """
    seen = []
    real_running = ActivityRegistry.running

    @contextmanager
    def witnessed(self, kind, label, **kwargs):
        with real_running(self, kind, label, **kwargs) as activity_id:
            listed = {e.activity_id: e for e in ActivityRegistryReader().read().entries}
            seen.append((kind, label, listed.get(activity_id)))
            yield activity_id

    monkeypatch.setattr(ActivityRegistry, 'running', witnessed)
    return seen


def _assert_left_no_trace(registry_dir):
    assert ActivityRegistryReader().read().entries == []
    assert not registry_dir.exists() or list(registry_dir.iterdir()) == []


class TestTheRunsEnterThemselves:

    def test_a_sweep_and_its_backtests_are_listed_one_under_the_other(self, witness,
                                                                         registry_dir):
        sweep_id = OptimizationRunner().run(MINI_GRID, channel=RunChannel.CLI)
        kinds = [kind for kind, _, _ in witness]
        assert kinds[0] is ActivityKind.SWEEP and ActivityKind.BACKTEST in kinds
        _, _, sweep = witness[0]
        assert sweep is not None and sweep.sweep_id == sweep_id
        assert sweep.origin is not None and sweep.origin.channel is RunChannel.CLI
        for kind, _, entry in witness[1:]:
            assert kind is ActivityKind.BACKTEST and entry is not None
            assert entry.parent_activity_id == sweep.activity_id
            assert entry.run_id and entry.origin.channel is RunChannel.SWEEP
        _assert_left_no_trace(registry_dir)

    def test_an_autotrader_session_is_listed_under_its_run_id(self, witness, registry_dir,
                                                               monkeypatch):
        """Refused at its start — cheap, and the path that must still leave the list."""
        def no_identity(configs):
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(autotrader_main_module, 'capture_code_identity', no_identity)
        config = load_autotrader_config(PROFILE)
        AutotraderMain(config).run()
        [(kind, label, entry)] = witness
        assert kind is ActivityKind.AUTOTRADER_SESSION and entry is not None
        # The name every report row and the carry-over key give the session
        assert entry.run_id and label == config.get_unit_name()
        _assert_left_no_trace(registry_dir)


class TestTheOtherWorkEntersItself:

    def test_an_import_is_listed_by_its_import_id(self, witness, registry_dir, monkeypatch):
        monkeypatch.setattr(ImportCoordinator, '_run', lambda self: ImportOutcome.COMPLETED)
        coordinator = ImportCoordinator(paths=import_paths_from_config(), show_display=False,
                                        channel=RunChannel.CLI)
        assert coordinator.run() is ImportOutcome.COMPLETED
        [(kind, label, entry)] = witness
        assert kind is ActivityKind.IMPORT and entry is not None
        assert label.startswith('20') and entry.origin.channel is RunChannel.CLI
        _assert_left_no_trace(registry_dir)

    def test_a_cache_rebuild_is_listed(self, witness, registry_dir, monkeypatch):
        monkeypatch.setattr(DiscoveryCacheManager, '_rebuild_all',
                            lambda self, force, on_cache_done: {})
        DiscoveryCacheManager().rebuild_all(force=True)
        [(kind, label, entry)] = witness
        assert kind is ActivityKind.DISCOVERY_CACHE_BUILD and entry is not None
        assert label == 'all caches, forced'
        _assert_left_no_trace(registry_dir)

    def test_the_test_suite_is_listed(self, witness, registry_dir, monkeypatch):
        # Through the module: a class named Test… imported here would be collected as a test
        runner_cli = test_runner_cli_module.TestRunnerCli
        monkeypatch.setattr(runner_cli, '_run_suites', lambda self: None)
        runner_cli().cmd_run()
        [(kind, label, entry)] = witness
        assert (kind, label) == (ActivityKind.TEST_SUITE, 'full') and entry is not None
        _assert_left_no_trace(registry_dir)

    def test_the_api_server_is_listed_by_its_command(self, witness, registry_dir, monkeypatch):
        monkeypatch.setattr(api_server_cli_module.uvicorn, 'run', lambda *args, **kwargs: None)
        ApiServerCli(host='127.0.0.1', port=8123, reload=False).run()
        [(kind, label, entry)] = witness
        assert (kind, label) == (ActivityKind.API_SERVER, 'port 8123') and entry is not None
        assert entry.origin.channel is RunChannel.CLI
        _assert_left_no_trace(registry_dir)
