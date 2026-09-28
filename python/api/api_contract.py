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
API_CONTRACT_VERSION = 12

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'reports/runs: an AutoTrader session\'s `group` is `autotrader` (was `live`) — every AutoTrader '
    'session, mock, dry run or real orders alike. The value `live` no longer occurs, here or in '
    'any `run_type` the API serves',
    'reports/runs: every run carries `ticks_from` (`archive` | `venue`) and `orders_to` '
    '(`simulated` | `venue`), recorded at its start from the resolved configuration — with '
    '`group` they tell a backtest, a mock session, a dry run and a real-money session apart — and '
    '`data_windows`, the market window each unit was declared to cover (`unit_name`, `start_date`, '
    '`end_date`; `end_date` null means open). All three are null on a run recorded before '
    'contract 12',
    'sweeps: a combination row\'s `config_snapshot` — the full strategy configuration as JSON — is '
    'now `strategy_config_json`',
    'directory: the AutoTrader test profiles moved from the folder `backtesting` to `mock`, and the '
    'one live-adapter probe among them to `observation`',
    'reports/runs/{run_id}/config: a configuration recorded from contract 12 on names a scenario '
    '`scenario_name` and a profile `profile_name`; a run recorded before keeps what it recorded',
    'booking periods: every `segment_*` field is `period_*` — `period_no`, `period_opened_at`, '
    '`period_closed_at`, `period_close_reason`, `period_max_equity`, `period_min_equity`, '
    '`period_max_drawdown` — on reports/runs/{run_id}/booking-periods, '
    'deployments/{id}/booking-periods and a sweep\'s combination rows, and every declared `key` '
    'names `period_no`. Stored runs were migrated, so an older run serves the new names too',
]
