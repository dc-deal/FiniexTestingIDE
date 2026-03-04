"""
Scenario Generator Config Saver
================================
Saves generation results as scenario set JSON configs.

Loads a template, fills in generated scenarios, and writes the output file.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
)
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class ScenarioGeneratorConfigSaver:
    """
    Saves scenario generation results as JSON config files.

    Loads a template header, populates it with generated scenarios,
    and writes the final config to disk.
    """

    def __init__(self):
        """Initialize with template and output paths from AppConfigManager."""
        app_config = AppConfigManager()
        self._template_path = Path(app_config.get_generator_template_path())
        self._output_dir = Path(app_config.get_generator_output_path())
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def save_config(
        self,
        result: GenerationResult,
        filename: str
    ) -> Path:
        """
        Save generation result as scenario set config.

        Args:
            result: Generation result with scenarios
            filename: Output filename

        Returns:
            Path to saved config file
        """
        # Load template
        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Scenario template not found: {self._template_path}\n"
                f"Configure 'generator_template' in app_config.json paths section.\n"
                f"This file is required for generating scenario configs."
            )

        with open(self._template_path, 'r') as f:
            config = json.load(f)

        # Update metadata
        config['version'] = "1.0"
        config['scenario_set_name'] = filename.replace('.json', '')
        config['created'] = datetime.now(timezone.utc).isoformat()

        # Add scenarios
        scenarios = result.scenarios
        config['scenarios'] = []
        for i, candidate in enumerate(scenarios, 1):
            name = f"{result.symbol}_{result.strategy.value}_{i:02d}"
            # Blocks/HighVolatility strategy: max_ticks = None (time-based only)
            use_max_ticks = None if result.strategy in [
                GenerationStrategy.BLOCKS,
                GenerationStrategy.HIGH_VOLATILITY
            ] else candidate.estimated_ticks
            scenario_dict = candidate.to_scenario_dict(name, use_max_ticks)
            config['scenarios'].append(scenario_dict)

        # Save to file
        output_path = self._output_dir / filename
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

        vLog.info(f"Saved {len(scenarios)} scenarios to {output_path}")

        return output_path
