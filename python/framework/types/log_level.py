"""
FiniexTestingIDE - Log Level Definitions
Case-insensitive log level validation and filtering
"""
from enum import StrEnum


class ColorCodes:
    """ANSI Color Codes for console output"""
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    PURPLE = '\033[95m'  # for VERBOSE
    CYAN = '\033[96m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


# Priority mapping — outside class to avoid str Enum member collision
_LOG_LEVEL_PRIORITY = {
    "VERBOSE": 0,
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40
}


class LogLevel(StrEnum):
    """Log level definitions and validation."""
    VERBOSE = "VERBOSE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @classmethod
    def validate(cls, level: str) -> 'LogLevel':
        """
        Validate and normalize log level (case insensitive).

        Args:
            level: Log level string (any case, e.g., "debug", "INFO", "Warning")

        Returns:
            LogLevel enum member

        Raises:
            ValueError: If invalid log level
        """
        if not isinstance(level, str):
            raise TypeError(
                f"Log level must be a string, but got {type(level).__name__}: {level}"
            )
        try:
            return cls(level.upper())
        except ValueError:
            valid_levels = ', '.join(m.value for m in cls)
            raise ValueError(
                f"Invalid log level: '{level}'. Must be one of: {valid_levels}"
            )

    @classmethod
    def should_log(cls, message_level: str, configured_level: str) -> bool:
        """
        Check if message should be logged based on configured level.

        Messages are logged if their priority >= configured minimum priority.

        Args:
            message_level: Level of the message (DEBUG, INFO, WARNING, ERROR)
            configured_level: Configured minimum level

        Returns:
            True if message should be logged, False otherwise
        """
        msg_priority = _LOG_LEVEL_PRIORITY.get(message_level.upper(), 0)
        cfg_priority = _LOG_LEVEL_PRIORITY.get(configured_level.upper(), 10)
        return msg_priority >= cfg_priority

    @classmethod
    def get_priority(cls, level: str) -> int:
        """
        Get numeric priority for a log level.

        Args:
            level: Log level string

        Returns:
            Numeric priority (0-40)
        """
        return _LOG_LEVEL_PRIORITY.get(level.upper(), 0)
