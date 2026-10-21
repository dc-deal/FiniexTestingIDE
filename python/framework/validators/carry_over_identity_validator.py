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
check got it backwards. `adapter_type: mock` selects the broker adapter, not the executor — every
AutoTrader session runs a `LiveTradeExecutor` (the factory only demands `is_live_capable()`,
which the mock adapter is), so every session builds both carry-over stores. Measured 2026-09-21:
**15 of the 16 documents in `data/runtime/cold_start_state/` belong to mock profiles.** An
exclusion would have skipped almost the entire population it exists to protect.

ONE pairing is allowed, and only when declared: two stages of one bot that run in order, the later
naming the earlier in `carry_over_from` (#358) — the restart stages of the security study, which
must start from what the stage before them left. `continuation_pair_refusal` holds the pair to
every condition that keeps it from being mistaken for a copy, and the boot check and the sweep
both ask it. What a session may do with the document once it is read is the next check's
question, `carry_over_continuity_validator`.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from python.configuration.data_home import WORKSPACE_DIR
from python.framework.config_directory.config_directory_discovery import json_files_under
from python.framework.exceptions.persistence_errors import (
    BotIdMalformedError,
    BotIdRequiredError,
    CarryOverContinuationError,
    CarryOverIdentityCollisionError,
)
from python.framework.persistence.carry_over_identity import (
    carry_over_key,
    sanitize_identity_part,
)
from python.framework.types.persistence_types import CarryOverClaim
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.utils.declared_id_utils import (
    DECLARED_ID_MAX_LENGTH,
    declared_id_malformed_reason,
)

# The directory every AutoTrader profile lives under, whatever purpose folder it sits in
# (#31: production / observation / field_study / backtesting). Found by walking UP from the
# profile in hand rather than configured, because the profile path is what a session is
# started with and a second declaration of the same root could disagree with it.
PROFILES_ROOT_NAME = 'autotrader_profiles'

# The project's config cascade, most specific first — the same pair §29 names for credentials.
# A profile tree exists under each, and a copy travels between them.
_WORKSPACE_CONFIG_DIR = WORKSPACE_DIR
_TRACKED_CONFIG_DIR = 'configs'

# What a declared identity may look like — the shape a bot id shares with an API account id,
# held in ONE place (`declared_id_utils`) so the two cannot drift apart. The name stays here
# because the messages below are about a bot, and a bot's ceiling IS the shared one.
BOT_ID_MAX_LENGTH = DECLARED_ID_MAX_LENGTH

# The purposes a declared continuation is allowed in (#358, operator 2026-10-14): a release
# study's stages and a test's fixtures. A production bot that keeps its identity across a rename
# is a different question, with its own answer.
CONTINUATION_PURPOSES = frozenset({RunPurpose.CERTIFICATE, RunPurpose.FIXTURE})

# The adapter a profile runs on when it names none — the loader's own default, applied here so
# that a profile which omits the key and one which states it agree.
_DEFAULT_ADAPTER_TYPE = 'mock'

# The session-end value that leaves orders resting for the next session to adopt.
_ORDERS_LEFT_RESTING = 'leave'


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
            f'    Add it to the profile, beside `profile_name`:\n'
            f'\n'
            f'        "bot_id": "{suggestion}"\n'
            f'\n'
            f'    Up to {BOT_ID_MAX_LENGTH} characters of a-z, 0-9 and hyphen. What it has to be '
            f'is UNIQUE\n'
            f'    across every profile and never changed again. The identity this session would '
            f"file\n    under is '{carry_over_key(profile_name, symbol, suggestion)}'."
        )

    reason = declared_id_malformed_reason(bot_id)
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


def validate_carry_over_identity_unique(
    config_path: Optional[Path],
    profile_name: str,
    symbol: str,
    bot_id: str = '',
    profile_homes: Sequence[Path] = (),
    carry_over_from: str = '',
) -> None:
    """
    Refuse to start when another profile claims this session's carry-over identity.

    Only a collision involving THIS session is raised. A collision between two other profiles is
    real but is not this run's problem, and it will be raised the moment either of them starts —
    at which point the message is about the session the operator is actually looking at.

    The one exception to "one identity, one profile" is a DECLARED continuation (#358): exactly
    two profiles, the later naming the earlier in `carry_over_from` — `continuation_pair_refusal`
    decides, for this check and for the sweep alike.

    Args:
        config_path: Where this session's profile was loaded from; None skips the check, because
            a config assembled in memory has no directory to compare against
        profile_name: The profile's declared name, or its symbol when it declares none — exactly
            what the stores are handed
        symbol: The traded symbol
        bot_id: The identity this profile DECLARES, which takes precedence over the name (#538).
            Empty means it declares none and the key is composed, as before
        profile_homes: Every configured root a profile may live under (`profile_homes`) — a
            session whose profile lies in one is compared against all of them. Empty compares
            only the tree the profile sits in and its sibling, which is what a test tree needs
        carry_over_from: The profile this session declares it continues, or empty

    Returns:
        None — raises CarryOverIdentityCollisionError when the identity is not unique, and
        CarryOverContinuationError when a declared continuation is not a pair
    """
    roots = _profiles_roots(config_path, profile_homes)
    if not roots:
        if carry_over_from:
            # A declaration nothing can check is not one to honour: the pair rule is what keeps
            # a later stage from reading a document it was never meant to see.
            raise CarryOverContinuationError(
                f"The profile '{profile_name}' declares `carry_over_from: '{carry_over_from}'`, "
                f'but it lies in no profile tree,\n'
                f'    so the profile it names cannot be found and checked. Start it from its '
                f'place under an\n    `{PROFILES_ROOT_NAME}` folder or a configured profile '
                f'home.')
        return

    key = carry_over_key(profile_name, symbol, bot_id)
    claimants = [claim for claim in _claims(roots) if claim.key == key]
    if len(claimants) < 2:
        if carry_over_from:
            raise CarryOverContinuationError(
                f"The profile '{profile_name}' declares `carry_over_from: '{carry_over_from}'`, "
                f'but no other profile\n'
                f"    claims its carry-over identity '{key}'. The profile it continues has to "
                f'declare the same\n'
                f'    `bot_id` and symbol, and be called exactly that.')
        return

    declared = [claim for claim in claimants if claim.carry_over_from]
    if declared:
        refusal = continuation_pair_refusal(claimants)
        if refusal is None:
            return
        listed = '\n'.join(f'    {claim.path}' for claim in sorted(claimants, key=_by_path))
        raise CarryOverContinuationError(
            f"The profiles claiming the carry-over identity '{key}' are not a continuation:\n"
            f'{listed}\n    {refusal}')

    listed = '\n'.join(f'    {claim.path}' for claim in sorted(claimants, key=_by_path))
    raise CarryOverIdentityCollisionError(
        f"Two profiles share one carry-over identity '{key}':\n{listed}\n"
        f'    They would share one position book, one position counter and one set of session '
        f'keys.\n'
        f'    Give one of them a distinct `bot_id` — the identity is `<bot_id>_<symbol>`, up to '
        f'{BOT_ID_MAX_LENGTH}\n'
        f'    characters of a-z, 0-9 and hyphen. The usual cause is a COPIED profile whose id was '
        f'not changed\n'
        f'    with it; the two need not sit in the same directory, and since 2026-09-24 this check '
        f'crosses\n    that boundary.\n'
        f'    If one is a later stage of the other — a release study run in order — the later '
        f'one declares\n'
        f'    `carry_over_from` with the earlier one\'s `profile_name` instead.'
    )


def continuation_pair_refusal(claimants: List[CarryOverClaim]) -> Optional[str]:
    """
    Why the profiles claiming one identity are not a declared continuation — None when they are.

    A continuation lets a later stage of one bot start from what the earlier stage left (#358),
    and the two stores honour it on read in both directions (`accepts_carry_over`). That is safe
    only for a pair that cannot be mistaken for anything else, so every condition below closes a
    route by which a session would read a document it was never meant to see:

      - exactly two profiles: a third would read the pair's state as its own
      - the later names the earlier byte for byte — the stores compare names, nothing looser
      - the earlier declares nothing itself: a chain or a cycle makes both sides accept each
        other's documents, and "the earlier stage starts fresh" no longer holds
      - two different names: the stores tell the two apart by name alone
      - a release study's stages or a test's fixtures, nothing else
      - one adapter, one broker, one cold-start directory: a mock stage beside a live one
        would hand a real account a risk baseline taken from simulated balances
      - the earlier stage leaves no order resting: the next boot to see it would be the later
        stage's, which was not the one that agreed to adopt it

    Args:
        claimants: Every profile claiming one carry-over identity

    Returns:
        A one-line reason naming the condition that fails, or None for a declared pair
    """
    if len(claimants) != 2:
        return (f'a continuation is exactly two profiles, the later naming the earlier; '
                f'{len(claimants)} claim this identity')
    later = [claim for claim in claimants if claim.carry_over_from]
    if len(later) != 1:
        return ('both profiles declare `carry_over_from` — the earlier stage of a continuation '
                'declares none')
    successor = later[0]
    predecessor = next(claim for claim in claimants if claim is not successor)
    if successor.profile_name == predecessor.profile_name:
        return (f"both profiles are called '{successor.profile_name}' — the stores tell the two "
                f'stages apart by name, so each needs its own `profile_name`')
    if successor.carry_over_from != predecessor.profile_name:
        return (f"{successor.path.name} continues '{successor.carry_over_from}', but the other "
                f"profile is called '{predecessor.profile_name}' — the name must match exactly")
    for claim in (successor, predecessor):
        if claim.run_purpose not in CONTINUATION_PURPOSES:
            stated = claim.run_purpose.value if claim.run_purpose else 'an unknown purpose'
            return (f'{claim.path.name} runs as {stated} — a continuation is allowed only '
                    f'between release-study stages (`certificate`) or test fixtures (`fixture`)')
    for field, label in (('adapter_type', '`adapter_type`'), ('broker_type', '`broker_type`'),
                         ('cold_start_path', '`cold_start.path`')):
        if getattr(successor, field) != getattr(predecessor, field):
            return (f'the two declare a different {label} '
                    f'({getattr(predecessor, field)!r} and {getattr(successor, field)!r}) — '
                    f'both stages have to run on the same venue and read the same document')
    if predecessor.session_end_orders == _ORDERS_LEFT_RESTING:
        return (f"{predecessor.path.name} leaves its orders resting at session end "
                f"(`session_end.orders: '{_ORDERS_LEFT_RESTING}'`) — the stage that would meet "
                f'them is the later one, which never agreed to adopt them')
    return None


def _by_path(claim: CarryOverClaim) -> str:
    """
    Sort key that lists claimants in a stable order.

    Args:
        claim: One claimant

    Returns:
        Its path as text
    """
    return str(claim.path)


def _profiles_roots(config_path: Optional[Path], profile_homes: Sequence[Path]) -> List[Path]:
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

    And every configured profile home besides (#581). A profile in a user algo directory has no
    `autotrader_profiles` folder above it, so the walk up found nothing and the check was
    skipped — for the operator's own bots, the very copies it exists for.

    Args:
        config_path: The loaded profile's path, or None
        profile_homes: The configured roots a profile may live under; may be empty

    Returns:
        The roots to scan, nearest first; empty when the config lies in no profile tree and in no
        configured home — a fixture in a test tree legitimately does not, and a check that guessed
        a root there would compare this session against profiles it has nothing to do with
    """
    if config_path is None:
        return []
    resolved = Path(config_path).resolve()
    homes = [Path(home).resolve() for home in profile_homes]
    own: Optional[Path] = None
    for parent in resolved.parents:
        if parent.name == PROFILES_ROOT_NAME:
            own = parent
            break
    in_a_home = any(resolved.is_relative_to(home) for home in homes)
    if own is None and not in_a_home:
        return []

    roots: List[Path] = []
    if own is not None:
        roots.append(own)
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
    # The configured homes join only for a profile that lies in one of them — for the same
    # reason: a temporary tree's copy of a shipped profile would otherwise collide with its own
    # original.
    if in_a_home:
        roots += [home for home in homes if home.is_dir() and home not in roots]
    return roots


def _claims(roots: List[Path]) -> List[CarryOverClaim]:
    """
    The carry-over claim every profile under these roots makes.

    Read from the RAW json rather than through the config loader: this runs at boot, a full load
    validates and resolves far more than a name and a symbol, and one unrelated profile with a
    stale key would then abort a session it has nothing to do with. An unreadable or incomplete
    profile is skipped for the same reason — this check answers one question and must not become
    a second config validator.

    Args:
        roots: The `autotrader_profiles` directories to scan

    Returns:
        One claim per readable profile that names a symbol
    """
    claims: List[CarryOverClaim] = []
    for root in roots:
        # The directory's own walk, which never enters a `.git`: a user algo directory is
        # usually a repository, and walking its objects cost ~1 s on this tree (2026-10-09).
        for path in json_files_under(root):
            raw = _read_profile(path)
            if raw is None:
                continue
            symbol = raw.get('symbol')
            if not symbol:
                continue
            name = raw.get('profile_name') or symbol
            cold_start = raw.get('cold_start') if isinstance(raw.get('cold_start'), dict) else {}
            session_end = (raw.get('session_end')
                           if isinstance(raw.get('session_end'), dict) else {})
            claims.append(CarryOverClaim(
                path=path,
                key=carry_over_key(name, symbol, raw.get('bot_id') or ''),
                profile_name=name,
                carry_over_from=_text(raw.get('carry_over_from')),
                run_purpose=_purpose(raw.get('run_purpose')),
                adapter_type=_text(raw.get('adapter_type')) or _DEFAULT_ADAPTER_TYPE,
                broker_type=_text(raw.get('broker_type')),
                cold_start_path=cold_start.get('path'),
                session_end_orders=session_end.get('orders'),
            ))
    return claims


def _purpose(value: object) -> Optional[RunPurpose]:
    """
    A raw profile's purpose, read the way the loader reads it.

    Args:
        value: The raw `run_purpose` value

    Returns:
        REGULAR when absent, the purpose it names, or None when it names none
    """
    if value is None:
        return RunPurpose.REGULAR
    try:
        return RunPurpose(value)
    except ValueError:
        return None


def _text(value: object) -> str:
    """
    A raw profile value as text, tolerating whatever the file holds.

    Args:
        value: The raw value

    Returns:
        The value when it is a string, else empty
    """
    return value if isinstance(value, str) else ''


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
    whole question at once, which is what a maintenance command or a test wants. A declared
    continuation pair is not a collision — the same rule as the boot check decides it.

    Args:
        roots: The `autotrader_profiles` directories to scan

    Returns:
        Identity → the profiles claiming it, for identities claimed more than once that are not
        a declared continuation
    """
    claimed: Dict[str, List[CarryOverClaim]] = {}
    for claim in _claims(roots):
        claimed.setdefault(claim.key, []).append(claim)
    return {key: [claim.path for claim in group] for key, group in claimed.items()
            if len(group) > 1 and continuation_pair_refusal(group) is not None}
