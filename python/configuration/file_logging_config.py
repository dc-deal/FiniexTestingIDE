"""
FiniexTestingIDE - File Logging Configuration
Handles file logging config with global vs scenario separation

Features:
- Separate paths for global.log vs scenario logs
- Append mode for global.log (continuous across runs)
- Inheritance logic for enabled and log_level
"""

from pathlib import Path
from python.configuration.config_file_loader import ConfigFileLoader
from python.framework.types.log_level import LogLevel


class FileLoggingConfig:
    """
    File logging configuration with global/scenario separation.

    Handles:
    - Global log: Single file with append mode
    - Scenario logs: Per-run directories with overwrite
    """

    def __init__(self):
        """
        Initialize file logging config.

        Args:
            config_dict: Raw file_logging config from app_config.json

        Raises:
            ValueError: If required fields missing or invalid
        """
        config, was_first_load = ConfigFileLoader.get_config()
        self._config = config.get(
            'file_logging', None)
        if self._config is None:
            raise ValueError(
                "File Logging Config not found (<<JSON root>>/file_logging). Recommendation: see default app configuration to fix structure.")

        # Global file logging (required fields)
        if 'enabled' not in self._config:
            raise ValueError(
                "file_logging.enabled is required (must be true/false)")
        if 'log_level' not in self._config:
            raise ValueError("file_logging.log_level is required")
        if 'log_path' not in self._config:
            raise ValueError("file_logging.log_path is required")
        if 'append_mode' not in self._config:
            raise ValueError(
                "file_logging.append_mode is required (must be true/false)")

        self._global_enabled = self._config['enabled']
        self._global_log_level = LogLevel.validate(
            self._config['log_level']
        )
        self._global_log_path = Path(self._config['log_path'])
        self._global_append_mode = self._config['append_mode']

        # Scenario file logging config (with inheritance)
        scenario_config = self._config.get('scenario', {})
        self._validate_scenario_config(scenario_config)

        # Scenario enabled (inherit if null)
        self._scenario_enabled = scenario_config.get('enabled')
        if self._scenario_enabled is None:
            self._scenario_enabled = self._global_enabled
        else:
            if not isinstance(self._scenario_enabled, bool):
                raise ValueError(
                    f"file_logging.scenario.enabled must be true/false/null, got: {type(self._scenario_enabled).__name__}"
                )

        # Scenario log level (inherit if null)
        self._scenario_log_level = scenario_config.get('log_level')
        if self._scenario_log_level is None:
            self._scenario_log_level = self._global_log_level

        # Scenario log root path (required)
        if 'log_root_path' not in scenario_config:
            raise ValueError("file_logging.scenario.log_root_path is required")
        self._scenario_log_root_path = Path(scenario_config['log_root_path'])

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
                "file_logging.scenario config block is required. "
                "Must contain: enabled, log_level, log_root_path"
            )

        required_fields = ['enabled', 'log_level', 'log_root_path']
        for field in required_fields:
            if field not in scenario_config:
                raise ValueError(f"file_logging.scenario.{field} is required")

    # ============================================
    # Public Properties - Global File Logging
    # ============================================

    @property
    def global_enabled(self) -> bool:
        """Global file logging enabled"""
        return self._global_enabled

    @property
    def global_log_level(self) -> str:
        """Global log level (validated)"""
        return self._global_log_level

    @property
    def global_log_path(self) -> Path:
        """Path to global.log file"""
        return self._global_log_path

    @property
    def global_append_mode(self) -> bool:
        """Append to global.log (vs overwrite)"""
        return self._global_append_mode

    # ============================================
    # Public Properties - Scenario File Logging
    # ============================================

    @property
    def scenario_enabled(self) -> bool:
        """Scenario file logging enabled (after inheritance)"""
        return self._scenario_enabled

    @property
    def scenario_log_level(self) -> str:
        """Scenario log level (after inheritance)"""
        return self._scenario_log_level

    @property
    def scenario_log_root_path(self) -> Path:
        """Root directory for scenario logs"""
        return self._scenario_log_root_path

    # ============================================
    # Utility Methods
    # ============================================

    def is_file_logging_enabled(self) -> bool:
        """
        Check if any file logging is enabled.

        Returns:
            True if global OR scenario file logging active
        """
        return self._global_enabled or self._scenario_enabled

    def __repr__(self) -> str:
        """Debug representation"""
        return (
            f"FileLoggingConfig("
            f"global={self._global_enabled}/{self._global_log_level} @ {self._global_log_path}, "
            f"scenario={self._scenario_enabled}/{self._scenario_log_level} @ {self._scenario_log_root_path})"
        )
