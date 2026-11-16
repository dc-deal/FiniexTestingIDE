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
API_CONTRACT_VERSION = 56

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'runs: total_units, failed_count and succeeded_count on every row (new) — how many units the '
    'run had and how many of them failed or did not, as its warnings-errors outcome counts them; '
    'null on a run recorded before this contract, while a session runs, and on a run that died '
    'before its close',
    'sweeps: best_combinations on every row (new) — the first-ranked combination in each account '
    'currency, by the same rule the sweep detail ranks with; best_combinations_key names currency; '
    'none for a sweep whose recorded objective cannot rank',
    "sweep detail: total_units, failed_count and succeeded_count on every combination (new) — the "
    "run's unit counts, as on the run list; a sweep whose recorded objective cannot rank is "
    'refused with 409 sweep_objective_unrankable (new), where it was a server error',
    "sweep detail: total_final_equity and total_initial_balance on every combination (new) — what "
    "the run's accounts in that currency held at its end and started with, each summed; a sweep "
    'may rank by it; null on a run recorded before this contract and while a session runs',
    'sweeps: an objective that is not a number no longer ranks; such a sweep is refused like one '
    'ranked by a figure a run can leave undefined',
]

