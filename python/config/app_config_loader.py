"""
FiniexTestingIDE - Application Configuration Loader
Centralized app config management

"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Use standard logging to avoid circular import
logger = logging.getLogger("AppConfigLoader")


class AppConfigLoader:
    """
    Centralized application configuration loader.

    Loads and provides access to app_config.json settings.
    Singleton pattern ensures config is loaded only once.

    """

    _instance: Optional['AppConfigLoader'] = None
    _config: Optional[Dict[str, Any]] = None
    _project_root: Optional[Path] = None

    def __new__(cls):
        """Singleton pattern - only one instance"""
        if cls._instance is None:
            cls._instance = super(AppConfigLoader, cls).__new__(cls)
        return cls._instance

    def __init__(self, config_path: str = "configs/app_config.json"):
        """
        Initialize app config loader.

        Args:
            config_path: Path to app_config.json
        """
        # Set project root (same as old config.py)
        if AppConfigLoader._project_root is None:
            # Assuming we're in python/config/app_config_loader.py
            # Go up 2 levels to reach project root
            AppConfigLoader._project_root = Path(__file__).parent.parent.parent

        # Only load once
        if AppConfigLoader._config is None:
            self.config_path = config_path
            self._load_config()
            self._print_config_status()

    def _load_config(self):
        """Load configuration from file"""
        config_file = Path(self.config_path)

        if not config_file.exists():
            logger.warning(
                f"⚠️  App config not found: {self.config_path}, using defaults")
            AppConfigLoader._config = self._get_default_config()
            return

        try:
            with open(config_file, 'r') as f:
                AppConfigLoader._config = json.load(f)
            logger.info(f"📋 Loaded app config: {self.config_path}")
        except Exception as e:
            logger.error(f"❌ Failed to load app config: {e}")
            AppConfigLoader._config = self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "version": "1.0",
            "development": {
                "dev_mode": False,
                "debug_logging": False
            },
            "execution": {
                "default_parallel_scenarios": True,
                "default_max_parallel_scenarios": 4,
                "default_parallel_workers": True,
                "default_worker_parallel_threshold_ms": 1.0
            },
            "logging": {
                "performance_tracking": True,
                "show_worker_details": True,
                "show_decision_logic_details": True,
                "warn_on_parameter_override": True,
                "log_level": "INFO"
            },
            "importer": {
                "move_processed_files": True,
                "delete_on_error": False
            },
            "paths": {
                "scenario_sets": "configs/scenario_sets",
                "data_raw": "data/raw",
                "data_processed": "data/processed",
                "data_finished": "data/finished",
                "data_parquet": "data/parquet"
            },
            "monitoring": {
                "tui_refresh_rate_ms": 300,
                "enable_live_stats": False
            }
        }

    def _print_config_status(self):
        """Print config status (replaces old config.py print)"""
        dev_mode = self.get_dev_mode()
        move_files = self.get_move_processed_files()
        logger.info(
            f"🔧 Config loaded - DEV_MODE: {dev_mode}, MOVE_FILES: {move_files}")

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """
        Get full configuration.

        Returns:
            Complete config dict
        """
        if cls._config is None:
            # Auto-initialize if not done yet
            cls()
        return cls._config

    @classmethod
    def get_execution_config(cls) -> Dict[str, Any]:
        """
        Get execution configuration.

        Returns:
            Execution config dict
        """
        config = cls.get_config()
        return config.get("execution", {})

    @classmethod
    def get_logging_config(cls) -> Dict[str, Any]:
        """
        Get logging configuration.

        Returns:
            Logging config dict
        """
        config = cls.get_config()
        return config.get("logging", {})

    @classmethod
    def get_paths_config(cls) -> Dict[str, Any]:
        """
        Get paths configuration.

        Returns:
            Paths config dict
        """
        config = cls.get_config()
        return config.get("paths", {})

    @classmethod
    def get_monitoring_config(cls) -> Dict[str, Any]:
        """
        Get monitoring configuration.

        Returns:
            Monitoring config dict
        """
        config = cls.get_config()
        return config.get("monitoring", {})

    @classmethod
    def get_default_parallel_scenarios(cls) -> bool:
        """
        Get default parallel scenarios setting.

        Returns:
            True if scenarios should run in parallel by default
        """
        exec_config = cls.get_execution_config()
        return exec_config.get("default_parallel_scenarios", True)

    @classmethod
    def get_default_max_parallel_scenarios(cls) -> int:
        """
        Get default max parallel scenarios.

        Returns:
            Max number of scenarios to run in parallel
        """
        exec_config = cls.get_execution_config()
        return exec_config.get("default_max_parallel_scenarios", 4)

    @classmethod
    def get_default_parallel_workers(cls) -> bool:
        """
        Get default parallel workers setting.

        Returns:
            True if workers should run in parallel by default
        """
        exec_config = cls.get_execution_config()
        return exec_config.get("default_parallel_workers", True)

    @classmethod
    def should_track_performance(cls) -> bool:
        """
        Check if performance tracking is enabled.

        Returns:
            True if performance tracking is enabled
        """
        logging_config = cls.get_logging_config()
        return logging_config.get("performance_tracking", True)

    @classmethod
    def should_show_worker_details(cls) -> bool:
        """
        Check if worker details should be shown.

        Returns:
            True if worker details should be displayed
        """
        logging_config = cls.get_logging_config()
        return logging_config.get("show_worker_details", True)

    @classmethod
    def should_warn_on_override(cls) -> bool:
        """
        Check if parameter override warnings are enabled.

        Returns:
            True if warnings should be shown
        """
        logging_config = cls.get_logging_config()
        return logging_config.get("warn_on_parameter_override", True)

    # ============================================
    # NEW (V0.7): Migrated from config.py
    # ============================================

    @classmethod
    def get_development_config(cls) -> Dict[str, Any]:
        """
        Get development configuration.

        Returns:
            Development config dict
        """
        config = cls.get_config()
        return config.get("development", {})

    @classmethod
    def get_importer_config(cls) -> Dict[str, Any]:
        """
        Get importer configuration.

        Returns:
            Importer config dict
        """
        config = cls.get_config()
        return config.get("importer", {})

    @classmethod
    def get_dev_mode(cls) -> bool:
        """
        Get dev mode setting.

        Returns:
            True if dev mode is enabled
        """

        # Config file
        dev_config = cls.get_development_config()
        return dev_config.get("dev_mode", False)

    @classmethod
    def get_debug_logging(cls) -> bool:
        """
        Get debug logging setting.

        Returns:
            True if debug logging is enabled
        """

        # Config file
        dev_config = cls.get_development_config()
        return dev_config.get("debug_logging", False)

    @classmethod
    def get_move_processed_files(cls) -> bool:
        """
        Get move processed files setting.

        Returns:
            True if processed files should be moved
        """

        # Config file
        importer_config = cls.get_importer_config()
        return importer_config.get("move_processed_files", True)

    @classmethod
    def get_delete_on_error(cls) -> bool:
        """
        Get delete on error setting.


        Returns:
            True if files should be deleted on error
        """

        # Config file
        importer_config = cls.get_importer_config()
        return importer_config.get("delete_on_error", False)

    @classmethod
    def get_project_root(cls) -> Path:
        """
        Get project root path.

        Returns:
            Path to project root
        """
        if cls._project_root is None:
            cls()  # Initialize
        return cls._project_root

    @classmethod
    def get_data_raw_path(cls) -> Path:
        """
        Get data/raw path.

        Returns:
            Path to data/raw directory
        """
        paths_config = cls.get_paths_config()
        raw_path = paths_config.get("data_raw", "data/raw")
        return cls.get_project_root() / raw_path

    @classmethod
    def get_data_processed_path(cls) -> Path:
        """
        Get data/processed path.

        Returns:
            Path to data/processed directory
        """
        paths_config = cls.get_paths_config()
        processed_path = paths_config.get("data_processed", "data/processed")
        return cls.get_project_root() / processed_path

    @classmethod
    def get_data_finished_path(cls) -> Path:
        """
        Get data/finished path.

        Returns:
            Path to data/finished directory
        """
        paths_config = cls.get_paths_config()
        finished_path = paths_config.get("data_finished", "data/finished")
        return cls.get_project_root() / finished_path

    @classmethod
    def get_data_parquet_path(cls) -> Path:
        """
        Get data/parquet path.

        Returns:
            Path to data/parquet directory
        """
        paths_config = cls.get_paths_config()
        parquet_path = paths_config.get("data_parquet", "data/parquet")
        return cls.get_project_root() / parquet_path
