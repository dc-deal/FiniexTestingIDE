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
API_CONTRACT_VERSION = 15

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'reports/runs: every run says what it DID — `results`, one entry per account currency '
    '(`currency`, `net_pnl`, `total_trades`, keyed by `results_key`), null when the ledger holds '
    'nothing for the run and [] when it closed without figures; plus `run_outcome` and the counts '
    '`error_count`, `warning_count` (Tier 1) and `log_warning_count` (Tier 2), null where not recorded',
    'reports/runs/{run_id}/warnings-errors: `outcome` carries the same three counts, counted once '
    'the same way in both pipelines; null on an artifact written before them',
    'directory: every row carries `last_run_figures` — what its newest run did, the same figures '
    'the run list carries; null when there is no run or the ledger holds nothing for it',
    'sweeps/{sweep_id}: `key` is ["run_id", "currency"] — the ranked rows are folded per run and '
    'currency, and the per-period key it declared before did not describe them',
]
