import json
from threading import Lock
from typing import Any, Dict, Optional, Tuple


class ConfigFileLoader:
    _config: Optional[Dict[str, Any]] = None
    _config_path: Optional[str] = "configs/app_config.json"
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
        """Internal load logic — uses your existing load_config."""
        if ConfigFileLoader._config_path is None:
            raise RuntimeError(
                "ConfigFileLoader not initialized. Call initialize(path).")

        try:
            with open(ConfigFileLoader._config_path, "r") as f:
                config = json.load(f)
            print(f"📋 Loaded config: {ConfigFileLoader._config_path}")
            return config
        except Exception as e:
            print(f"❌ Failed to load config ({e}), using defaults.")
            return _get_default_config()


# ---- Your existing default config ----
def _get_default_config() -> Dict[str, Any]:
    return {
        "version": "1.0",
        "description": "FiniexTestingIDE - Application Configuration",
        "development": {"dev_mode": False},
        "execution": {
            "default_parallel_scenarios": None,
            "default_max_parallel_scenarios": 20,
            "default_parallel_workers": None,
            "default_worker_parallel_threshold_ms": 1.0,
        },
        "console_logging": {
            "enabled": None,
            "log_level": "INFO",
            "warn_on_parameter_override": None,
            "scenario": {
                "enabled": None,
                "log_level": None,
                "write_system_info": None,
            },
        },
        "file_logging": {
            "enabled": None,
            "log_level": "INFO",
            "log_path": "logs/",
            "append_mode": None,
            "scenario": {
                "enabled": None,
                "log_level": "DEBUG",
                "log_root_path": "logs/scenario_sets",
                "file_name_prefix": "scenario"
            },
        },
        "importer": {"move_processed_files": None, "delete_on_error": False},
        "paths": {
            "scenario_sets": "configs/scenario_sets",
            "brokers": "configs/brokers",
            "generator_template": "configs/generator/template_scenario_set_header.json",
            "generator_output": "configs/scenario_sets",
            "data_raw": "data/raw",
            "data_processed": "data/processed",
            "data_finished": "data/finished"
        },
        "monitoring": {
            "enabled": None,
            "tui_refresh_rate_ms": 300,
            "detailed_live_stats": None,
            "detailed_live_stats_threshold": 3,
            "detailed_live_stats_exports": {
                "export_portfolio_stats": False,
                "export_performance_stats": False,
                "export_current_bars": False,
            },
        },
        "data_validation": {
            "warmup_quality_mode": "standard",
            "allowed_gap_categories": ["seamless", "short"]
        }
    }
