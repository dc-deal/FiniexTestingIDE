

from typing import Any, Dict


class ConfigurationError(Exception):
    """Base class for all configuration errors"""

    def get_context(self) -> Dict[str, Any]:
        """Get error context for logger"""
        return {}


class ScenarioSetConfigurationError(ConfigurationError):
    """
    Raised when not enough ticks available for tick-limited mode.

    Used in: Modus A (max_ticks mode)
    """

    def __init__(
        self,
        file_name: str,
        reason: str,
        sceanrio_set_configuration: Any = None
    ):
        self.sceanrio_set_configuration = sceanrio_set_configuration
        message = (
            f"Scenario Set Configuration Error - File '{file_name}'!\n"
            f"   Reason is: {reason}"
        )

        super().__init__(message)

    def get_context(self) -> Dict[str, Any]:
        return {
            'sceanrio_set_configuration': self.sceanrio_set_configuration
        }
