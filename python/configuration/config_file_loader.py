import json
from pathlib import Path
from threading import Lock
import traceback
from typing import Any, Dict, Optional, Tuple


class ConfigFileLoader:
    """
    Simple loader for program main configuration: app_config.json
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: Optional[str] = "configs/app_config.json"
    _user_config_path: Optional[str] = "user_configs/app_config.json"
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
                "ConfigFileLoader not initialized. Call initialize(path).")

        # Load base configuration
        try:
            with open(ConfigFileLoader._config_path, "r") as f:
                base_config = json.load(f)
            print(f"📋 Loaded config: {ConfigFileLoader._config_path}")
        except FileNotFoundError:
            raise FileNotFoundError(
                f"❌ Config file not found: {ConfigFileLoader._config_path}\n"
                f"   Please ensure configs/app_config.json exists."
            )
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"❌ Invalid JSON in config file: {ConfigFileLoader._config_path}\n"
                f"   Error: {e}"
            )
        except Exception as e:
            raise RuntimeError(
                f"❌ Failed to load config: {ConfigFileLoader._config_path}\n"
                f"   Error: {e}"
            )

        # Try to load user override configuration
        user_config_path = ConfigFileLoader._user_config_path
        if user_config_path and Path(user_config_path).exists():
            try:
                with open(user_config_path, "r") as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = ConfigFileLoader._deep_merge(
                    base_config, user_override)
                print(f"✅ Merged user_configs/app_config.json")
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user config: {user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load user config: {user_config_path}\n"
                    f"Error: {e}"
                )

        # No user config - return base config
        return base_config

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
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
                result[key] = ConfigFileLoader._deep_merge(result[key], value)
            else:
                # Direct override for non-dict values
                result[key] = value

        return result
