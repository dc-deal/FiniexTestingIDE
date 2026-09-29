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
API_CONTRACT_VERSION = 17

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    "reports/runs/{run_id}/run-summary and …/portfolio: `final_equity` is ONE account's closing "
    'equity and is null when the currency spans several accounts — a backtest of several '
    'scenarios, whose sum no account ever held. The sum is `total_final_equity`, beside '
    '`total_initial_balance` and `unit_count`; `account_max_drawdown_unit` names the account the '
    'drawdown, `max_equity` and `account_max_dd_pct` describe',
    'reports/runs/{run_id}/aggregated-portfolio: `max_equity` / `max_equity_scenario` are '
    '`highest_equity` / `highest_equity_scenario` — the highest peak of ANY account, not the peak '
    'beside the drawdown (that is `headline.max_equity`); `recovery_factor` is null over several '
    'accounts',
    'reports/runs/{run_id}/booking-periods: every period carries `opening_equity` and its costs '
    "split (`commission_cost`, `swap_cost`, `spread_cost`); `unit_totals` folds each unit's "
    'periods into its total, and `keys` replaces `key`; `final_equity` is `total_final_equity`, '
    "the sum over the units — it was the last row's own figure. The rows of "
    'deployments/{deployment_id}/booking-periods carry the same period fields',
    'reports/runs/{run_id}/trade-history: every execution carries `shared_by` — how many trade '
    "rows of its unit carry that fill; a partial close's `commission_cost` includes its exit "
    'fee, so `commission_cost + swap_cost == total_fees` on every row',
    'reports/runs and directory: every run carries `tick_timespan_seconds` — the market time its '
    'units processed, covered together so a shared stretch counts once; run-summary adds '
    '`tick_timespan_total_seconds`, the sum',
    'sweeps/{sweep_id}: `final_equity`, `unrealized_pnl` and `open_position_count` are null on a '
    "row that folds several accounts — the latest reading of one of them is not the run's",
]
