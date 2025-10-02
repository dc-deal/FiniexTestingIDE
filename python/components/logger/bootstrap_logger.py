"""
FiniexTestingIDE - Zentrales Logger Setup
Einmal aufrufen, überall verfügbar
"""

from python.components.logger.visual_console_logger import VisualConsoleLogger

# Globale Logger-Instanz
_logger_instance = None


def setup_logging(name: str = "FiniexTestingIDE") -> VisualConsoleLogger:
    """
    Initialisiert VisualConsoleLogger für das gesamte Projekt
    Muss einmal am Start jedes Entry-Points aufgerufen werden

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
    Gibt die globale Logger-Instanz zurück

    Returns:
        VisualConsoleLogger Instanz oder None falls nicht initialisiert
    """
    return _logger_instance
