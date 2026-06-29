"""
Optimization runner (#390) — drives a parameter sweep.

Loads the sweep spec + base scenario set, validates the grid STRUCTURE (fail-fast),
expands the grid, and runs each combination as one normal batch through the existing
pipeline. Parameter existence / range are validated per combination inside the run
(Phase 0), so a bad value fails only its own combination. Every combination self-records
its KPIs in the run-results ledger (tagged with the sweep id), so ranking happens
afterwards by reading the ledger — the runner itself collects nothing in memory.
"""

from datetime import datetime, timezone
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.optimization_config_loader import OptimizationConfigLoader
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.optimization.grid_expander import expand_grid
from python.framework.optimization.parameter_override import apply_overrides
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.run_results_types import SweepContext
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.validators.sweep_grid_validator import validate_sweep_grid
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_strategy_runner import initialize_batch_and_run

vLog = get_global_logger()


class OptimizationRunner:
    """Runs a grid sweep: expand → run each combination → ledger records the results."""

    def __init__(self):
        """Initialize the runner with the spec, scenario, and app config loaders."""
        self._spec_loader = OptimizationConfigLoader()
        self._scenario_loader = ScenarioConfigLoader()
        self._app_config = AppConfigManager()

    def run(self, spec_file: str) -> str:
        """
        Run a full parameter sweep.

        Args:
            spec_file: Sweep spec filename or path

        Returns:
            The sweep id (group the ledger rows of this sweep by it)
        """
        spec = self._spec_loader.load_spec(spec_file)
        base = self._scenario_loader.load_config(spec.base_scenario_set)
        if not base.scenarios:
            raise ValueError(
                f"Base scenario set '{spec.base_scenario_set}' has no enabled scenarios")

        # Fail-fast: validate the grid STRUCTURE (path shape + non-empty lists).
        # Param existence / range is validated per combination inside the run (Phase 0).
        validate_sweep_grid(spec.grid, vLog)

        combos = expand_grid(spec.grid)
        sweep_id = f"sweep_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        vLog.info(
            f"🎛 Sweep {sweep_id}: {len(combos)} combination(s) over "
            f"'{spec.base_scenario_set}' (objective: {spec.objective})")

        # All of this sweep's runs (the base mount build + every combination) nest under one
        # grouping dir so the log root stays tidy (#419).
        run_group = f"sweeps/{sweep_id}"

        # Mount reuse (#419): load the data ONCE from the base and reuse it across every
        # combination (the grid varies only strategy_config → constant data identity).
        mount = None
        if self._app_config.get_optimization_mount_reuse_enabled():
            base_set = ScenarioSet(base, self._app_config, run_group=run_group)
            mount = BatchOrchestrator(base_set, self._app_config).build_mount()
            if not mount.scenario_packages:
                # Data-level failure (invalid window / missing data) — invariant across every
                # combination → abort the whole sweep, nothing ran (§35).
                vLog.error(
                    f"🛑 Sweep {sweep_id} aborted: the base data could not be loaded for any "
                    f"scenario (invalid window / missing data). Every combination shares this "
                    f"data — nothing was run.")
                return sweep_id

        villain_abort = self._app_config.get_optimization_villain_abort_enabled()
        runs = 0
        for index, combo in enumerate(combos):
            label = f"__{sweep_id}_c{index:03d}"
            cfg = apply_overrides(base, combo, label)
            sweep_context = SweepContext(
                sweep_id=sweep_id, sweep_params=combo,
                objective=spec.objective, maximize=spec.maximize)
            vLog.info(f"  [{index + 1}/{len(combos)}] {combo}")
            summary = initialize_batch_and_run(
                cfg, self._app_config, sweep_context=sweep_context, mount=mount,
                run_group=run_group)
            runs += 1

            # Fail-fast OOM-villain abort: if the FIRST executed combination crashed data-level
            # (a worker subprocess was OOM-killed), every combination would crash identically.
            if villain_abort and index == 0 and self._has_subprocess_oom(summary):
                vLog.error(
                    f"🛑 Sweep {sweep_id} aborted after the first combination: a worker "
                    f"subprocess was terminated (out-of-memory). Every combination shares this "
                    f"data + parallelism → the remaining {len(combos) - 1} would fail "
                    f"identically. Lower max_parallel_scenarios or use smaller windows.")
                break

        vLog.info(f"✅ Sweep {sweep_id} complete — {runs} run(s) recorded in the ledger")
        return sweep_id

    @staticmethod
    def _has_subprocess_oom(summary: Optional[BatchExecutionSummary]) -> bool:
        """
        Whether a run's results carry a subprocess-pool (OOM) crash signature (#419 villain abort).

        Args:
            summary: The combination's BatchExecutionSummary (None if it failed at startup)

        Returns:
            True if any scenario failed with a BrokenProcessPool / SubprocessPoolMemoryError (#416)
        """
        if summary is None:
            return False
        oom_markers = ('BrokenProcessPool', 'SubprocessPoolMemoryError')
        return any(
            (result.error_type or '') in oom_markers
            or any(marker in (result.error_message or '') for marker in oom_markers)
            for result in summary.process_result_list
        )
