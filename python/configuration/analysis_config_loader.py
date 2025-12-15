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
    """

    def __init__(self, config_path: str = "configs/generator/analysis_config.json"):
        """ 
            Init & load
        """
        self.config_path = Path(config_path)
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
        Load analysis configuration from file.

        Args:
            config_path: Path to analysis_config.json

        Returns:
            Config dict, or default config if file not found
        """

        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
                return config
        except Exception as e:
            vLog.error(f"Failed to load analysis_config: {e}")
            raise e
