"""
FiniexTestingIDE - Application Configuration Loader
Centralized app config management
"""

from typing import Dict, Any, List
from python.configuration.console_logging_config import ConsoleLoggingConfig
from python.configuration.file_logging_config import FileLoggingConfig
from python.configuration.config_file_loader import ConfigFileLoader
from python.framework.types.log_level import LogLevel


class AppConfigManager:
    """
    Centralized application configuration loader.

    Loads and provides access to app_config.json settings.
    Singleton pattern ensures config is loaded only once.
    """

    def __init__(self):
        """
        Initialize app config loader.
        """
        # Only load once
        config, was_first_load = ConfigFileLoader.get_config()
        self._config = config
        if was_first_load:
            self._print_config_status()
        self._file_logging_config = FileLoggingConfig()
        self._console_logging_config = ConsoleLoggingConfig()

    def _print_config_status(self):
        """Print config status (replaces old config.py print)"""
        dev_mode = self.get_dev_mode()
        print(
            f"🔧 Config loaded - DEV_MODE: {dev_mode}")

    def get_console_logging_config_object(self) -> ConsoleLoggingConfig:
        """
        Get structured logging configuration.
        """
        return self._console_logging_config

    def get_file_logging_config_object(self) -> FileLoggingConfig:
        """
        Get structured file logging configuration.
            ValueError: If config structure invalid
        """
        return self._file_logging_config

    def get_config(self) -> Dict[str, Any]:
        """
        Get full configuration.

        Returns:
            Complete config dict
        """
        return self._config

    def get_execution_config(self) -> Dict[str, Any]:
        """
        Get execution configuration.

        Returns:
            Execution config dict
        """
        return self._config.get("execution", {})

    def get_scenario_execution_defaults(self) -> Dict[str, Any]:
        """
        Get default execution_config for scenarios from app_config.

        These defaults are used as the base layer in the 3-level cascade:
            app_config → global → scenario

        Only execution_config has this 3-level cascade. strategy_config
        and trade_simulator_config use 2-level cascade (global → scenario).

        Returns:
            Default execution config dict for scenarios
        """
        exec_config = self.get_execution_config()
        return exec_config.get("scenario_execution_defaults", {})

    def get_logging_show_scenario_logging(self) -> bool:
        """
        Check if scenario logging should be shown in console.

        Returns:
            True if scenario logs should be displayed
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.should_log_scenarios()

    def get_logging_write_system_info(self) -> bool:
        """
        Check if system info should be written for scenarios.

        Returns:
            True if system info should be written
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.scenario_write_system_info

    def get_paths_config(self) -> Dict[str, Any]:
        """
        Get paths configuration.

        Returns:
            Paths config dict
        """
        config = self._config
        return config.get("paths", {})

    def get_monitoring_config(self) -> Dict[str, Any]:
        """
        Get monitoring configuration.

        Returns:
            Monitoring config dict
        """
        config = self._config
        return config.get("monitoring", {})

    def get_default_parallel_scenarios(self) -> bool:
        """
        Get default parallel scenarios setting.

        Returns:
            True if scenarios should run in parallel by default
        """
        exec_config = self.get_execution_config()
        return exec_config.get("parallel_scenarios", True)

    def get_default_max_parallel_scenarios(self) -> int:
        """
        Get default max parallel scenarios.

        Returns:
            Max number of scenarios to run in parallel
        """
        exec_config = self.get_execution_config()
        return exec_config.get("max_parallel_scenarios", 4)

    def get_default_parallel_workers(self) -> bool:
        """
        Get default parallel workers setting.

        Returns:
            True if workers should run in parallel by default
        """
        exec_config = self.get_execution_config()
        return exec_config.get("parallel_workers", True)

    def should_warn_on_override(self) -> bool:
        """
        Check if parameter override warnings are enabled.

        Returns:
            True if warnings should be shown

        Raises:
            ValueError: If logging config is invalid (no fallback to defaults)
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.warn_on_parameter_override

    # ============================================
    # Log Level Methods (Validated)
    # ============================================

    def get_console_log_level(self) -> str:
        """
        Get console log level (validated).

        Returns:
            Validated log level string (DEBUG, INFO, WARNING, ERROR)

        Raises:
            ValueError: If log level is invalid (no fallback to defaults)
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.global_log_level

    # ============================================
    # Development Config
    # ============================================

    def get_development_config(self) -> Dict[str, Any]:
        """
        Get development configuration.

        Returns:
            Development config dict
        """
        return self._config.get("development", {})

    def get_dev_mode(self) -> bool:
        """
        Get dev mode setting.

        Returns:
            True if dev mode is enabled
        """
        dev_config = self.get_development_config()
        return dev_config.get("dev_mode", False)

    # ============================================
    # History Config
    # ============================================

    def get_history_config(self) -> Dict[str, Any]:
        """
        Get history retention configuration.

        Returns:
            History config dict with keys:
            - bar_max_history: Max bars per symbol/timeframe (default: 1000)
            - order_history_max: Max order records (default: 10000, 0=unlimited)
            - trade_history_max: Max trade records (default: 5000, 0=unlimited)
        """
        return self._config.get("history", {})

    def get_bar_max_history(self) -> int:
        """Get max bars to retain per symbol/timeframe."""
        return self.get_history_config().get("bar_max_history", 1000)

    def get_order_history_max(self) -> int:
        """Get max order history entries (0=unlimited)."""
        return self.get_history_config().get("order_history_max", 10000)

    def get_trade_history_max(self) -> int:
        """Get max trade history entries (0=unlimited)."""
        return self.get_history_config().get("trade_history_max", 5000)

    def get_data_validation_config(self) -> Dict[str, Any]:
        """
        Get data validation configuration.

        Returns:
            Data validation config dict
        """
        return self._config.get("data_validation", {})

    def get_warmup_quality_mode(self) -> str:
        """
        Get warmup quality mode.

        Returns:
            Warmup quality mode: 'permissive' or 'standard' (default: 'standard')
        """
        validation_config = self.get_data_validation_config()
        return validation_config.get("warmup_quality_mode", "standard")

    def get_allowed_gap_categories(self) -> List[str]:
        """
        Get allowed gap categories for validation.

        Returns:
            List of allowed gap category strings (default: ['seamless', 'short'])
        """
        validation_config = self.get_data_validation_config()
        return validation_config.get("allowed_gap_categories", ["seamless", "short"])

    # ============================================
    # Centralized Path Methods (Validated)
    # ============================================

    def get_data_processed_path(self) -> str:
        """
        Get data processed path from config.

        Returns:
            Path string for processed data directory

        Raises:
            ValueError: If path not configured
        """
        paths = self.get_paths_config()
        path = paths.get("data_processed")
        if not path:
            raise ValueError(
                "Missing required path 'data_processed' in app_config.json. "
                "Add to 'paths' section: \"data_processed\": \"data/processed\""
            )
        return path

    def get_scenario_sets_path(self) -> str:
        """
        Get scenario sets config path from config.

        Returns:
            Path string for scenario sets directory

        Raises:
            ValueError: If path not configured
        """
        paths = self.get_paths_config()
        path = paths.get("scenario_sets")
        if not path:
            raise ValueError(
                "Missing required path 'scenario_sets' in app_config.json. "
                "Add to 'paths' section: \"scenario_sets\": \"configs/scenario_sets\""
            )
        return path

    def get_brokers_path(self) -> str:
        """
        Get brokers config path from config.

        Returns:
            Path string for brokers config directory

        Raises:
            ValueError: If path not configured
        """
        paths = self.get_paths_config()
        path = paths.get("brokers")
        if not path:
            raise ValueError(
                "Missing required path 'brokers' in app_config.json. "
                "Add to 'paths' section: \"brokers\": \"configs/brokers\""
            )
        return path

    def get_generator_template_path(self) -> str:
        """
        Get generator template file path from config.

        Returns:
            Path string for scenario generator template file

        Raises:
            ValueError: If path not configured
        """
        paths = self.get_paths_config()
        path = paths.get("generator_template")
        if not path:
            raise ValueError(
                "Missing required path 'generator_template' in app_config.json. "
                "Add to 'paths' section: \"generator_template\": \"configs/generator/template_scenario_set_header.json\""
            )
        return path

    def get_generator_output_path(self) -> str:
        """
        Get generator output path from config.

        Falls back to scenario_sets path if not explicitly configured.

        Returns:
            Path string for generator output directory
        """
        paths = self.get_paths_config()
        path = paths.get("generator_output")
        if not path:
            # Fallback to scenario_sets path
            return self.get_scenario_sets_path()
        return path
