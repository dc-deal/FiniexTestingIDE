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
API_CONTRACT_VERSION = 23

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'order-events: GET /api/v1/reports/runs/{run_id}/order-events serves every step of every '
    'order — events — and, for a live session, what the venue reported when the session asked it '
    '— broker_truth. Both lists are keyed ["scenario_name", "seq"] in keys, and seq runs across '
    'both within a unit. Served while a run is going; the run list names it in stream_files',
    'venue-account: GET /api/v1/reports/runs/{run_id}/venue-account, live sessions only — what the '
    'venue held at the start and at the end, and the reconciliation lines between; key ["name"]. A '
    'backtest and a dry run against a real venue have none',
    "Order counts in execution-stats, run-summary, aggregated-portfolio and a sweep's "
    'combinations: orders_sent is gone; one count per way an order starts or ends replaces it — '
    'orders_submitted, orders_adopted, orders_executed, orders_denied, orders_rejected, '
    'orders_cancelled, orders_expired, orders_undelivered, orders_unaccounted. execution-stats '
    'adds orders_failed',
    'MEANING: orders_executed now counts the fills of closing orders too, and orders_rejected '
    "counts the venue's refusals only — a refusal before anything was sent is orders_denied",
    'order-history: status gains denied, undelivered and unaccounted and loses submitted and '
    'partial; new order_type, close_type, initiator and end_reason; swap and slippage_points '
    'removed. rejection_reason gains unaccounted_order, position_not_found and close_withheld and '
    'loses broker_unreachable and unresolved_write',
    'pending-orders: counted from the order events, and an AutoTrader session now has a row. '
    'total_submitted, total_accepted, total_rejected, total_never_confirmed and total_expired; '
    'avg/min/max_in_flight_ms and in_flight_count replace the latency fields; '
    'never_confirmed_orders lists the orders never confirmed. The pending_* fields of '
    'aggregated-portfolio follow the same names',
    'trade-history adds close_type, entry_lots and position_closes',
    "deployments: orders_to per row lists where its sessions' orders went — venue is real money, "
    'both values a mix, which is also changed; each session carries orders_to and '
    'orders_to_changed, and the advisory carries orders_to and fires on a mix alone',
    'A run recorded before this contract serves 0 in the new counts and the pending counters until '
    'it is run again',
]
