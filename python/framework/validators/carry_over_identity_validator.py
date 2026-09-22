"""
FiniexTestingIDE - Carry-Over Identity Collision Check

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

from python.framework.exceptions.persistence_errors import CarryOverIdentityCollisionError
from python.framework.persistence.carry_over_identity import carry_over_key

# The directory every AutoTrader profile lives under, whatever purpose folder it sits in
# (#31: production / observation / field_study / backtesting). Found by walking UP from the
# profile in hand rather than configured, because the profile path is what a session is
# started with and a second declaration of the same root could disagree with it.
PROFILES_ROOT_NAME = 'autotrader_profiles'

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
    root = _profiles_root(config_path)
    if root is None:
        return

    key = carry_over_key(profile_name, symbol, bot_id)
    claimants = [path for path, claimed in _live_identities(root).items() if claimed == key]
    if len(claimants) < 2:
        return

    listed = '\n'.join(f'    {path}' for path in sorted(str(p) for p in claimants))
    raise CarryOverIdentityCollisionError(
        f"Two profiles share one carry-over identity '{key}':\n{listed}\n"
        f'    They would share one position book, one position counter and one set of session '
        f'keys.\n'
        f"    Give one of them a distinct `name` — the identity is `<name>_<symbol>`, and the "
        f'name is free text nothing validates.'
    )


def _profiles_root(config_path: Optional[Path]) -> Optional[Path]:
    """
    The profile tree this session's config sits in.

    Args:
        config_path: The loaded profile's path, or None

    Returns:
        The `autotrader_profiles` directory above it, or None when the config did not come from
        one — a fixture in a test tree legitimately does not, and a check that guessed a root
        there would compare this session against profiles it has nothing to do with
    """
    if config_path is None:
        return None
    for parent in Path(config_path).resolve().parents:
        if parent.name == PROFILES_ROOT_NAME:
            return parent
    return None


def _live_identities(root: Path) -> Dict[Path, str]:
    """
    The carry-over identity every profile under this root claims.

    Read from the RAW json rather than through the config loader: this runs at boot, a full load
    validates and resolves far more than a name and a symbol, and one unrelated profile with a
    stale key would then abort a session it has nothing to do with. An unreadable or incomplete
    profile is skipped for the same reason — this check answers one question and must not become
    a second config validator.

    Args:
        root: The `autotrader_profiles` directory

    Returns:
        Profile path → the identity it would file its carry-over under
    """
    identities: Dict[Path, str] = {}
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


def collisions(root: Path) -> Dict[str, List[Path]]:
    """
    Every shared carry-over identity under a profile root, for a sweep rather than a boot.

    The boot check deliberately raises only on the running session's own key; this answers the
    whole question at once, which is what a maintenance command or a test wants.

    Args:
        root: The `autotrader_profiles` directory

    Returns:
        Identity → the profiles claiming it, for identities claimed more than once
    """
    claimed: Dict[str, List[Path]] = {}
    for path, key in _live_identities(root).items():
        claimed.setdefault(key, []).append(path)
    return {key: paths for key, paths in claimed.items() if len(paths) > 1}
