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
`docs/architecture/api_contract_log.md`, and #524 is what will eventually serve it.
"""

from typing import List

# One monotonic integer. Not a date and not the app version: a consumer compares it for
# equality, and equality is the only question they have.
API_CONTRACT_VERSION = 20

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'reports/runs/{run_id}/order-history: a rejected row states its side (`action`), its '
    '`symbol`, `direction` and `requested_lots`, and when it was refused — `?symbol=` no longer '
    'drops rejections. Runs recorded before carry the symbol and the side; direction, size and '
    'time stay null there',
    'order-history: `execution_time` is renamed `event_time` — when the row\'s event happened '
    '(the fill, the refusal, the expiry) on the run\'s clock; every other `execution_time` in the '
    'API is a duration',
    'order-history: an absent value is null, never an empty string or 0.0 — position_id, '
    'direction, action, requested_lots, executed_lots, executed_price, event_time, '
    'rejection_reason, rejection_message. `direction`, `action`, `status` and `rejection_reason` '
    'are enums in the schema; their values are unchanged',
    'order-history: an expired row states its direction and requested lots, and the expiry of a '
    'close-side order (a protective stop) says `close` instead of `open`',
    'reports/runs/{run_id}/pending-orders: an active order\'s `order_type` and `direction` are '
    'enums in the schema. The active-order lists hold the orders still resting when the unit\'s '
    'data ended — in a backtest the same orders are recorded `expired` in order-history',
]
