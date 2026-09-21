"""
Carry-over identity — the one place a bot's persistent state is addressed from (#354 / #355).

Both carry-over stores keep one document per BOT, named after it. Which bot is a composite of
two free-text strings, and before this module each store composed it for itself — the same
sanitiser and the same `<profile>_<symbol>` pattern, written twice (§19), with any third caller
inviting a third copy.

**The key must be STABLE, which is why it is not a hash.** A fingerprint over the profile —
`profile_hash` (#497) is exactly that — CHANGES when the profile changes, and that is its job: it
exists to say the operation moved. Using one here would point a restarted bot at a new, empty
document the moment somebody raised a stop level, and the position book it inherited would be
gone from its own view while the venue still held it. The key answers "which bot am I", not
"what does it currently look like".

The consequence is a property nothing enforces: two profiles declaring the same `name` for the
same symbol resolve to ONE document. `carry_over_identity_validator` is the check for that, and
it lives apart because this module has to stay a pure function.
"""


def sanitize_identity_part(name: str) -> str:
    """
    Reduce an identity component to a safe filename token.

    Args:
        name: Raw profile or symbol string

    Returns:
        Lowercased token with non-alphanumerics collapsed to underscores
    """
    return ''.join(c if c.isalnum() else '_' for c in name).strip('_').lower()


# NOT `utils/file_utils.sanitize_filename`, and the two must not be merged. That one replaces the
# characters an operating system forbids and keeps case and dashes; this one collapses every
# non-alphanumeric and lowercases, so `dot-live` and `dot live` meet here and stay apart there.
# The difference is not a cleanup waiting to happen: this function's OUTPUT is a filename that
# already exists on disk for every live bot, so changing the rule orphans every carry-over
# document at once — the position book among them.


def carry_over_key(profile: str, symbol: str) -> str:
    """
    The identity both carry-over stores file a bot's document under.

    Returned WITHOUT an extension: it is the identity, and the stores add their own suffix. A
    caller that only wants to compare two bots — the startup collision check — needs the
    identity and not a filename.

    Args:
        profile: The AutoTrader profile's name, or its symbol when it declares none
        symbol: The traded symbol

    Returns:
        `<sanitized profile>_<sanitized symbol>`
    """
    return f'{sanitize_identity_part(profile)}_{sanitize_identity_part(symbol)}'
