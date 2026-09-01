"""
FiniexTestingIDE - Run Id
Minting the one identity a run is known by.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

# Length of the random half. Eight hex characters is the form already used for order-guard ids
# (`order_guard.py`), so the project has one shape for "short unique suffix", not two.
_SUFFIX_LEN: int = 8

# Characters of that random half a live session stamps onto its client order ids (#473).
# Four, because Kraken allows 18 ASCII characters for the whole key and a counter has to
# fit beside it — and the requirement is uniqueness across the venue's OPEN orders, not
# across all time.
_SESSION_KEY_LEN: int = 4

# Leading character of a client order id, so the key is recognizable as ours at a glance
# in the venue's own order list — and so a parse can refuse a string that is not one.
_CL_ORD_ID_PREFIX: str = 'p'

# The timestamp half. Fixed width and zero-padded on purpose: `ReportStore.list_runs` sorts run ids
# lexicographically descending and the sweep ranking tie-breaks on them, so "newest first" and
# deterministic ordering both rest on this prefix keeping byte order equal to time order.
_STAMP_FORMAT: str = '%Y%m%d_%H%M%S'


def mint_run_id(start_time: datetime, owner_dir: Optional[Path] = None) -> str:
    """
    The id a run is known by, everywhere: directory name, artifact key, ledger column, API route.

    Two halves, each earning its place. The timestamp keeps the id READABLE and SORTABLE — an
    operator reads it in a log line, and every consumer that orders runs relies on it. The random
    suffix makes it DISTINCT: a second-resolution stamp collided in ordinary use (measured: 4 of
    188 runs, two of them across categories), and a collision does not merely duplicate a row —
    the artifact resolver returns the first match, so the API serves a DIFFERENT run's data than
    the index lists under that id.

    Minted ONCE per run and passed down. Deriving it independently in each logger would give the
    three loggers of one session three different ids, and therefore three directories.

    Args:
        start_time: The run's start (UTC, tz-aware) — the readable half comes from it
        owner_dir: The directory the run will live in; when given, an id whose directory already
            exists is re-minted. The check belongs HERE and not in the logger: the loggers of one
            run join a directory the minter claimed, so only the minter can tell a claim from a
            join. Without it, `exist_ok=True` would let two runs share one directory silently —
            which is the failure this id format exists to prevent

    Returns:
        e.g. '20260830_132034_a3f9c2d1'
    """
    while True:
        run_id = f'{start_time.strftime(_STAMP_FORMAT)}_{uuid4().hex[:_SUFFIX_LEN]}'
        if owner_dir is None or not (owner_dir / run_id).exists():
            return run_id


def session_key_from_run_id(run_id: str) -> str:
    """
    The short discriminator a live session stamps onto every client order id it sends.

    Reuses the run id's random half rather than minting a second identifier, so an
    unfamiliar order in the venue's own UI can be traced back to a run directory by eye:
    `p1641_47` at the broker, `20260831_110757_164176c0` in `runs/`.

    Four characters is deliberate. Kraken allows 18 ASCII characters for a client order
    id, and what has to fit alongside is a counter — the requirement is uniqueness across
    the venue's OPEN orders, not across all time, so 65 536 sessions is generous. The
    readable internal id (`pos_btcusd_47`) never goes on the wire; it stays in our books.

    Args:
        run_id: A run id as minted by mint_run_id

    Returns:
        The first four characters of the random half, or '' when the id has no such half
    """
    parts = run_id.rsplit('_', 1)
    return parts[-1][:_SESSION_KEY_LEN] if len(parts) == 2 else ''


def build_client_order_id(session_key: str, order_id: str) -> Optional[str]:
    """
    The key a live session sends to the venue for one internal order id.

    Two jobs. It survives a restart without colliding: the counter inside `order_id`
    restarts at 1 with the process, so an order still resting at the venue from the
    previous session would otherwise be matched by a brand-new one. And it is short —
    Kraken allows 18 ASCII characters, which the readable internal id does not fit
    alongside a discriminator, so the readable form stays in our own books.

    Args:
        session_key: This session's discriminator, from session_key_from_run_id
        order_id: Internal order id, e.g. 'pos_btcusd_47'

    Returns:
        The wire key, e.g. 'p1641_47', or None when no session key is configured
    """
    if not session_key:
        return None
    counter = order_id.rsplit('_', 1)[-1]
    return f'{_CL_ORD_ID_PREFIX}{session_key}_{counter}'


def parse_client_order_id(client_order_id: Optional[str]) -> Optional[Tuple[str, str]]:
    """
    Split a wire key back into the session that sent it and the order's counter.

    The reader half of build_client_order_id, and the reason the shape lives here rather
    than at the sending site: on a truth pull the venue echoes the key back, and the
    session half is what tells THIS session's order apart from one an earlier session of
    the same bot left resting (#355).

    Deliberately strict — the discriminator must have the exact minted width and the
    counter must be digits. A client order id is free-format at the venue, so anything
    looser would claim another client's key as ours, which is the one mistake that turns
    a foreign order into an adoption candidate.

    Args:
        client_order_id: The key the venue echoed back, e.g. 'p1641_47'

    Returns:
        (session_key, counter), or None when the string is not one of our keys
    """
    if not client_order_id or not client_order_id.startswith(_CL_ORD_ID_PREFIX):
        return None
    session_key, _, counter = client_order_id[len(_CL_ORD_ID_PREFIX):].partition('_')
    if len(session_key) != _SESSION_KEY_LEN or not counter.isdigit():
        return None
    return session_key, counter
