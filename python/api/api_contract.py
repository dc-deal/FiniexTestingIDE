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
API_CONTRACT_VERSION = 4

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'caller: GET /api/v1/caller says who the server takes the caller to be — `client` (the '
    'consumer the token authenticates as), `account` with `account_kind` (`person` | `service`) '
    "and `display_name` (on whose behalf it calls), `grants` as a list, and the token's "
    '`note`. A token is required while gating is on, and no grant. `enforced` names the '
    "server's gating state: while it is false nothing verifies a presented token, so every "
    'identity field is null even for a caller that sent a valid one',
    'sweeps: `RunResultRow.git_dirty` on /api/v1/sweeps/{sweep_id} changed MEANING, not shape. '
    'It used to cover this repository only; it now covers every repository a component of the '
    'run came from, so a strategy from an uncommitted algo repository reads true. A code state '
    'that could not be determined also reads true, where it used to read false — nothing says '
    'it was clean',
]
