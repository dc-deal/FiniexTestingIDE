"""
Analysis Configuration Loader
Simple loader for analysis_config.json
"""

import json
from pathlib import Path
from typing import Any, Dict
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_generator_types import GeneratorConfig
vLog = get_global_logger()


class AnalysisConfigLoader:
    """
    Simple loader for analysis configuration.

    Loads configs/generator/analysis_config.json without caching.
    Supports user overrides from user_configs/analysis_config.json.
    """

    def __init__(self,
                 config_path: str = "configs/generator/analysis_config.json",
                 user_config_path: str = "user_configs/analysis_config.json"):
        """ 
        Initialize analysis config loader.

        Args:
            config_path: Path to base analysis config
            user_config_path: Path to user override config
        """
        self.config_path = Path(config_path)
        self.user_config_path = Path(user_config_path)
        self.config = self._load()

    def get_generator_config(self) -> GeneratorConfig:
        """
        Load and get Genearator Config
        """
        config = self._load()
        return GeneratorConfig.from_dict(config)

    def get_config_raw(self) -> Dict[str, Any]:
        """
        Load Config
        """
        return self._load()

    def _load(self) -> Dict[str, Any]:
        """
        Load analysis configuration with user override support.

        Loads base config from configs/generator/analysis_config.json and
        optionally merges user overrides from user_configs/analysis_config.json.

        Returns:
            Merged config dict, or empty dict if base config not found
        """
        # Load base configuration
        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, 'r') as f:
                base_config = json.load(f)
        except Exception as e:
            vLog.error(f"Failed to load analysis_config: {e}")
            raise e

        # Try to load user override configuration
        if self.user_config_path.exists():
            try:
                with open(self.user_config_path, 'r') as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = self._deep_merge(base_config, user_override)
                vLog.debug(
                    f"Merged user analysis config from {self.user_config_path}")
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user analysis config: {self.user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )
            except Exception as e:
                vLog.error(f"Failed to load user analysis config: {e}")
                raise e

        # No user config - return base config
        return base_config

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
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
                # Recursive merge for nested dicts
                result[key] = self._deep_merge(result[key], value)
            else:
                # Direct override for non-dict values
                result[key] = value

        return result
