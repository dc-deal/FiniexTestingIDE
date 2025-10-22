
from python.components.logger.global_logger import GlobalLogger
from typing import Optional

"""
FiniexTestingIDE - Bootstrap Logger (Factory)
Provides singleton GlobalLogger instance

This module now acts as a factory for the GlobalLogger.
For other modules, nothing changes - they still use get_logger().

Usage:
    from python.components.logger.bootstrap_logger import get_logger
    logger = get_logger()
    logger.info("Application started")
"""


# Singleton instance
_logger_instance: Optional[GlobalLogger] = None


def get_logger(name: str = "FiniexTestingIDE") -> GlobalLogger:
    """
    Get or create GlobalLogger singleton.

    Args:
        name: Logger name (default: "FiniexTestingIDE")

    Returns:
        GlobalLogger singleton instance
    """
    global _logger_instance

    if _logger_instance is None:
        _logger_instance = GlobalLogger(name=name)

    return _logger_instance


def reset_logger():
    """
    Reset logger singleton (primarily for testing).

    Forces creation of new logger instance on next get_logger() call.
    """
    global _logger_instance

    if _logger_instance is not None:
        _logger_instance.close()
        _logger_instance = None
