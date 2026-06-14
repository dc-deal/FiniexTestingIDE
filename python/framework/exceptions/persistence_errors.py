"""
FiniexTestingIDE - Persistence Errors
Exception types for the algo state persistence layer (#354).
"""

from python.framework.exceptions.finiex_error import FiniexError


class StatePersistenceError(FiniexError):
    """
    Algo state could not be persisted or restored.

    Raised for a non-serializable snapshot (the pre-flight check or a save
    encounters a value json cannot encode), for a corrupt state file under the
    'fail' policy, or for a stale state file under the 'halt' policy.
    """
    pass
