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
API_CONTRACT_VERSION = 52

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'every figure a console printed is now a field: fields added, nothing removed, renamed or '
    'changed in meaning',
    'trade-history: duration_ticks on every row; on each analytics entry the counts by direction, '
    'outcome and close reason, the averages per trade and the holding time in ticks',
    'order-history: failed_listed, failed_by_unit and failed_by_reason count the failed rows '
    'listed — a filter counts them again',
    'portfolio and pending-orders: open_position_count on a unit row; active_limit_count, '
    'active_stop_count and active_order_count',
    'aggregated-portfolio: total_costs (every cost category, the spread included — not a fee '
    'total) and spot_total_est_pnl with its percentage',
    'run-summary: execution_rate_pct over the run\'s counts',
    'worker-decision: a unit\'s worker sums and algo time, unit_mean_time_ms on a totals row, '
    'decision_totals and bottlenecks over the tracked units — null where none tracked',
    'profiling: the clipping rate, the slowest profiled unit beside the mean, the warmup\'s total '
    'and slowest phase with each phase\'s share, worker_decision_breakdowns, and keys',
    'order-history: keys, [] for the order rows',
    'a run recorded earlier answers what its own rows give; what it does not keep is null',
    'scenario-details, warnings-errors, booking-periods, feed-stability, signal, broker: the '
    'shares, differences and counts their lists imply',
    'validation checks: usable_funds_negative',
]
