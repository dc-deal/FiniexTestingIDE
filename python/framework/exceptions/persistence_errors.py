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


class CarryOverIdentityCollisionError(FiniexError):
    """
    Two AutoTrader profiles resolve to one carry-over identity.

    The carry-over stores file one document per bot, keyed by the profile's declared name and
    its symbol — both free text. Two profiles agreeing on that pair share a position book, a
    position-counter high-water mark and a set of session keys, and neither notices: each store's
    own identity check asks whether a document belongs to THIS bot, which it does for both.

    Raised at startup rather than survived, because the state is genuinely ambiguous from that
    moment on and a live session has no second chance (§35).
    """
    pass
