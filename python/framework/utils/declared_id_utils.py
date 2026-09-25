"""
FiniexTestingIDE - Declared Identity Shape

The one shape rule for an identity a person DECLARES and types rather than one the code mints: a
bot's `bot_id` (#538) and an API account's `account_id` (#551). Both are typed by hand, read in a
table, compared by eye, and end up inside a filename or a run-index column — so they obey one
rule, and the rule lives here rather than in either caller.

What each caller says about a MISSING id, and why its shape matters there, stays with the caller:
that is where the operator reading the message is looking.
"""

from typing import FrozenSet, Optional

# What a declared identity may look like. The ceiling is the operator's (2026-09-24) — an id is
# typed, read in a table and compared by eye. The character set is NOT a style choice: a bot id
# becomes half of a filename, and `sanitize_identity_part` rewrites anything else silently, so a
# wider set would let the declared identity and the stored one drift apart. The underscore is
# excluded because it is the reserved join character.
DECLARED_ID_MAX_LENGTH = 10
_DECLARED_ID_ALLOWED: FrozenSet[str] = frozenset('abcdefghijklmnopqrstuvwxyz0123456789-')


def declared_id_malformed_reason(value: str) -> Optional[str]:
    """
    Why a declared identity cannot be used as it stands, or None when it can.

    Args:
        value: The declared identity, never empty — an absent id is each caller's own message

    Returns:
        A phrase completing "which …", or None when the id is well formed
    """
    if len(value) > DECLARED_ID_MAX_LENGTH:
        return f'is {len(value)} characters long — the ceiling is {DECLARED_ID_MAX_LENGTH}'
    bad = sorted({c for c in value if c not in _DECLARED_ID_ALLOWED})
    if bad:
        return 'contains ' + ', '.join(f"'{c}'" for c in bad)
    return None
