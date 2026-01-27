"""
Market Configuration File Loader
Singleton pattern for loading market_config.json
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple


class MarketConfigFileLoader:
    """
    Singleton loader for market_config.json.

    Thread-safe, loads config only once and caches it.
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: str = "configs/market_config.json"
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
        Internal load logic.

        Returns:
            Loaded config dict
        """
        config_path = Path(MarketConfigFileLoader._config_path)

        if not config_path.exists():
            raise FileNotFoundError(
                f"❌ Market config not found: {config_path}\n"
                f"   Please create {config_path} with broker and market type mappings."
            )

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        print(f"📋 Loaded market config: {config_path}")
        return config
