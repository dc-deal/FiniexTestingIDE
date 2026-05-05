"""
FiniexTestingIDE - File Logging Configuration Types
Pydantic models for the file_logging section of app_config.json.
"""
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from python.framework.types.log_level import LogLevel


class ScenarioFileLoggingConfig(BaseModel):
    """Scenario-level file logging config. None fields inherit from global."""
    enabled: Optional[bool] = None
    log_level: Optional[LogLevel] = None
    log_root_path: Path
    file_name_prefix: str


class FileLoggingConfig(BaseModel):
    """
    File logging configuration with global/scenario separation.

    Handles:
    - Global log: Single file with append mode
    - Scenario logs: Per-run directories with overwrite
    """
    enabled: bool
    log_level: LogLevel
    log_path: Path
    append_mode: bool
    scenario: ScenarioFileLoggingConfig

    # ============================================
    # Public Properties - Global File Logging
    # ============================================

    @property
    def global_enabled(self) -> bool:
        """Global file logging enabled"""
        return self.enabled

    @property
    def global_log_level(self) -> LogLevel:
        """Global log level (validated)"""
        return self.log_level

    @property
    def global_log_path(self) -> Path:
        """Path to global.log file"""
        return self.log_path

    @property
    def global_append_mode(self) -> bool:
        """Append to global.log (vs overwrite)"""
        return self.append_mode

    # ============================================
    # Public Properties - Scenario File Logging (with inheritance)
    # ============================================

    @property
    def scenario_enabled(self) -> bool:
        """Scenario file logging enabled (after inheritance)"""
        return self.scenario.enabled if self.scenario.enabled is not None else self.enabled

    @property
    def scenario_log_level(self) -> LogLevel:
        """Scenario log level (after inheritance)"""
        return self.scenario.log_level if self.scenario.log_level is not None else self.log_level

    @property
    def scenario_log_root_path(self) -> Path:
        """Root directory for scenario logs"""
        return self.scenario.log_root_path

    @property
    def scenario_file_name_prefix(self) -> str:
        """Name prefix for scenario logs (default "scenario" -> scenario_01_USDJPY_blks_02.log)"""
        return self.scenario.file_name_prefix

    # ============================================
    # Utility Methods
    # ============================================

    def is_file_logging_enabled(self) -> bool:
        """
        Check if any file logging is enabled.

        Returns:
            True if global OR scenario file logging active
        """
        return self.enabled or self.scenario_enabled

    def __repr__(self) -> str:
        """Debug representation"""
        return (
            f"FileLoggingConfig("
            f"global={self.enabled}/{self.log_level} @ {self.log_path}, "
            f"scenario={self.scenario_enabled}/{self.scenario_log_level} @ {self.scenario.log_root_path})"
        )
