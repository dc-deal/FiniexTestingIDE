"""
FiniexTestingIDE - Carry-Over Identity Collision Check

Two live profiles must not resolve to one carry-over document.

A bot's persistent state — the open position book, the position-counter high-water mark, the
session keys its orders were sent under — is filed under `<profile name>_<symbol>`, and both
halves are free text nothing validates. Two profiles declaring the same name for the same symbol
therefore share one document, one counter and one book, and **neither store can see it**: each
checks whether a document belongs to THIS bot (`cold_start_state_store.py:136-141`), which in a
collision it does, for both of them.

Measured 2026-09-21: of 28 profiles, 9 can write a carry-over and they claim 9 distinct
identities — latent rather than live. The plausible route into it is the one the profile layout
now invites: copy a production profile into `observation/` and keep its name. Note that the
purpose folder decides nothing here — `backtesting/dry_run_resting_probe.json` declares
`adapter_type: live` and is counted, which is why the adapter is read rather than the path.

Mock profiles are excluded, and not for convenience: both carry-over stores are constructed only
behind `isinstance(executor, LiveTradeExecutor)`, so a mock session writes no document and cannot
take part in a collision. Blocking a real session over one would be a false alarm on the money
path, which is the fastest way to teach somebody to ignore a check.
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

# A profile declaring this adapter runs no live executor, so neither carry-over store is built
# for it (`cold_start_setup.py:109`, `autotrader_main.py:586`; the switch itself is
# `autotrader_startup.py:477`, `config.adapter_type == 'live'`).
#
# Tested NEGATIVELY on purpose — everything that is not mock counts. A positive `== 'live'` test
# would silently exempt any adapter added later (#209's MT5 is the next one), and an exemption
# in a guard is the failure the guard exists to prevent. Mock is the one case proven not to
# write a carry-over, so mock is the one case excluded.
MOCK_ADAPTER = 'mock'


def validate_carry_over_identity_unique(
    config_path: Optional[Path],
    profile_name: str,
    symbol: str,
    adapter_type: str,
) -> None:
    """
    Refuse to start when another live profile claims this session's carry-over identity.

    Only a collision involving THIS session is raised. A collision between two other profiles is
    real but is not this run's problem, and it will be raised the moment either of them starts —
    at which point the message is about the session the operator is actually looking at.

    A MOCK session is skipped entirely, and the reason is the mirror of why mock profiles are
    excluded from the comparison: it writes no carry-over, so nothing can be taken from it. Left
    in, it would abort on two OTHER profiles colliding at a key that happens to equal its own —
    a test run stopped by a problem it cannot have, which is how a check earns the reputation of
    being in the way.

    Args:
        config_path: Where this session's profile was loaded from; None skips the check, because
            a config assembled in memory has no directory to compare against
        profile_name: The profile's declared name, or its symbol when it declares none — exactly
            what the stores are handed
        symbol: The traded symbol
        adapter_type: This session's own adapter, so a mock run is not stopped by a collision it
            cannot take part in

    Returns:
        None — raises CarryOverIdentityCollisionError when the identity is not unique
    """
    if adapter_type == MOCK_ADAPTER:
        return
    root = _profiles_root(config_path)
    if root is None:
        return

    key = carry_over_key(profile_name, symbol)
    claimants = [path for path, claimed in _live_identities(root).items() if claimed == key]
    if len(claimants) < 2:
        return

    listed = '\n'.join(f'    {path}' for path in sorted(str(p) for p in claimants))
    raise CarryOverIdentityCollisionError(
        f"Two live profiles share one carry-over identity '{key}':\n{listed}\n"
        f'    They would share one position book, one position counter and one set of session '
        f'keys.\n'
        f"    Give one of them a distinct `name` — the identity is `<name>_<symbol>`, and both "
        f'halves are free text.'
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
    The carry-over identity every live profile under this root claims.

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
        if raw.get('adapter_type') == MOCK_ADAPTER:
            continue
        identities[path] = carry_over_key(raw.get('name') or symbol, symbol)
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
