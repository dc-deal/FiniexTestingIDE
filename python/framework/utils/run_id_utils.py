"""
FiniexTestingIDE - Run Id
Minting the one identity a run is known by.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

# Length of the random half. Eight hex characters is the form already used for order-guard ids
# (`order_guard.py`), so the project has one shape for "short unique suffix", not two.
_SUFFIX_LEN: int = 8

# Characters of that random half a live session stamps onto its client order ids (#473).
# Four, because Kraken allows 18 ASCII characters for the whole key and a counter has to
# fit beside it — and the requirement is uniqueness across the venue's OPEN orders, not
# across all time.
_SESSION_KEY_LEN: int = 4

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
