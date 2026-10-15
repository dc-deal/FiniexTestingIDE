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
API_CONTRACT_VERSION = 32

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "GET /api/v1/brokers/{broker}/symbols/{symbol}/gaps: every gap carries origin_class — the "
    "origin class of the tick file it lies in or follows, null where none could be found — and "
    "origin_class_after, which differs from it only at a seam between two origins",
    "The gaps answer carries gaps_by_year — per year and origin class the short, moderate and "
    "large gaps and the hours without data, over the whole archive",
    "The gaps answer's key is replaced by keys, one entry per list: gaps by start, gaps_by_year by "
    "year and origin_class",
    "Two query parameters narrow the gaps, never the counts or gaps_by_year: origin_class and "
    "since (the gaps that start at or after it; anything not ISO-8601 is refused with 400 "
    "invalid_timestamp)",
    "A gap's reason no longer guesses at a cause: 'check data collection' and "
    "'restart/connection?' are gone, because the reason cannot know whose data surrounds a gap "
    "(a coverage cache built before this contract serves the old words until it is rebuilt)",
    "GET /api/v1/reports/runs/{run_id}/trade-history: every scenario_totals row carries "
    "total_spread_cost — the unit's measured spread cost summed, null where the unit read data "
    "without a quote and on a run recorded before this contract",
    "The portfolio's total_spread_cost follows the same rule and is now nullable: null for a unit "
    "that read data without a quote, and on the aggregated portfolio — with avg_spread — null as "
    "soon as one unit's is",
]
