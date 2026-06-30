"""
Sweep mount reuse + fail-fast abort tests (#419).

Validates that reusing the prepared data mount across a sweep's combinations produces results
identical to the cold (reload-per-combination) path, the data-level abort fires before any
combination runs, and the OOM villain-abort signature is detected.

The off-switch is exercised by the cold leg of warm==cold (mount reuse disabled); strategy-level
errors keeping the sweep alive is the unchanged §33 behavior (covered by the Phase-0
parameter-validation tests).
"""

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.optimization.optimization_runner import OptimizationRunner
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.mount_package_types import MountPackage
from python.framework.types.process_data_types import ProcessResult

MINI_GRID = 'tests/fixtures/optimization/btcusd_mini_grid.json'
WARMUP_GRID = 'tests/fixtures/optimization/btcusd_mini_grid_warmup.json'


def _kpis_by_hash(rows):
    """Map each ledger row to a deterministic KPI signature, keyed by param_hash."""
    return {
        row.param_hash: (row.status, round(row.net_pnl, 6),
                         row.total_trades, round(row.win_rate, 6))
        for row in rows
    }


def _warm_then_cold(grid_spec, monkeypatch):
    """Run a sweep warm (mount reuse) then cold (off-switch); return (warm, cold) KPI maps."""
    ledger = RunResultsLedger(AppConfigManager().get_run_results_path())
    sweep_warm = OptimizationRunner().run(grid_spec)  # mount reuse on (default)

    monkeypatch.setattr(
        AppConfigManager, 'get_optimization_mount_reuse_enabled', lambda self: False)
    sweep_cold = OptimizationRunner().run(grid_spec)  # off-switch → cold reload path

    return (_kpis_by_hash(ledger.read_rows(sweep_id=sweep_warm)),
            _kpis_by_hash(ledger.read_rows(sweep_id=sweep_cold)))


def test_sweep_warm_equals_cold(monkeypatch):
    """A mount-reused (warm) sweep yields ledger results identical to the cold reload path (#368)."""
    warm, cold = _warm_then_cold(MINI_GRID, monkeypatch)
    assert warm, 'warm sweep produced no ledger rows'
    assert warm == cold


def test_warmup_affecting_sweep_falls_back_to_cold(monkeypatch):
    """A nested-period sweep changes the warmup window, so one combination's data identity differs
    from the mount and it reloads (#419 cold fallback) — the result must still match the cold path."""
    warm, cold = _warm_then_cold(WARMUP_GRID, monkeypatch)
    assert len(warm) == 2, 'both period combinations should run (one warm, one reloaded)'
    assert warm == cold


def test_data_level_abort_records_nothing(monkeypatch):
    """A base mount with no loadable data aborts the whole sweep before any combination runs."""
    empty_mount = MountPackage(
        scenario_packages={}, clipping_stats_map={}, broker_configs={},
        broker_scenario_map={}, requirements_map=None, warmup_phases=[],
        batch_warmup_time=0.0, data_identity={})
    monkeypatch.setattr(BatchOrchestrator, 'build_mount', lambda self: empty_mount)

    ledger = RunResultsLedger(AppConfigManager().get_run_results_path())
    sweep_id = OptimizationRunner().run(MINI_GRID)

    assert ledger.read_rows(sweep_id=sweep_id) == [], 'aborted sweep must record no runs'


def test_oom_villain_aborts_sweep(monkeypatch):
    """An OOM crash in the first executed combination aborts the rest of the sweep (§35)."""
    nonempty_mount = MountPackage(
        scenario_packages={0: object()}, clipping_stats_map={}, broker_configs={},
        broker_scenario_map={}, requirements_map=None, warmup_phases=[],
        batch_warmup_time=0.0, data_identity={})
    monkeypatch.setattr(BatchOrchestrator, 'build_mount', lambda self: nonempty_mount)

    class _OomSummary:
        process_result_list = [ProcessResult(
            success=False, scenario_name='c0', scenario_index=0,
            error_type='BrokenProcessPool', error_message='a process terminated abruptly')]

    calls = {'count': 0}

    def _fake_run(scenario_config_data, app_config_loader, sweep_context=None,
                  mount=None, run_group=None):
        calls['count'] += 1
        return _OomSummary()

    monkeypatch.setattr(
        'python.framework.optimization.optimization_runner.initialize_batch_and_run', _fake_run)

    OptimizationRunner().run(MINI_GRID)

    # The 4-combo grid must stop after the first combination once the OOM is seen.
    assert calls['count'] == 1
