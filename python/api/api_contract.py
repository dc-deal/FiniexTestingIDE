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
API_CONTRACT_VERSION = 43

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "GET /api/v1/reports/runs/{run_id}/units/{unit_name}/recorded-streams, new: a unit's timeline — "
    'every stream it records over time (today its state frames) with its record_count and the time '
    'of its first and latest record, its cadence, and recording; made to be asked every second or two',
    "GET /api/v1/reports/runs/{run_id}/units/{unit_name}/recorded-streams/window, new: a unit's "
    'records in [from, to), oldest first, at most limit of them (1 to 2000, refused outside it, never '
    'clamped) — more only where one millisecond holds more, as a page never ends inside one; a bound '
    'left out is resolved from the records the window holds, null when it holds none; each state '
    'frame carries record_no, its position in the stream; matched, returned, truncated, and '
    'next_from — set only when the page left records out, on a millisecond boundary',
    'Two new 404 codes: unit_not_found and streams_not_recorded',
]
