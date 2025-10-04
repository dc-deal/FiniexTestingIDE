"""
FiniexTestingIDE - Monitoring Module
Live performance monitoring and TUI support

NEW (V0.7): Prepares for Issue #1 (TUI Dashboard)
"""

from .tui_adapter import TUIAdapter, TUIMetricsFormatter

__all__ = [
    'TUIAdapter',
    'TUIMetricsFormatter'
]
