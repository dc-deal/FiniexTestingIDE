"""
FiniexTestingIDE - Log Level Definitions
Case-insensitive log level validation and filtering

Usage:
    from python.framework.types.log_level import LogLevel
    
    # Validate
    level = LogLevel.validate("debug")  # Returns "DEBUG"
    
    # Check if should log
    if LogLevel.should_log("DEBUG", "INFO"):
        print("Message won't be logged")
"""


class LogLevel:
    """
    Log level definitions and validation.
    Thread-safe, case-insensitive validation.
    """
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    # Priority for filtering (higher = more important)
    _PRIORITY = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40
    }

    @classmethod
    def validate(cls, level: str) -> str:
        """
        Validate and normalize log level (case insensitive).

        Args:
            level: Log level string (any case, e.g., "debug", "INFO", "Warning")

        Returns:
            Normalized uppercase log level (e.g., "DEBUG", "INFO", "WARNING")

        Raises:
            ValueError: If invalid log level
        """
        level_upper = level.upper()
        if level_upper not in cls._PRIORITY:
            valid_levels = ", ".join(cls._PRIORITY.keys())
            raise ValueError(
                f"Invalid log level: '{level}'. Must be one of: {valid_levels}"
            )
        return level_upper

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
        msg_priority = cls._PRIORITY.get(message_level.upper(), 0)
        cfg_priority = cls._PRIORITY.get(configured_level.upper(), 10)
        return msg_priority >= cfg_priority

    @classmethod
    def get_priority(cls, level: str) -> int:
        """
        Get numeric priority for a log level.

        Args:
            level: Log level string

        Returns:
            Numeric priority (10-40)
        """
        return cls._PRIORITY.get(level.upper(), 0)
