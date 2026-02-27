"""
Import Configuration File Loader.

Singleton pattern for loading import_config.json with user override support.
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple


class ImportConfigFileLoader:
    """
    Singleton loader for import_config.json.

    Thread-safe, loads config only once and caches it.
    Supports user overrides from user_config/import_config.json.
    """

    _config: Optional[Dict[str, Any]] = None
    _config_path: str = "configs/import_config.json"
    _user_config_path: str = "user_config/import_config.json"
    _lock = Lock()

    @staticmethod
    def initialize(config_path: str) -> None:
        """
        Initialize the loader with a custom path.

        Args:
            config_path: Path to import_config.json
        """
        ImportConfigFileLoader._config_path = config_path

    @staticmethod
    def get_config() -> Tuple[Dict[str, Any], bool]:
        """
        Get cached configuration or load if not yet loaded.

        Returns:
            Tuple of (config_dict, was_first_load)
        """
        with ImportConfigFileLoader._lock:
            was_first_load = False

            if ImportConfigFileLoader._config is None:
                ImportConfigFileLoader._config = ImportConfigFileLoader._load()
                was_first_load = True

            return ImportConfigFileLoader._config, was_first_load

    @staticmethod
    def reload() -> Dict[str, Any]:
        """
        Force reload of configuration.

        Returns:
            Reloaded config dict
        """
        with ImportConfigFileLoader._lock:
            ImportConfigFileLoader._config = ImportConfigFileLoader._load()
            return ImportConfigFileLoader._config

    @staticmethod
    def _load() -> Dict[str, Any]:
        """
        Load import configuration with user override support.

        Loads base config from configs/import_config.json and optionally
        merges user overrides from user_config/import_config.json.

        Returns:
            Merged configuration dictionary
        """
        config_path = Path(ImportConfigFileLoader._config_path)

        # Load base configuration
        if not config_path.exists():
            raise FileNotFoundError(
                f"❌ Import config not found: {config_path}\n"
                f"   Please create {config_path} with import pipeline settings."
            )

        with open(config_path, "r", encoding="utf-8") as f:
            base_config = json.load(f)

        print(f"📋 Loaded import config: {config_path}")

        # Try to load user override configuration
        user_config_path = Path(ImportConfigFileLoader._user_config_path)
        if user_config_path.exists():
            try:
                with open(user_config_path, "r", encoding="utf-8") as f:
                    user_override = json.load(f)

                # Merge user overrides into base config
                merged_config = ImportConfigFileLoader._deep_merge(
                    base_config, user_override)
                print(f"✅ Merged user_config/import_config.json")
                return merged_config

            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Invalid JSON in user import config: {user_config_path}\n"
                    f"Error: {e}\n"
                    f"Please fix the JSON syntax or remove the file."
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load user import config: {user_config_path}\n"
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
                result[key] = ImportConfigFileLoader._deep_merge(
                    result[key], value)
            else:
                # Direct override for non-dict values
                result[key] = value

        return result
