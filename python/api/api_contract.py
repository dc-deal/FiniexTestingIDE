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
API_CONTRACT_VERSION = 3

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'reports: /reports/runs/{run_id}/config serves the configuration a run was commissioned '
    'with, parsed, resolved from the run-config store through the run\'s `config_id`. '
    '`config_snapshot` on the response is the SOURCE file name — the per-run copy that used to '
    'sit in the run directory is retired. Two distinct 404s: `run_not_found` for an unknown '
    'identity, `config_snapshot_missing` for a run that predates the store and therefore '
    'carries no id to resolve through',
]

# Version 2 — what a consumer captured before the route above.
PREVIOUS_CHANGES: List[str] = [
    'deployments: three read-only routes — /deployments, /deployments/{id}, '
    '/deployments/{id}/booking-periods — on a new `deployments` grant surface',
    'reports: /reports/runs/{run_id}/booking-periods serves the run Hauptbuch as a stored '
    'artifact; a run from before it answers 404',
    'BookingPeriodsReport.reconciles is THREE-state: true | false | null, where null means the '
    'run reports no figure in this currency and the check did NOT run',
    'BookingPeriodsReport.run_net_pnl / run_total_trades are nullable for the same reason',
    'BookingPeriodsReport gained `currencies` — one table is ONE account currency, and this '
    'names EVERY currency the run booked, the shown one included; the console is what filters '
    'the shown one out when it prints',
    'RunResultRow.segment_trade_count was REMOVED; read `total_trades`, which is the same '
    'record count and always was',
    'segment_max_drawdown is now a MAGNITUDE, matching account_max_drawdown and the excursion '
    'columns; the sign is a display decision',
    'every list response declares `key` — what makes one of its rows unique',
    'deployment rows carry `bot_id`, the one identity that does not move',
]
