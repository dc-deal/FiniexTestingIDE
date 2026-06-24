"""
Scenario Generator Config Saver
================================
Saves generation results as scenario set JSON configs.

Loads a template, fills in generated scenarios, and writes the output file.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
)
from python.scenario.generator.balance_defaults import ensure_quote_balance, resolve_quote_currency
from python.scenario.generator.role_assignment import assign_roles_time_ordered
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
        filename: str,
        robustness: Optional[RobustnessConfig] = None
    ) -> Path:
        """
        Save generation result as scenario set config.

        Args:
            result: Generation result with scenarios
            filename: Output filename
            robustness: Optional robustness mode (#367) — writes the top-level block + assigns
                time-ordered IS/OOS roles to the scenarios when enabled

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

        # Always provide a balance in the symbol's quote currency, so the generated set passes
        # validation and runs out of the box (cascade-capable keys are no longer emitted per
        # scenario, so the balance must live set-wide in global). The quote is resolved
        # authoritatively from the broker config (#265) — broker_type comes from the candidates.
        global_config = config.setdefault('global', {})
        if result.scenarios:
            quote_currency = resolve_quote_currency(
                result.symbol, result.scenarios[0].broker_type)
            global_config['trade_simulator_config'] = ensure_quote_balance(
                global_config.get('trade_simulator_config'), quote_currency)

        # Robustness mode (#367): write the set-wide block (positioned ABOVE `global`, since it
        # is a set-wide mode, not a cascade default) + assign time-ordered IS/OOS roles.
        roles = None
        if robustness is not None and robustness.enabled:
            roles = assign_roles_time_ordered(len(result.scenarios), robustness.oos_split)
            rob = robustness.model_dump(mode='json')
            reordered = {}
            for key, value in config.items():
                if key == 'global':
                    reordered['robustness'] = rob
                reordered[key] = value
            reordered.setdefault('robustness', rob)  # no `global` in template → still present
            config = reordered

        # Add scenarios
        scenarios = result.scenarios
        config['scenarios'] = []
        for i, candidate in enumerate(scenarios, 1):
            name = f"{result.symbol}_{result.strategy.value}_{i:02d}"
            # Blocks strategy: max_ticks = None (time-based only)
            use_max_ticks = None if result.strategy == GenerationStrategy.BLOCKS else candidate.estimated_ticks
            role = roles[i - 1].value if roles else None
            scenario_dict = candidate.to_scenario_dict(name, use_max_ticks, role)
            config['scenarios'].append(scenario_dict)

        # Save to file
        output_path = self._output_dir / filename
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

        vLog.info(f"Saved {len(scenarios)} scenarios to {output_path}")

        return output_path
