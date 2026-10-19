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
API_CONTRACT_VERSION = 35

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "New GET /api/v1/activities: one row per activity running on the machine this server runs on "
    "— a backtest, a parameter sweep, an AutoTrader session, an import, a discovery cache build, a "
    "test suite, the server itself — keyed by activity_id, never by a process id; each with the "
    "resident memory of its processes (rss_bytes, process_count), beside the machine's memory and "
    "its pressure verdict read at the same instant, and server_activity_id, the row of the server "
    "answering",
    'GET /api/v1/activities and GET /api/v1/machine-resources sit on a new grant surface, machine: '
    'a token reaches them with machine:* and is refused with 403 without it; grants are read at '
    'start-up, so a newly issued grant counts after the server restarts',
    "GET /api/v1/brokers moves onto the brokers surface: listing the brokers needs a brokers grant, "
    "as a broker's symbols already did",
    "Every row of GET /api/v1/brokers/{broker}/symbols carries tick_size, the instrument's smallest "
    "price step from the broker's configuration; null where the configuration names no such symbol "
    'or cannot be read',
    'from and to on the bars and ATR routes accept an ISO-8601 instant only: a number that is no '
    'ISO date, which was read as Unix seconds, and any other text that is no instant, which '
    'answered 422, are refused with 400 invalid_timestamp; an instant without an offset is read as '
    "UTC, and an eight-digit date in ISO's basic format (20261019) is read as the date it is",
    "Every booking period, a run's and a deployment's, carries what it carries beside what it did: "
    "account_max_drawdown and account_max_dd_pct, the account's decline since its equity curve "
    "began — an AutoTrader session continues its predecessor's curve wherever its profile carries "
    "the risk history over, whatever its deployment — and unrealized_pnl and open_position_count "
    'at the seal',
    'New error activity_registry_unreadable (500): the list of what runs on the machine could not be '
    'read, which is not the same as nothing running',
    'New document activities',
]
