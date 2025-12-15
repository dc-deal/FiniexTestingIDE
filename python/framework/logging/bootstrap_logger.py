
from python.framework.logging.global_logger import GlobalLogger
from typing import Optional

"""
FiniexTestingIDE - Bootstrap Logger (Factory)
Provides singleton GlobalLogger instance

This module now acts as a factory for the GlobalLogger.
For other modules, nothing changes - they still use get_global_logger().

Usage:
    from python.framework.logging.bootstrap_logger import get_global_logger
    logger = get_global_logger()
    logger.info("Application started")
"""


# Singleton instance
_logger_instance: Optional[GlobalLogger] = None


def get_global_logger(name: str = "FiniexTestingIDE") -> GlobalLogger:
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
