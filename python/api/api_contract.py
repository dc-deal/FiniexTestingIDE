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
API_CONTRACT_VERSION = 44

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "GET /api/v1/reports/runs/{run_id}/venue-api-performance, new: how the venue's REST API "
    'answered a live session, per endpoint (key venue_endpoint) — calls, avg_ms, min_ms, max_ms, '
    "slow_calls and failed_calls, with the session's totals and the slow_call_threshold_ms a call "
    'was judged against; AutoTrader only, written when the session ran its monitor',
    'venue-account: every row carries reconciliation — enabled, cycles_compared, cycles_skipped and '
    'divergences_seen over the whole session; a session that reconciled without a broker-truth line '
    'now has a row too',
    "broker: every row carries fee_report (where the venue reports a fill's fee, as its adapter "
    "declares) and venue_fee_expected (whether the run's fills carry the venue's own fee — false in "
    'a backtest and a dry run)',
    'order-history: every row carries order_event_seq, its key into the order-event stream, and a '
    "fill's slippage — slippage_measured_against, slippage_reference_price, slippage and "
    'slippage_pct, positive is a cost',
    'execution-stats: fill_slippage per unit and reference kind and fill_slippage_totals over every '
    "unit's fills, with keys for the three lists",
    'trade-history: exit_slippage and exit_slippage_pct of a stop-loss or take-profit exit are '
    'measured against the level that fired instead of the submission mid; '
    'exit_slippage_measured_against says which',
    'The run list: venue_api_performance.json joins artifacts',
]
