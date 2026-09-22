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

**The separator is RESERVED, and that is what makes the composition injective.** Until 2026-09-22
both halves were sanitised to `[a-z0-9_]` and joined with `_`, so the underscore occurred inside
the halves as well as between them and two DIFFERENT bots could resolve to one document:

    'btc'      + 'USD_SPOT'  ->  btc_usd_spot
    'btc_usd'  + 'SPOT'      ->  btc_usd_spot      the same file, two unrelated bots

Bot B then opened bot A's document and read a position book it never wrote. The halves now
sanitise to `[a-z0-9-]`, so the `_` appears exactly once and the two are `btc_usd-spot` and
`btc-usd_spot`. A startup validator still refuses a collision; what changed is that there is no
longer a collision to refuse in this shape.

**The structural answer is `bot_id`, which a profile DECLARES.** Composing an identity from what
a profile is CALLED means the identity moves when the name does — and a display name is exactly
the thing an operator improves: renaming `dot_live` to `dotusd_live_v2` would point a restarted
bot at a new, empty document while the venue still held its position. A declared id says "this is
the same bot" out loud and survives every rename of everything else. It is OPTIONAL: a profile
without one keeps the composed key, so nothing that exists changes.

What remains, for a profile that declares none: two SPELLINGS of one name — `dot-live`,
`dot live` and `dot_live` all reduce to `dot-live` — and two profiles using the same name for the
same symbol. Both resolve to ONE document, and nothing in this module can see it.
`carry_over_identity_validator` is the check for that, and it lives apart because this module has
to stay a pure function.
"""


def sanitize_identity_part(name: str) -> str:
    """
    Reduce an identity component to a safe filename token that cannot contain the separator.

    Non-alphanumerics become a HYPHEN, never an underscore: the underscore is reserved as the
    join character, and a half that could contain it would make the composition ambiguous — which
    is exactly how two unrelated bots came to share one carry-over document.

    Args:
        name: Raw profile or symbol string

    Returns:
        Lowercased token over `[a-z0-9-]`, free of the separator
    """
    return ''.join(c if c.isalnum() else '-' for c in name).strip('-').lower()


# NOT `utils/file_utils.sanitize_filename`, and the two must not be merged. That one replaces the
# characters an operating system forbids and keeps case; this one reduces every non-alphanumeric
# to a hyphen and lowercases, so `dot-live` and `dot live` meet here and stay apart there.
# The difference is not a cleanup waiting to happen: this function's OUTPUT is a filename that
# already exists on disk for every live bot, so changing the rule orphans every carry-over
# document at once — the position book among them. It was changed exactly once, on 2026-09-22,
# to reserve the separator, and that change shipped WITH the migration that renames the
# documents (`python/experiments/migrate_carry_over_keys/`). Any further change needs the same.


def carry_over_key(profile: str, symbol: str, bot_id: str = '') -> str:
    """
    The identity both carry-over stores file a bot's document under.

    Returned WITHOUT an extension: it is the identity, and the stores add their own suffix. A
    caller that only wants to compare two bots — the startup collision check — needs the
    identity and not a filename.

    **`bot_id` is the DECLARED identity and takes precedence.** Without it the key is composed
    from what the profile happens to be CALLED, and a display name is something an operator
    improves: renaming `dot_live` to `dotusd_live_v2` silently points the bot at a new, empty
    document while the venue still holds its position. A declared id is the operator saying "this
    is the same bot" out loud, and it survives every rename of everything else. It is optional so
    that no existing profile changes key by this function gaining an argument.

    Args:
        profile: The AutoTrader profile's name, or its symbol when it declares none
        symbol: The traded symbol
        bot_id: The identity the profile DECLARES, or empty to compose one from the name

    Returns:
        `<declared or sanitized name>_<sanitized symbol>` — and the underscore occurs exactly
        once, because neither half can contain one
    """
    head = sanitize_identity_part(bot_id) if bot_id else sanitize_identity_part(profile)
    return f'{head}_{sanitize_identity_part(symbol)}'
