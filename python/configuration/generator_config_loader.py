"""
Generator Configuration Loader
Loader for generator_config.json (strategy configurations for scenario generation).
"""

import json
from pathlib import Path
from typing import Any, Dict

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.scenario_generator_types import GeneratorConfig

vLog = get_global_logger()


class GeneratorConfigLoader:
    """
    Loader for generator configuration.

    Loads strategy configs from configs/generator/generator_config.json.
    """

    def __init__(
        self,
        config_path: str = 'configs/generator/generator_config.json',
        user_config_path: str = 'user_configs/generator_config.json'
    ):
        """
        Initialize generator config loader.

        Args:
            config_path: Path to base generator config
            user_config_path: Path to user override config
        """
        self._config_path = Path(config_path)
        self._user_config_path = Path(user_config_path)

    def get_generator_config(self) -> GeneratorConfig:
        """
        Load and return GeneratorConfig.

        Returns:
            GeneratorConfig instance
        """
        generator_data = self._load()
        return GeneratorConfig.from_dict(generator_data)

    def _load(self) -> Dict[str, Any]:
        """
        Load generator configuration with user override support.

        Returns:
            Merged config dict
        """
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"Generator config not found: {self._config_path}\n"
                f"Expected at configs/generator/generator_config.json"
            )

        try:
            with open(self._config_path, 'r') as f:
                base_config = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Invalid JSON in generator config: {self._config_path}\n"
                f"Error: {e}"
            )

        # Try to load user override configuration
        if self._user_config_path.exists():
            try:
                with open(self._user_config_path, 'r') as f:
                    user_override = json.load(f)

                merged_config = self._deep_merge(base_config, user_override)
                vLog.debug(
                    f"Merged user generator config from {self._user_config_path}"
                )
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user generator config: {self._user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )

        return base_config

    def _deep_merge(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deep merge override dict into base dict.

        Args:
            base: Base configuration dictionary
            override: Override configuration dictionary

        Returns:
            Merged configuration dictionary
        """
        result = base.copy()

        for key, value in override.items():
            if (
                key in result and
                isinstance(result[key], dict) and
                isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result
