"""
FiniexTestingIDE - File Logging Configuration Types
Pydantic models for the file_logging section of app_config.json.
"""
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from python.framework.types.log_level import LogLevel


class RunLogPaths(BaseModel):
    """
    Where each kind of run writes its logs — the ONE source for writers and readers alike.

    Three categories, because the three are browsed differently: a live session, a standalone
    simulation run, and one combination of a parameter sweep. The API reads these same paths to
    build its run index and its sweep list, so a path declared here cannot drift from where the
    runs actually land.
    """
    autotrader: Path
    single_runs: Path
    sweeps: Path


class ScenarioFileLoggingConfig(BaseModel):
    """Scenario-level file logging config. None fields inherit from global."""
    enabled: Optional[bool] = None
    log_level: Optional[LogLevel] = None
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
    global_log_dir: Path
    append_mode: bool
    run_logs: RunLogPaths
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
            f'FileLoggingConfig('
            f'global={self.enabled}/{self.log_level} @ {self.global_log_dir}, '
            f'scenario={self.scenario_enabled}/{self.scenario_log_level} '
            f'@ {self.run_logs.single_runs})'
        )
