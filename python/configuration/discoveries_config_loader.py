"""
Discoveries Configuration Loader
Simple loader for discoveries_config.json with typed accessors.
"""

import json
from pathlib import Path
from typing import Any, Dict

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.market_types.market_volatility_profile_types import (
    CrossInstrumentRankingConfig,
    VolatilityProfileConfig,
)

vLog = get_global_logger()


class DiscoveriesConfigLoader:
    """
    Simple loader for discoveries configuration.

    Loads configs/discoveries/discoveries_config.json without caching.
    Supports user overrides from user_configs/discoveries_config.json.
    """

    def __init__(self,
                 config_path: str = 'configs/discoveries/discoveries_config.json',
                 user_config_path: str = 'user_configs/discoveries_config.json'):
        """
        Initialize discoveries config loader.

        Args:
            config_path: Path to base discoveries config
            user_config_path: Path to user override config
        """
        self.config_path = Path(config_path)
        self.user_config_path = Path(user_config_path)
        self.config = self._load()

    def get_config_raw(self) -> Dict[str, Any]:
        """
        Load Config
        """
        return self._load()

    def get_volatility_profile_config(self) -> VolatilityProfileConfig:
        """
        Get typed volatility profile configuration.

        Returns:
            VolatilityProfileConfig instance
        """
        data = self.config.get('volatility_profile', {})
        return VolatilityProfileConfig(
            timeframe=data.get('timeframe', 'M5'),
            atr_period=data.get('atr_period', 14),
            regime_granularity_hours=data.get('regime_granularity_hours', 1),
            regime_thresholds=data.get(
                'regime_thresholds', [0.5, 0.8, 1.2, 1.8]
            ),
        )

    def get_cross_instrument_ranking_config(self) -> CrossInstrumentRankingConfig:
        """
        Get typed cross-instrument ranking configuration.

        Returns:
            CrossInstrumentRankingConfig instance
        """
        data = self.config.get('cross_instrument_ranking', {})
        return CrossInstrumentRankingConfig(
            top_count=data.get('top_count', 3)
        )

    def _load(self) -> Dict[str, Any]:
        """
        Load discoveries configuration with user override support.

        Loads base config from configs/discoveries/discoveries_config.json and
        optionally merges user overrides from user_configs/discoveries_config.json.

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
            vLog.error(f"Failed to load discoveries_config: {e}")
            raise e

        # Try to load user override configuration
        if self.user_config_path.exists():
            try:
                with open(self.user_config_path, 'r') as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = self._deep_merge(base_config, user_override)
                vLog.debug(
                    f"Merged user discoveries config from {self.user_config_path}")
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user discoveries config: {self.user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )
            except Exception as e:
                vLog.error(f"Failed to load user discoveries config: {e}")
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
