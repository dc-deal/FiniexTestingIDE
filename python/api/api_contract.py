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
API_CONTRACT_VERSION = 40

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "A booking period a trading day closed ends at that day's boundary (closed_at), and the tick "
    'that crosses it belongs to the new day: its fills, its trades and its move',
    'Every trading day has its booking period: a day the run heard nothing from is booked with '
    'tick_count 0; a market that closes books no closed day, and the period after a weekend opens '
    'where the last trading day ended',
    'tick_count on every booking-period row, null on a period recorded before this contract',
    'final_equity, min_equity, max_equity and max_drawdown of a booking period are null where the '
    'period could value the account at no point; deepest_period_drawdown is null when none could',
    "A period's band starts at its opening value; a session's last period includes its end",
    "A session's trading days reach the ledger as they close: a running or dead session shows its "
    'sealed days, and its ended, ran_hours, run_outcome and counts are null until its close',
    'account_max_drawdown, max_equity and account_max_dd_pct of the run summary are typed nullable',
    "A deployment's unfinished overlaps with its sessions: a session that sealed a day and never "
    'closed stands in both',
    'max_drawdown and max_drawdown_pct of a deployment and of its sessions, and '
    'account_max_drawdown of a sweep combination and of a booking-period unit total, are null '
    'where no drawdown was measured',
    'unranked on a sweep: the combinations its ranking left out because they carry no value for '
    'its objective',
    'rendered_config_id, broker_config_id and period_tick_count on every sweep combination row',
]
