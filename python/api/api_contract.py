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
API_CONTRACT_VERSION = 18

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'reports/runs/{run_id}/portfolio and …/run-summary: `total_fees` is the fees of the CLOSED '
    'trades — the population trade-history, booking-periods and the ledger sum; what the run '
    'charged, open positions included, is `fees_charged`. They differ by the fees of what is '
    'still open',
    'portfolio, run-summary and the ledger rows: a trade that realised exactly nothing is neither '
    'a winner nor a loser — `losing_trades` no longer counts it (win_rate is unchanged, avg_loss '
    'no longer divides by it)',
    'portfolio.aggregates and run-summary: when no account declined, the drawdown trio names the '
    'first account and its peak instead of max_equity 0.0 and no unit',
    'run-summary and trade-history analytics: a streak is the longest of ONE account; over '
    'several scenarios their trades are no longer interleaved into one sequence',
    "booking-periods: `unit_totals[].opening_equity` is null when the unit's first period did "
    "not record one — it no longer shows a later period's",
    'sweeps/{sweep_id}: combinations are ranked within each account currency, currencies in '
    'order; order counts (`orders_sent` and siblings) are null on a row folded from booking '
    'periods, which carry none — they read 0',
    'deployments/{deployment_id}: each currency is its own series — `index`, the gap and the '
    'change marks restart per currency',
    'aggregated-portfolio: a spot row takes its base / quote split and its value estimate from '
    'the unit — the estimate of the initial value no longer drops an initial base holding',
    "trade-history: a spot trade's `mae_*` / `mfe_*` are tracked between entry and close from "
    'this contract on; runs recorded before it keep the values they were written with',
]
