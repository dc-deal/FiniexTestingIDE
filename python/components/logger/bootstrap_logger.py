"""
FiniexTestingIDE - Zentrales Logger Setup
Einmal aufrufen, überall verfügbar

USAGE:
- Entry Points (strategy_runner.py): setup_logging(name="FiniexTestingIDE")
- All other files: get_logger()
"""

from python.components.logger.visual_console_logger import VisualConsoleLogger

# Globale Logger-Instanz
_logger_instance = None


def setup_logging(name: str = "FiniexTestingIDE") -> VisualConsoleLogger:
    """
    Initialisiert VisualConsoleLogger für das gesamte Projekt.
    Muss einmal am Start jedes Entry-Points aufgerufen werden.

    Args:
        name: Logger-Name (default: FiniexTestingIDE)

    Returns:
        VisualConsoleLogger Instanz
    """
    global _logger_instance

    if _logger_instance is None:
        _logger_instance = VisualConsoleLogger(name=name)

    return _logger_instance


def get_logger() -> VisualConsoleLogger:
    """
    Gibt die globale Logger-Instanz zurück.
    Sollte in allen Files außer Entry Points verwendet werden.

    Returns:
        VisualConsoleLogger Instanz

    Raises:
        RuntimeError: Falls setup_logging() noch nicht aufgerufen wurde
    """
    if _logger_instance is None:
        raise RuntimeError(
            "Logger not initialized! Call setup_logging() in your entry point first."
        )
    return _logger_instance
