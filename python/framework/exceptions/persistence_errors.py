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


class CarriedLevelError(FiniexError):
    """A stored position book still carries a level or a venue-held stop (#625)."""
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


class BotAlreadyRunningError(FiniexError):
    """
    A session of this bot is already running on this machine (#598).

    Raised at the very start of a session, before any carry-over is read or anything is sent,
    when the kernel lock beside the bot's carry-over is held by another process. Two sessions of
    one bot would adopt the same orders and overwrite each other's carry-over. The message names
    the holder's run and process and the lock file; the start ends with exit 2, which a
    supervisor does not restart.
    """
    pass


class CarryOverRequiredError(FiniexError):
    """
    A session trading real money found no carry-over it can use, and its start was not declared
    the bot's first (#598).

    Absent, unreadable, in another format or written by another profile: in each case the
    session would trade without knowing what the bot holds at the venue — the usual cause is a
    start in the wrong data home, or a deploy into a code tree that found none. The message
    names what was found, where, and the two ways forward: point FINIEX_DATA_HOME at the home
    the last session wrote, or declare a genuine first start with --first-start.
    """
    pass


class FirstStartContradictedError(FiniexError):
    """
    A start declared the bot's first (--first-start) found a carry-over it can use (#598).

    The declaration would let the session start over a history it should continue; refusing
    it means a flag left behind in a service unit cannot switch the rule off.
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
    moment on and an AutoTrader session has no second chance (§35).
    """
    pass


class CarryOverContinuationError(FiniexError):
    """
    A declared continuation of another profile's carry-over that the boot cannot honour.

    `carry_over_from` says a later stage of one bot follows an earlier one and starts from what
    that one left (#358). The declaration is the statement that a carry-over is EXPECTED, so a
    session that cannot read it is not a first run but a contradiction — and starting fresh
    instead would run the later stage against a state it was never meant to see. Raised when
    the pair is not one (a third claimant, a name that matches nothing, a chain, a mock stage
    beside a live one), when the predecessor left nothing to read, when the run that wrote it
    has not completed, or when this session would not read a carry-over at all.

    Raised at startup, before any order: an AutoTrader session has one start, and nothing to
    exclude while the rest run on.
    """
    pass


class CarryOverForeignClaimsError(FiniexError):
    """
    This bot's carry-over was written by another profile and still speaks for the venue.

    The document is filed under `<bot_id>_<symbol>`, and its writer is recorded by profile name.
    A document whose writer is neither this profile nor its declared partner is ignored on read —
    and the session's first write would REPLACE it. Where it still holds an open position or a
    session key an order resting at the venue may carry, that write is how a position ends up at
    the venue with nothing left that knows about it. The usual cause is a profile renamed while
    its `bot_id` stayed.

    Raised at startup rather than survived: only the operator can say which profile the
    document belongs to.
    """
    pass
