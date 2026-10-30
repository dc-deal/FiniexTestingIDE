"""
The API's CONTRACT version — what a consumer builds against, and what they can assert.

The app version moves on every release; the contract moves when a route or a response model
moves, and the two are not the same clock. Measured 2026-09-22: three models changed shape
inside one app version — `reconciles` became three-state, `run_net_pnl` became nullable,
`segment_trade_count` was removed — and a consumer had no way to see any of it. `/health`
reported the same number before and after.

**Deliberately not a deprecation mechanism.** A deprecation warning promises that the old shape
still works for a while; §27 says this project ships no backward-compatibility layers in the
alpha, so such a promise would be one we never keep. What is offered instead is stronger
because it is true: the server states which contract it IS, a consumer records that number
with its fixtures and asserts it at start-up. A stale mock then fails loudly and locally — no
connection needed, because the number travels IN the response it was captured from.

Bumped BY HAND, in the same change that moves a model. `CHANGES` carries what moved in the
current version, for the consumer deciding whether they care; the full history is
`docs/consumer/contract-log.md`, which the API serves at GET /api/v1/docs/contract-log.
"""

from typing import List

# One monotonic integer. Not a date and not the app version: a consumer compares it for
# equality, and equality is the only question they have.
API_CONTRACT_VERSION = 46

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'portfolio and state frames: protective_level_enforcement and take_profit_enforcement removed '
    "from the open-position rows; a session holds no level any more, and a backtest's level is the "
    "simulation's own",
    'order-history and order-events: end_reason protection_released is no longer produced and '
    'stays served for a run recorded before this contract; rejection_reason close_withheld removed',
    'every report route answers 409 artifact_unreadable for a stored artifact that no longer matches '
    'its model; some answered 500 before',
    "A close's order_id is its position's on every row; a rebuilt stop's own id is no longer produced",
    'expectancy can be null where no trade has an R, on run summaries, trade analytics, booking '
    'periods, ledger rows and robustness windows; it read 0.0',
    'sweeps: a ranking by expectancy leaves such a run out and counts it in unranked; a sweep that '
    'recorded no objective ranks by net_pnl; the sweep list carries features_beyond_venue',
]
