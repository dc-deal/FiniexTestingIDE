"""
Market Configuration File Loader
Singleton pattern for loading market_config.json
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active


class MarketConfigFileLoader:
    """
    Singleton loader for market_config.json.

    Thread-safe, loads config only once and caches it.
    Supports user overrides from user_configs/market_config.json.
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: str = "configs/market_config.json"
    _user_config_path: str = "user_configs/market_config.json"  # ← ADDED
    _lock = Lock()

    @staticmethod
    def initialize(config_path: str) -> None:
        """
        Initialize the loader with a custom path.

        Args:
            config_path: Path to market_config.json
        """
        MarketConfigFileLoader._config_path = config_path

    @staticmethod
    def get_config() -> Tuple[Dict[str, Any], bool]:
        """
        Get cached configuration or load if not yet loaded.

        Returns:
            Tuple of (config_dict, was_first_load)
        """
        with MarketConfigFileLoader._lock:
            was_first_load = False

            if MarketConfigFileLoader._config is None:
                MarketConfigFileLoader._config = MarketConfigFileLoader._load()
                was_first_load = True

            return MarketConfigFileLoader._config, was_first_load

    @staticmethod
    def reload() -> Dict[str, Any]:
        """
        Force reload of configuration.

        Returns:
            Reloaded config dict
        """
        with MarketConfigFileLoader._lock:
            MarketConfigFileLoader._config = MarketConfigFileLoader._load()
            return MarketConfigFileLoader._config

    @staticmethod
    def _load() -> Dict[str, Any]:
        """
        Load market configuration with user override support.

        Loads base config from configs/market_config.json and optionally
        merges user overrides from user_configs/market_config.json.

        Returns:
            Merged configuration dictionary
        """
        config_path = Path(MarketConfigFileLoader._config_path)

        # Load base configuration
        if not config_path.exists():
            raise FileNotFoundError(
                f"❌ Market config not found: {config_path}\n"
                f"   Please create {config_path} with broker and market type mappings."
            )

        with open(config_path, "r", encoding="utf-8") as f:
            base_config = json.load(f)

        print(f"📋 Loaded market config: {config_path}")

        # Try to load user override configuration.
        # Tests run with FINIEX_CONFIG_ISOLATION=1 (see tests/conftest.py) — the
        # user workspace must never bleed into the test suite (non-determinism).
        user_config_path = Path(MarketConfigFileLoader._user_config_path)
        if user_config_path.exists() and not is_config_isolation_active():
            try:
                with open(user_config_path, "r", encoding="utf-8") as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = MarketConfigFileLoader._deep_merge(
                    base_config, user_override)
                print(f"✅ Merged user_configs/market_config.json")
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user market config: {user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load user market config: {user_config_path}\n"
                    f"Error: {e}"
                )

        # No user config - return base config
        return base_config

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        return deep_merge(base, override)
