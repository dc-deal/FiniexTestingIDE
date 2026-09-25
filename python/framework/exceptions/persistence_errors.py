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


class BotIdRequiredError(FiniexError):
    """
    An AutoTrader profile does not declare the identity its state is filed under.

    Required of EVERY profile since 2026-09-24, not only of a continuous deployment. The
    narrower rule protected the case that needs it least: a continuous profile is the one
    somebody thought about, while the route into a collision is copying a profile into another
    purpose folder and keeping its name — and that copy was exempt.

    Without a declared `bot_id` the state is filed under what the profile is CALLED: the open
    position book, the position-counter high-water mark, the session keys its orders were sent
    under. A display name is the thing an operator improves. The rename does not fail; the next
    session
    simply looks somewhere else, finds nothing, and reads its own holding as flat while the venue
    still holds it. At spot that is unrecoverable, because a holding is a balance the venue cannot
    describe as a position.

    Raised at startup rather than warned about, because a warning on a thirty-day unattended run
    is a warning nobody is there to read, and the cost of being wrong is a position nobody knows
    about (§35: the live side aborts at boot — it has one session).
    """
    pass


class BotIdMalformedError(FiniexError):
    """
    A declared `bot_id` is not a shape the carry-over key can carry unchanged.

    The id BECOMES half of a filename (`<bot_id>_<symbol>.json`), and `sanitize_identity_part`
    would silently rewrite anything outside `[a-z0-9-]` — so `Bot_01` in the profile would be
    `bot-01` on disk. A declared identity that differs from the stored one is the same class of
    confusion the id exists to prevent, one level down, so it is refused rather than fixed up.
    The underscore is excluded because it is the RESERVED join character.

    The length ceiling is the operator's (2026-09-24): an identity is typed, read in a table and
    compared by eye, and ten characters is where that stays possible.

    Raised at startup for the same reason as BotIdRequiredError — the live side aborts at boot.
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
