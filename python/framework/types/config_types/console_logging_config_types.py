"""
FiniexTestingIDE - Console Logging Configuration Types
Pydantic models for the console_logging section of app_config.json.
"""
from typing import Optional
from pydantic import BaseModel
from python.framework.types.log_level import LogLevel


class ScenarioLoggingConfig(BaseModel):
    """Scenario-level console logging overrides. None fields inherit from global."""
    enabled: Optional[bool] = None
    log_level: Optional[LogLevel] = None
    write_system_info: bool


class SummaryLoggingConfig(BaseModel):
    """Batch summary display settings."""
    show_global_log: bool = True
    detail: bool = False
    scenario_detail_threshold: int = 9


class ConsoleLoggingConfig(BaseModel):
    """
    Console logging configuration with inheritance support.

    Handles console logging and scenario-specific overrides.
    None values in scenario section inherit from global.
    """
    enabled: bool
    log_level: LogLevel
    warn_on_parameter_override: bool
    scenario: ScenarioLoggingConfig
    summary: SummaryLoggingConfig

    # ============================================
    # Public Properties - Console Logging
    # ============================================

    @property
    def console_enabled(self) -> bool:
        """Console logging enabled"""
        return self.enabled

    @property
    def global_log_level(self) -> LogLevel:
        """Console log level (validated)"""
        return self.log_level

    # ============================================
    # Public Properties - Scenario Logging (with inheritance)
    # ============================================

    @property
    def scenario_enabled(self) -> bool:
        """Scenario logging enabled (after inheritance)"""
        return self.scenario.enabled if self.scenario.enabled is not None else self.enabled

    @property
    def scenario_log_level(self) -> LogLevel:
        """Scenario log level (after inheritance)"""
        return self.scenario.log_level if self.scenario.log_level is not None else self.log_level

    @property
    def scenario_write_system_info(self) -> bool:
        """Write system info for scenarios"""
        return self.scenario.write_system_info

    # ============================================
    # Public Properties - Summary
    # ============================================

    @property
    def show_global_log(self) -> bool:
        """Show global log section in console output after batch run"""
        return self.summary.show_global_log

    @property
    def summary_detail(self) -> bool:
        """Show per-scenario detail blocks in console summary"""
        return self.summary.detail

    @property
    def scenario_detail_threshold(self) -> int:
        """Max scenarios to show as grid; above this switches to compact list"""
        return self.summary.scenario_detail_threshold

    # ============================================
    # Utility Methods
    # ============================================

    def should_log_scenarios(self) -> bool:
        """
        Check if scenario logs should be displayed.

        Returns:
            True if scenarios should log to console
        """
        return self.enabled and self.scenario_enabled

    def __repr__(self) -> str:
        """Debug representation"""
        return (
            f"LoggingConfig("
            f"console={self.enabled}/{self.log_level}, "
            f"scenario={self.scenario_enabled}/{self.scenario_log_level}, "
            f"system_info={self.scenario.write_system_info})"
        )
