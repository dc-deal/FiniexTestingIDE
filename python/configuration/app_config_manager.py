"""
FiniexTestingIDE - Application Configuration Loader
Centralized app config management
"""

from typing import Dict, Any, List
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig
from python.configuration.config_file_loader import ConfigFileLoader
from python.framework.types.config_types.app_config_types import AppConfig
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
        raw_config, was_first_load = ConfigFileLoader.get_config()
        self._app_config = AppConfig(**raw_config)
        if was_first_load:
            self._print_config_status()

    def _print_config_status(self):
        """Print config status (replaces old config.py print)"""
        dev_mode = self.get_dev_mode()
        print(
            f"🔧 Config loaded - DEV_MODE: {dev_mode}")

    def get_console_logging_config_object(self) -> ConsoleLoggingConfig:
        """
        Get structured logging configuration.
        """
        return self._app_config.console_logging

    def get_file_logging_config_object(self) -> FileLoggingConfig:
        """
        Get structured file logging configuration.
            ValueError: If config structure invalid
        """
        return self._app_config.file_logging

    def get_config(self) -> Dict[str, Any]:
        """
        Get full configuration.

        Returns:
            Complete config dict
        """
        return self._app_config.model_dump()

    def get_execution_config(self) -> Dict[str, Any]:
        """
        Get execution configuration.

        Returns:
            Execution config dict
        """
        return self._app_config.backtesting.execution.model_dump()

    def get_scenario_execution_defaults(self) -> Dict[str, Any]:
        """
        Get default execution_config for scenarios from app_config.

        These defaults are used as the base layer in the 3-level cascade:
            app_config → global → scenario

        execution_config and trade_simulator_config use 3-level cascade.
        strategy_config and stress_test_config use 2-level cascade (global → scenario).

        Returns:
            Default execution config dict for scenarios
        """
        return self._app_config.backtesting.execution.default_scenario_execution_config.model_dump()

    def get_trade_simulator_defaults(self) -> Dict[str, Any]:
        """
        Get default trade_simulator_config from app_config.

        These defaults are used as the base layer in the 3-level cascade:
            app_config → global → scenario

        Returns:
            Default trade simulator config dict (latency ranges, etc.)
        """
        return self._app_config.backtesting.default_trade_simulator_config.model_dump()

    def get_logging_show_scenario_logging(self) -> bool:
        """
        Check if scenario logging should be shown in console.

        Returns:
            True if scenario logs should be displayed
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.should_log_scenarios()

    def get_summary_detail(self) -> bool:
        """
        Check if per-scenario detail blocks should be shown in console summary.

        Returns:
            True if per-scenario details should be displayed
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.summary_detail

    def get_logging_write_system_info(self) -> bool:
        """
        Check if system info should be written for scenarios.

        Returns:
            True if system info should be written
        """
        logging_config = self.get_console_logging_config_object()
        return logging_config.scenario_write_system_info

    def get_monitoring_config(self) -> Dict[str, Any]:
        """
        Get monitoring configuration.

        Returns:
            Monitoring config dict
        """
        return self._app_config.backtesting.monitoring.model_dump()

    def get_event_tape_size(self) -> int:
        """
        Get event tape ring buffer size for strategy events.

        Returns:
            Maximum number of events to retain in the UI ring buffer
        """
        return self._app_config.backtesting.monitoring.event_tape_size

    def get_default_parallel_scenarios(self) -> bool:
        """
        Get default parallel scenarios setting.

        Returns:
            True if scenarios should run in parallel by default
        """
        return self._app_config.backtesting.execution.parallel_scenarios

    def get_default_max_parallel_scenarios(self) -> int:
        """
        Get default max parallel scenarios.

        Returns:
            Max number of scenarios to run in parallel
        """
        return self._app_config.backtesting.execution.max_parallel_scenarios

    def get_optimization_mount_reuse_enabled(self) -> bool:
        """
        Whether a parameter sweep reuses the prepared data mount across combinations (#419).

        Returns:
            True if mount reuse is enabled (default; False falls back to the cold per-combo path)
        """
        return self._app_config.backtesting.parameter_optimization.mount_reuse_enabled

    def get_optimization_villain_abort_enabled(self) -> bool:
        """
        Whether a sweep aborts when its first executed combination crashes data-level (OOM) (#419).

        Returns:
            True if the fail-fast villain abort is enabled (default)
        """
        return self._app_config.backtesting.parameter_optimization.villain_abort_enabled

    def get_default_parallel_workers(self) -> bool:
        """
        Get default parallel workers setting.

        Returns:
            True if workers should run in parallel by default
        """
        return self._app_config.backtesting.execution.default_scenario_execution_config.parallel_workers

    def get_default_tick_loop_profiling(self) -> bool:
        """
        Get default tick-loop profiling setting (Layer B).

        Returns:
            True if operation-level timers in the tick loop are enabled by default
        """
        return self._app_config.backtesting.execution.default_scenario_execution_config.performance_tracking.tick_loop_profiling

    def get_default_worker_decision_tracking(self) -> bool:
        """
        Get default per-worker / decision tracking setting (Layer A) for Backtesting.

        Returns:
            True if per-component performance trackers are created by default
        """
        return self._app_config.backtesting.execution.default_scenario_execution_config.performance_tracking.worker_decision_tracking

    def get_autotrader_worker_decision_tracking(self) -> bool:
        """
        Get default per-worker / decision tracking setting (Layer A) for AutoTrader.

        AutoTrader has no Layer-B equivalent, so only Layer A is exposed.

        Returns:
            True if per-component performance trackers are created by default
        """
        return self._app_config.autotrader.execution.performance_tracking.worker_decision_tracking

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

    def get_version(self) -> str:
        """
        Get the application version.

        Returns:
            Version string from app_config.json (single source — release
            checklist updates it there; never hardcode versions in code)
        """
        return self._app_config.version

    # ============================================
    # Development Config
    # ============================================

    def get_dev_mode(self) -> bool:
        """
        Get dev mode setting.

        Returns:
            True if dev mode is enabled
        """
        return self._app_config.development.dev_mode

    # ============================================
    # History Config
    # ============================================

    def get_bar_max_history(self) -> int:
        """Get max bars to retain per symbol/timeframe."""
        return self._app_config.history.bar_max_history

    def get_order_history_max(self) -> int:
        """Get max order history entries (0=unlimited)."""
        return self._app_config.history.order_history_max

    def get_trade_history_max(self) -> int:
        """Get max trade history entries (0=unlimited)."""
        return self._app_config.history.trade_history_max

    def get_data_validation_config(self) -> Dict[str, Any]:
        """
        Get data validation configuration.

        Returns:
            Data validation config dict
        """
        return self._app_config.backtesting.data_validation.model_dump()

    def get_warmup_quality_mode(self) -> str:
        """
        Get warmup quality mode.

        Returns:
            Warmup quality mode: 'permissive' or 'standard' (default: 'standard')
        """
        return self._app_config.backtesting.data_validation.warmup_quality_mode

    def get_allowed_gap_categories(self) -> List[str]:
        """
        Get allowed gap categories for validation.

        Returns:
            List of allowed gap category strings (default: ['seamless', 'short'])
        """
        return self._app_config.backtesting.data_validation.allowed_gap_categories

    # ============================================
    # Centralized Path Methods (Validated)
    # ============================================

    def get_user_algo_dirs(self) -> List[str]:
        """
        Get user algo directories scanned for scenario configs.

        Returns:
            List of directory paths (default: ["user_algos/"])
        """
        return self._app_config.paths.user_algo_dirs

    def get_data_processed_path(self) -> str:
        """
        Get data processed path from config.

        Returns:
            Path string for processed data directory

        Raises:
            ValueError: If path not configured
        """
        return self._app_config.paths.data_processed

    def get_run_results_path(self) -> str:
        """
        Get the persistent run-results ledger directory from config.

        Returns:
            Path string for the run-results ledger directory
        """
        return self._app_config.paths.run_results

    def get_scenario_sets_path(self) -> str:
        """
        Get scenario sets config path from config.

        Returns:
            Path string for scenario sets directory

        Raises:
            ValueError: If path not configured
        """
        return self._app_config.backtesting.paths.scenario_sets

    def get_user_scenario_sets_path(self) -> str:
        """
        Get user override scenario sets path (gitignored, personal configs).

        Returns:
            Path string for user scenario sets directory
        """
        return 'user_configs/scenario_sets'

    def get_brokers_path(self) -> str:
        """
        Get brokers config path from config.

        Returns:
            Path string for brokers config directory

        Raises:
            ValueError: If path not configured
        """
        return self._app_config.backtesting.paths.brokers

    def get_generator_template_path(self) -> str:
        """
        Get generator template file path from config.

        Returns:
            Path string for scenario generator template file

        Raises:
            ValueError: If path not configured
        """
        return self._app_config.backtesting.paths.generator_template

    def get_autotrader_defaults(self) -> Dict[str, Any]:
        """
        Get AutoTrader-wide default config from app_config.json.

        Returns:
            autotrader section dict, empty dict if section absent
        """
        return self._app_config.autotrader.model_dump()

    def get_generator_output_path(self) -> str:
        """
        Get generator output path from config.

        Falls back to scenario_sets path if not explicitly configured.

        Returns:
            Path string for generator output directory
        """
        return self._app_config.backtesting.paths.generator_output
