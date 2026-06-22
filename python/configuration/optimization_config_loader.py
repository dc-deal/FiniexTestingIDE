"""
Parameter Optimization config loader (#390).

Loads + validates a sweep spec (`configs/sweeps/<name>.json`). Resolution mirrors the
scenario loader: a direct path, then the user algo dirs (private specs live there), then
the public `configs/sweeps/` convention dir. Pydantic (`extra='forbid'`) guards the spec
structure; grid-value validation against the component schemas happens in the runner once
the base scenario set is loaded.
"""

import json
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.optimization_config_types import SweepSpec

# Public convention directory for sweep specs (private specs live under user algo dirs).
SWEEPS_DIR = Path('configs/sweeps')


class OptimizationConfigLoader:
    """Loads sweep specs from JSON config files."""

    def __init__(self):
        """Initialize loader with user-algo search dirs from AppConfigManager."""
        self._user_algo_dirs = [Path(d) for d in AppConfigManager().get_user_algo_dirs()]

    def load_spec(self, spec_file: str) -> SweepSpec:
        """
        Load and structurally validate a sweep spec.

        Args:
            spec_file: Spec filename or path (e.g. 'cautious_macd_grid.json')

        Returns:
            The parsed SweepSpec (sweep_name defaults to the file stem)
        """
        path = self._resolve_path(spec_file)
        if not path.exists():
            raise FileNotFoundError(f"Sweep spec not found: {path}")

        with open(path, 'r') as f:
            data = json.load(f)

        spec = SweepSpec(**data)
        if not spec.sweep_name:
            spec = spec.model_copy(update={'sweep_name': path.stem})
        return spec

    def _resolve_path(self, filename: str) -> Path:
        """
        Resolve a sweep spec path: direct → user algo dirs (recursive) → configs/sweeps.

        Args:
            filename: Full path or spec filename

        Returns:
            Resolved Path
        """
        direct = Path(filename)
        if direct.exists():
            return direct

        for algo_dir in self._user_algo_dirs:
            if not algo_dir.exists():
                continue
            for found in algo_dir.rglob(filename):
                return found

        return SWEEPS_DIR / filename
