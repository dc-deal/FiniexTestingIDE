"""
FiniexTestingIDE - Runtime Environment Utilities

Detects the execution environment (debugger attached, debug/serial mode) so that the
execution coordinator (fork vs serial decision) and the reports (timing-reliability
notice) share one source of truth instead of each re-deriving it.
"""

import os
import sys


def is_debugger_attached() -> bool:
    """
    Whether a debugger (VS Code debugpy / pydevd) is attached to this process.

    Returns:
        True if a trace function or a known debugger module is active
    """
    return (
        (hasattr(sys, 'gettrace') and sys.gettrace() is not None)
        or 'debugpy' in sys.modules
        or 'pydevd' in sys.modules
    )


def is_debug_execution() -> bool:
    """
    Whether the batch runs in debug / serial mode.

    True when a debugger is attached or DEBUG_MODE is set. In this mode the batch
    executes serially (single process) and per-tick timings carry debugger/trace
    overhead, so they are NOT representative of production performance.

    Returns:
        True if execution is in debug/serial mode
    """
    return is_debugger_attached() or bool(os.getenv('DEBUG_MODE'))
