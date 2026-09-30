"""
FiniexTestingIDE - Release Gate Session

Which pytest session is a RELEASE GATE — the one kind of session that writes an operator record.

The daily suite never writes into the operator's stores (§34): every store is redirected to a
throwaway directory for the session. A release gate is different by construction — it is
excluded from the daily runner, the operator starts it, and its certificate is COMMITTED. The
patch that certificate names (#551) has to resolve in the operator's `run_patches/`, not in a
temporary directory pytest deletes three sessions later.
"""

from typing import Iterable

# The marks `tests/conftest.py` gives the release-gate suites, by directory.
RELEASE_GATE_MARKS = ('live_adapter', 'benchmark', 'live_field_study', 'live_signal_feed')


def is_release_gate_session(items: Iterable) -> bool:
    """
    Whether EVERY collected test belongs to a release gate.

    All, never any: a session mixing a release gate with daily suites is a daily session, and
    one daily test writing into the operator's store is the leak the redirect exists to stop.

    Args:
        items: The session's collected test items

    Returns:
        True only for a non-empty session made of release-gate tests alone
    """
    collected = list(items)
    return bool(collected) and all(
        any(item.get_closest_marker(mark) for mark in RELEASE_GATE_MARKS)
        for item in collected)
