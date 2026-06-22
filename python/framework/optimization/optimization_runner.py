"""
Optimization runner (#390) — drives a parameter sweep.

Loads the sweep spec + base scenario set, validates the grid against the component
schemas (fail-fast), expands the grid, and runs each combination as one normal batch
through the existing pipeline. Every combination self-records its KPIs in the run-results
ledger (tagged with the sweep id), so ranking happens afterwards by reading the ledger —
the runner itself collects nothing in memory.
"""

from datetime import datetime, timezone

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.optimization_config_loader import OptimizationConfigLoader
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.optimization.grid_expander import expand_grid
from python.framework.optimization.parameter_override import apply_overrides
from python.framework.types.run_results_types import SweepContext
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

        # Fail-fast: validate every grid value against its component's parameter schema
        validate_sweep_grid(spec.grid, base.scenarios[0].strategy_config, vLog)

        combos = expand_grid(spec.grid)
        sweep_id = f"sweep_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        vLog.info(
            f"🎛 Sweep {sweep_id}: {len(combos)} combination(s) over "
            f"'{spec.base_scenario_set}' (objective: {spec.objective})")

        for index, combo in enumerate(combos):
            label = f"__{sweep_id}_c{index:03d}"
            cfg = apply_overrides(base, combo, label)
            sweep_context = SweepContext(sweep_id=sweep_id, sweep_params=combo)
            vLog.info(f"  [{index + 1}/{len(combos)}] {combo}")
            initialize_batch_and_run(cfg, self._app_config, sweep_context=sweep_context)

        vLog.info(f"✅ Sweep {sweep_id} complete — {len(combos)} run(s) recorded in the ledger")
        return sweep_id
