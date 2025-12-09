"""
FiniexTestingIDE - Logging Configuration
Handles logging config with inheritance logic

Features:
- Inheritance: null values inherit from parent
- Validation: Ensures all required fields present
- Type safety: Proper typing for all config values
"""

from python.configuration.config_file_loader import ConfigFileLoader
from python.framework.types.log_level import LogLevel


class ConsoleLoggingConfig:
    """
    Logging configuration with inheritance support.

    Handles console logging and scenario-specific overrides.
    Supports null inheritance pattern for enabled and log_level.
    """

    def __init__(self):
        """
        Initialize logging config with inheritance logic.

        Args:
            config_dict: Raw logging config from app_config.json

        Raises:
            ValueError: If required fields missing or invalid
        """
        config, was_first_load = ConfigFileLoader.get_config()
        self._config = config.get(
            'console_logging', None)
        if self._config is None:
            raise ValueError(
                "Console Logging Config not found (<<JSON root>>/console_logging). Recommendation: see default app configuration to fix structure.")

        # Base console logging (required fields)
        if 'enabled' not in self._config:
            raise ValueError(
                "logging.enabled is required (must be true/false)")
        if 'log_level' not in self._config:
            raise ValueError("logging.log_level is required")

        self._console_enabled = self._config['enabled']
        # Validate global log level - will raise ValueError if invalid
        self._global_log_level = LogLevel.validate(
            self._config['log_level']
        )

        # Warn on parameter override (required)
        if 'warn_on_parameter_override' not in self._config:
            raise ValueError(
                "logging.warn_on_parameter_override is required (must be true/false)")
        self._warn_on_parameter_override = self._config['warn_on_parameter_override']

        # Scenario logging config (with inheritance)
        scenario_config = self._config.get('scenario', {})
        self._validate_scenario_config(scenario_config)

        # Scenario enabled (inherit if null)
        self._scenario_enabled = scenario_config.get('enabled')
        if self._scenario_enabled is None:
            self._scenario_enabled = self._console_enabled
        else:
            if not isinstance(self._scenario_enabled, bool):
                raise ValueError(
                    f"logging.scenario.enabled must be true/false/null, got: {type(self._scenario_enabled).__name__}"
                )

        # Scenario log level (inherit if null, validate if string)
        self._scenario_log_level = scenario_config.get('log_level')
        if self._scenario_log_level is None:
            # Inherit from parent
            self._scenario_log_level = self._global_log_level
        else:
            # Validate the provided log level - will raise ValueError if invalid
            self._scenario_log_level = LogLevel.validate(
                self._scenario_log_level)

        # Scenario write_system_info (required)
        if 'write_system_info' not in scenario_config:
            raise ValueError(
                "logging.scenario.write_system_info is required (must be true/false)")
        self._scenario_write_system_info = scenario_config['write_system_info']

    def _validate_scenario_config(self, scenario_config: dict):
        """
        Validate scenario config structure.

        Args:
            scenario_config: Scenario config dict

        Raises:
            ValueError: If required fields missing
        """
        if not scenario_config:
            raise ValueError(
                "logging.scenario config block is required. "
                "Must contain: enabled, log_level, write_system_info"
            )

        required_fields = ['enabled', 'log_level', 'write_system_info']
        for field in required_fields:
            if field not in scenario_config:
                raise ValueError(f"logging.scenario.{field} is required")

    # ============================================
    # Public Properties - Console Logging
    # ============================================

    @property
    def console_enabled(self) -> bool:
        """Console logging enabled"""
        return self._console_enabled

    @property
    def global_log_level(self) -> LogLevel:
        """Console log level (validated)"""
        return self._global_log_level

    @property
    def warn_on_parameter_override(self) -> bool:
        """Warn when scenario overrides parameters"""
        return self._warn_on_parameter_override

    # ============================================
    # Public Properties - Scenario Logging
    # ============================================

    @property
    def scenario_enabled(self) -> bool:
        """Scenario logging enabled (after inheritance)"""
        return self._scenario_enabled

    @property
    def scenario_log_level(self) -> str:
        """Scenario log level (after inheritance)"""
        return self._scenario_log_level

    @property
    def scenario_write_system_info(self) -> bool:
        """Write system info for scenarios"""
        return self._scenario_write_system_info

    # ============================================
    # Utility Methods
    # ============================================

    def should_log_scenarios(self) -> bool:
        """
        Check if scenario logs should be displayed.

        This replaces the old show_scenario_logging flag.

        Returns:
            True if scenarios should log to console
        """
        return self._console_enabled and self._scenario_enabled

    def __repr__(self) -> str:
        """Debug representation"""
        return (
            f"LoggingConfig("
            f"console={self._console_enabled}/{self._global_log_level}, "
            f"scenario={self._scenario_enabled}/{self._scenario_log_level}, "
            f"system_info={self._scenario_write_system_info})"
        )
