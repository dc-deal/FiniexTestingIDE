"""
Market Configuration File Loader
Singleton pattern for loading market_config.json
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from python.configuration.data_home import workspace_path
from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active


class MarketConfigFileLoader:
    """
    Singleton loader for market_config.json.

    Thread-safe, loads config only once and caches it.
    Supports user overrides from user_configs/market_config.json.
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: str = 'configs/market_config.json'
    _user_config_name: str = 'market_config.json'
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
                f'❌ Market config not found: {config_path}\n'
                f'   Please create {config_path} with broker and market type mappings.'
            )

        with open(config_path, 'r', encoding='utf-8') as f:
            base_config = json.load(f)

        print(f'📋 Loaded market config: {config_path}')

        # Try to load user override configuration.
        # Tests run with FINIEX_CONFIG_ISOLATION=1 (see tests/conftest.py) — the
        # user workspace must never bleed into the test suite (non-determinism).
        user_config_path = MarketConfigFileLoader._workspace_override_path()
        if user_config_path is not None:
            try:
                with open(user_config_path, 'r', encoding='utf-8') as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = MarketConfigFileLoader._deep_merge(
                    base_config, user_override)
                print(f'✅ Merged {user_config_path}')
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f'Invalid JSON in user market config: {user_config_path}\n'
                    f'Error: {e}\n'
                    f'Please fix the JSON syntax or remove the file.'
                )
            except Exception as e:
                raise RuntimeError(
                    f'Failed to load user market config: {user_config_path}\n'
                    f'Error: {e}'
                )

        # No user config - return base config
        return base_config

    @staticmethod
    def get_layers() -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        The shipped market configuration and the workspace override as each file holds them,
        unmerged — for a reader that has to say which of the two a value came from (#626).

        Returns:
            (shipped, override); the override is empty when there is none to merge
        """
        with open(MarketConfigFileLoader._config_path, 'r', encoding='utf-8') as f:
            shipped = json.load(f)
        override_path = MarketConfigFileLoader._workspace_override_path()
        if override_path is None:
            return shipped, {}
        with open(override_path, 'r', encoding='utf-8') as f:
            return shipped, json.load(f)

    @staticmethod
    def _workspace_override_path() -> Optional[Path]:
        """
        The workspace override this loader merges, when there is one to merge.

        Tests run with FINIEX_CONFIG_ISOLATION=1 (see tests/conftest.py) — the user workspace must
        never bleed into the test suite (non-determinism).

        Returns:
            Its path; None when there is none, or the configuration is isolated
        """
        path = workspace_path(MarketConfigFileLoader._user_config_name)
        if not path.exists() or is_config_isolation_active():
            return None
        return path

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge user override into base market config.

        The 'brokers' list is matched per 'broker_type' identifier via the shared
        list-merge mechanism — base brokers stay intact unless explicitly overridden.

        Args:
            base: Base market config dict
            override: User override dict from user_configs/

        Returns:
            New merged config dict
        """
        return deep_merge(base, override, list_merge_keys={'brokers': 'broker_type'})
