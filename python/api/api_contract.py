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
API_CONTRACT_VERSION = 58

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "order-events: ts_event on a row (new) — the venue's own stamp of the step, where it reports "
    "one: a live order's fill in full; null in a backtest, a mock session, a dry run and on every "
    'other event',
    'an AutoTrader session runs on a timeline of arrivals and timers: every time it records is when '
    "a tick or an answer reached it, no longer the venue's stamp of the tick; a backlog it fell "
    "behind on is one slice, the algo computing once on its newest tick; a venue's answer resolves "
    'on arrival, except while a stopping session waits for its last orders — a session recorded '
    'from this contract on can differ from one recorded before',
    "worker-decision: a session's ticks_processed leaves out the ticks of a backlog behind its "
    'newest, which never reached the algorithm — a changed meaning in a session',
    'order-events and pending-orders: a live in_flight_ms runs until the session reads the answer, '
    'which it now does right after the answer arrives, no longer up to one heartbeat after it '
    'except while a stopping session waits for its last orders — a changed meaning',
]

