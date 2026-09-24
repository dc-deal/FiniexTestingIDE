"""
FiniexTestingIDE - Carry-Over Identity Checks

Two things a bot's carry-over identity has to be before a session may start: DECLARED where it
matters, and UNIQUE always.

Two profiles must not resolve to one carry-over document.

A bot's persistent state — the open position book, the position-counter high-water mark, the
session keys its orders were sent under — is filed under `<profile name>_<symbol>`. Since #538
the separator is RESERVED, so two DIFFERENT bots can no longer collide by accident of where the
underscores fall; what remains is that both halves are free text nothing validates, and two
profiles declaring the same name for the same symbol
therefore share one document, one counter and one book, and **neither store can see it**: each
checks whether a document belongs to THIS bot (`cold_start_state_store.py:136-141`), which in a
collision it does, for both of them.

Measured 2026-09-21: 28 profiles claim 28 distinct identities — latent rather than live. The
plausible route into it is the one the profile layout now invites: copy a profile into another
purpose folder and keep its name.

NOTHING is excluded, and that correction is worth recording because the first version of this
check got it backwards. `adapter_type: mock` selects the tick SOURCE, not the executor — every
AutoTrader session runs a `LiveTradeExecutor` (the factory only demands `is_live_capable()`,
which the mock adapter is), so every session builds both carry-over stores. Measured 2026-09-21:
**15 of the 16 documents in `data/runtime/cold_start_state/` belong to mock profiles.** An
exclusion would have skipped almost the entire population it exists to protect.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from python.framework.exceptions.persistence_errors import (
    BotIdMalformedError,
    BotIdRequiredError,
    CarryOverIdentityCollisionError,
)
from python.framework.persistence.carry_over_identity import (
    carry_over_key,
    sanitize_identity_part,
)

# The directory every AutoTrader profile lives under, whatever purpose folder it sits in
# (#31: production / observation / field_study / backtesting). Found by walking UP from the
# profile in hand rather than configured, because the profile path is what a session is
# started with and a second declaration of the same root could disagree with it.
PROFILES_ROOT_NAME = 'autotrader_profiles'

# The project's config cascade, most specific first — the same pair §29 names for credentials.
# A profile tree exists under each, and a copy travels between them.
_WORKSPACE_CONFIG_DIR = 'user_configs'
_TRACKED_CONFIG_DIR = 'configs'

# What a declared identity may look like. The ceiling is the operator's (2026-09-24) — an id is
# typed, read in a table and compared by eye. The character set is NOT a style choice: the id
# becomes half of a filename, and `sanitize_identity_part` rewrites anything else silently, so
# a wider set would let the declared identity and the stored one drift apart. The underscore is
# excluded because it is the reserved join character.
BOT_ID_MAX_LENGTH = 10
_BOT_ID_ALLOWED = set('abcdefghijklmnopqrstuvwxyz0123456789-')


def validate_bot_id(profile_name: str, symbol: str, bot_id: str) -> None:
    """
    Refuse to start any profile whose carry-over identity is missing or malformed.

    Required of EVERY profile since 2026-09-24 (operator). The older rule asked only of a
    CONTINUOUS deployment, which protected the case that needs it least — a continuous profile
    is one somebody thought about, while the route into a collision is copying a profile into
    another purpose folder and keeping its name, and that copy was exempt. Everything else here
    was already true of the narrow rule; only the population changed.

    The message carries a SUGGESTION rather than only a complaint, because the value is
    arbitrary and the operator has no reason to invent one — what matters is that it is unique
    and never changes again.

    Args:
        profile_name: The profile's declared name, or its symbol when it declares none
        symbol: The traded symbol
        bot_id: The identity the profile declares, or empty

    Returns:
        None — raises BotIdRequiredError when none is declared, BotIdMalformedError when the
        declared one is not a shape the carry-over key can carry unchanged
    """
    if not bot_id:
        suggestion = sanitize_identity_part(profile_name)[:BOT_ID_MAX_LENGTH].strip('-')
        raise BotIdRequiredError(
            f"The profile '{profile_name}' declares no `bot_id`.\n"
            f'    A bot\'s state is filed under this identity — the open position book, the '
            f'position\n'
            f'    counter, the session keys. Without one it is filed under the profile NAME, so '
            f'renaming\n'
            f'    the profile points the next session at an empty document while the venue still '
            f'holds\n'
            f'    the position.\n'
            f'\n'
            f'    Add it to the profile, beside `name`:\n'
            f'\n'
            f'        "bot_id": "{suggestion}"\n'
            f'\n'
            f'    Up to {BOT_ID_MAX_LENGTH} characters of a-z, 0-9 and hyphen. What it has to be '
            f'is UNIQUE\n'
            f'    across every profile and never changed again. The identity this session would '
            f"file\n    under is '{carry_over_key(profile_name, symbol, suggestion)}'."
        )

    reason = _malformed_reason(bot_id)
    if reason is None:
        return
    raise BotIdMalformedError(
        f"The profile '{profile_name}' declares `bot_id: '{bot_id}'`, which {reason}.\n"
        f'    The id becomes half of a filename ("<bot_id>_<symbol>.json"), and anything outside\n'
        f'    a-z, 0-9 and hyphen would be rewritten on the way to disk — the profile would then\n'
        f'    declare one identity and the store would hold another. The underscore is the '
        f'reserved\n'
        f'    join character and is excluded for the same reason.\n'
        f'\n'
        f'    Allowed: 1 to {BOT_ID_MAX_LENGTH} characters of a-z, 0-9 and hyphen.'
    )


def _malformed_reason(bot_id: str) -> Optional[str]:
    """
    Why a declared identity cannot be used as it stands, or None when it can.

    Args:
        bot_id: The declared identity, never empty

    Returns:
        A phrase completing "which …", or None when the id is well formed
    """
    if len(bot_id) > BOT_ID_MAX_LENGTH:
        return f'is {len(bot_id)} characters long — the ceiling is {BOT_ID_MAX_LENGTH}'
    bad = sorted({c for c in bot_id if c not in _BOT_ID_ALLOWED})
    if bad:
        return 'contains ' + ', '.join(f"'{c}'" for c in bad)
    return None


def validate_carry_over_identity_unique(
    config_path: Optional[Path],
    profile_name: str,
    symbol: str,
    bot_id: str = '',
) -> None:
    """
    Refuse to start when another profile claims this session's carry-over identity.

    Only a collision involving THIS session is raised. A collision between two other profiles is
    real but is not this run's problem, and it will be raised the moment either of them starts —
    at which point the message is about the session the operator is actually looking at.

    Args:
        config_path: Where this session's profile was loaded from; None skips the check, because
            a config assembled in memory has no directory to compare against
        profile_name: The profile's declared name, or its symbol when it declares none — exactly
            what the stores are handed
        symbol: The traded symbol
        bot_id: The identity this profile DECLARES, which takes precedence over the name (#538).
            Empty means it declares none and the key is composed, as before

    Returns:
        None — raises CarryOverIdentityCollisionError when the identity is not unique
    """
    roots = _profiles_roots(config_path)
    if not roots:
        return

    key = carry_over_key(profile_name, symbol, bot_id)
    claimants = [path for path, claimed in _live_identities(roots).items() if claimed == key]
    if len(claimants) < 2:
        return

    listed = '\n'.join(f'    {path}' for path in sorted(str(p) for p in claimants))
    raise CarryOverIdentityCollisionError(
        f"Two profiles share one carry-over identity '{key}':\n{listed}\n"
        f'    They would share one position book, one position counter and one set of session '
        f'keys.\n'
        f'    Give one of them a distinct `bot_id` — the identity is `<bot_id>_<symbol>`, up to '
        f'{BOT_ID_MAX_LENGTH}\n'
        f'    characters of a-z, 0-9 and hyphen. The usual cause is a COPIED profile whose id was '
        f'not changed\n'
        f'    with it; the two need not sit in the same directory, and since 2026-09-24 this check '
        f'crosses\n    that boundary.'
    )


def _profiles_roots(config_path: Optional[Path]) -> List[Path]:
    """
    Every profile tree this session must be compared against.

    The tree the config sits in, PLUS its sibling in the other config directory. Both, since
    2026-09-24, and the reason is the route an operator actually takes: copying a profile and
    forgetting to change its `bot_id`. A private copy of a shipped profile lands in
    `user_configs/`, which is across the boundary the old single-root walk never crossed — so the
    one check that could catch the copy was blind to exactly the copy that matters.

    They are separate BOTS, not a cascade. An AutoTrader profile does not merge with a same-named
    file the way `app_config.json` does (`deep_merge` puts app defaults UNDER one profile and
    nothing else), so two files claiming one identity are always two bots sharing one position
    book — whichever directories they sit in.

    Args:
        config_path: The loaded profile's path, or None

    Returns:
        The roots to scan, nearest first; empty when the config did not come from a profile tree
        at all — a fixture in a test tree legitimately does not, and a check that guessed a root
        there would compare this session against profiles it has nothing to do with
    """
    if config_path is None:
        return []
    own: Optional[Path] = None
    for parent in Path(config_path).resolve().parents:
        if parent.name == PROFILES_ROOT_NAME:
            own = parent
            break
    if own is None:
        return []

    roots = [own]
    # The pair is the project's config cascade, named here the way §29 names the credential
    # one: most specific first. The sibling is found by swapping the directory the root sits
    # in, never by guessing a path from the working directory — a session started from a
    # temporary tree must not suddenly be compared against the repository's profiles.
    container = own.parent
    for a, b in ((_WORKSPACE_CONFIG_DIR, _TRACKED_CONFIG_DIR),
                 (_TRACKED_CONFIG_DIR, _WORKSPACE_CONFIG_DIR)):
        if container.name == a:
            sibling = container.parent / b / PROFILES_ROOT_NAME
            if sibling.is_dir():
                roots.append(sibling)
    return roots


def _live_identities(roots: List[Path]) -> Dict[Path, str]:
    """
    The carry-over identity every profile under these roots claims.

    Read from the RAW json rather than through the config loader: this runs at boot, a full load
    validates and resolves far more than a name and a symbol, and one unrelated profile with a
    stale key would then abort a session it has nothing to do with. An unreadable or incomplete
    profile is skipped for the same reason — this check answers one question and must not become
    a second config validator.

    Args:
        roots: The `autotrader_profiles` directories to scan

    Returns:
        Profile path → the identity it would file its carry-over under
    """
    identities: Dict[Path, str] = {}
    for root in roots:
        for path in sorted(root.rglob('*.json')):
            raw = _read_profile(path)
            if raw is None:
                continue
            symbol = raw.get('symbol')
            if not symbol:
                continue
            identities[path] = carry_over_key(
                raw.get('name') or symbol, symbol, raw.get('bot_id') or '')
    return identities


def _read_profile(path: Path) -> Optional[dict]:
    """
    Parse one profile, tolerating anything this check is not responsible for.

    Args:
        path: The profile file

    Returns:
        The parsed object, or None when it cannot be read as one
    """
    try:
        with open(path, encoding='utf-8') as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def collisions(roots: List[Path]) -> Dict[str, List[Path]]:
    """
    Every shared carry-over identity under a profile root, for a sweep rather than a boot.

    The boot check deliberately raises only on the running session's own key; this answers the
    whole question at once, which is what a maintenance command or a test wants.

    Args:
        roots: The `autotrader_profiles` directories to scan

    Returns:
        Identity → the profiles claiming it, for identities claimed more than once
    """
    claimed: Dict[str, List[Path]] = {}
    for path, key in _live_identities(roots).items():
        claimed.setdefault(key, []).append(path)
    return {key: paths for key, paths in claimed.items() if len(paths) > 1}
