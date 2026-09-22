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


class ContinuousDeploymentNeedsBotIdError(FiniexError):
    """
    A profile declares a continuous deployment but not the identity its state is filed under.

    A continuous deployment is precisely the case where state has to survive a restart: the open
    position book, the position-counter high-water mark, the session keys its orders were sent
    under. Without a declared `bot_id` that state is filed under what the profile is CALLED — and
    a display name is the thing an operator improves. The rename does not fail; the next session
    simply looks somewhere else, finds nothing, and reads its own holding as flat while the venue
    still holds it. At spot that is unrecoverable, because a holding is a balance the venue cannot
    describe as a position.

    Raised at startup rather than warned about, because a warning on a thirty-day unattended run
    is a warning nobody is there to read, and the cost of being wrong is a position nobody knows
    about (§35: the live side aborts at boot — it has one session).
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
