import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from python.configuration.data_home import workspace_path
from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active


class ConfigFileLoader:
    """
    Simple loader for program main configuration: app_config.json
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: Optional[str] = 'configs/app_config.json'
    _user_config_name: Optional[str] = 'app_config.json'
    _lock = Lock()

    @staticmethod
    def initialize(config_path: str):
        """Init the loader with a path (only once)."""
        ConfigFileLoader._config_path = config_path

    @staticmethod
    def get_config() -> Tuple[Dict[str, Any], bool]:
        """
        Returns the configuration and whether it was loaded during this call.

        Loads the config once and caches it. The boolean indicates if the config
        was freshly loaded (`True`) or returned from cache (`False`).

        Returns:
            Tuple[Dict[str, Any], bool]: (config_dict, was_first_load)
        """
        with ConfigFileLoader._lock:
            was_first_load = False

            if ConfigFileLoader._config is None:
                ConfigFileLoader._config = ConfigFileLoader._load()
                was_first_load = True

            return ConfigFileLoader._config, was_first_load

    @staticmethod
    def reload() -> Dict[str, Any]:
        """Force reload of config."""
        with ConfigFileLoader._lock:
            ConfigFileLoader._config = ConfigFileLoader._load()
            return ConfigFileLoader._config

    @staticmethod
    def _load() -> Dict[str, Any]:
        """
        Load configuration with user override support.

        Loads base config from configs/app_config.json and optionally
        merges user overrides from user_configs/app_config.json.

        Returns:
            Merged configuration dictionary
        """
        if ConfigFileLoader._config_path is None:
            raise RuntimeError(
                'ConfigFileLoader not initialized. Call initialize(path).')

        # Load base configuration
        try:
            with open(ConfigFileLoader._config_path, 'r') as f:
                base_config = json.load(f)
            print(f'📋 Loaded config: {ConfigFileLoader._config_path}')
        except FileNotFoundError:
            raise FileNotFoundError(
                f'❌ Config file not found: {ConfigFileLoader._config_path}\n'
                f'   Please ensure configs/app_config.json exists.'
            )
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f'❌ Invalid JSON in config file: {ConfigFileLoader._config_path}\n'
                f'   Error: {e}'
            )
        except Exception as e:
            raise RuntimeError(
                f'❌ Failed to load config: {ConfigFileLoader._config_path}\n'
                f'   Error: {e}'
            )

        # Try to load user override configuration.
        # Tests run with FINIEX_CONFIG_ISOLATION=1 (see tests/conftest.py) — the
        # user workspace must never bleed into the test suite (non-determinism).
        user_config_path = ConfigFileLoader._workspace_override_path()
        if user_config_path is not None:
            try:
                with open(user_config_path, 'r') as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = ConfigFileLoader._deep_merge(
                    base_config, user_override)
                print(f'✅ Merged {user_config_path}')
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f'Invalid JSON in user config: {user_config_path}\n'
                    f'Error: {e}\n'
                    f'Please fix the JSON syntax or remove the file.'
                )
            except Exception as e:
                raise RuntimeError(
                    f'Failed to load user config: {user_config_path}\n'
                    f'Error: {e}'
                )

        # No user config - return base config
        return base_config

    @staticmethod
    def get_layers() -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        The shipped configuration and the workspace override as each file holds them, unmerged —
        for a reader that has to say which of the two a value came from (#626).

        Returns:
            (shipped, override); the override is empty when there is none to merge
        """
        with open(ConfigFileLoader._config_path, 'r') as f:
            shipped = json.load(f)
        override_path = ConfigFileLoader._workspace_override_path()
        if override_path is None:
            return shipped, {}
        with open(override_path, 'r') as f:
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
        name = ConfigFileLoader._user_config_name
        path = workspace_path(name) if name else None
        if path is None or not path.exists() or is_config_isolation_active():
            return None
        return path

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        return deep_merge(base, override)
