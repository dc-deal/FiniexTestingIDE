# API Contract Log

A consumer of this API records the contract number its fixtures were captured under and asserts
it at start-up, so a stale mock fails locally instead of silently answering with an old shape. The
server tells them only what moved into the CURRENT number: `GET /api/v1/contract` returns
`changes` for this version and nothing older. A consumer whose fixtures are several versions
behind needs every step in between, and this log is where they are kept — newest first.

Not here: what each route serves today (the endpoint table in
[`api_server_architecture.md`](api_server_architecture.md#endpoints)), and why the contract is a
number rather than a deprecation promise (the module docstring of `python/api/api_contract.py`).

## How a change is recorded

In the same change that moves a route or a response model, or changes what a field means:

1. Raise `API_CONTRACT_VERSION` in `python/api/api_contract.py` by one.
2. Replace `CHANGES` with the new version's lines — one line per change, for someone who cannot
   read this repository. The old lines are not kept in code: this log is the history.
3. Add the new version at the top of this log, with its date and its issue.

A change of MEANING counts as much as a change of shape: a field that keeps its type and starts
answering a different question breaks a consumer just as silently, and more so, because nothing
fails to parse.

The server serves the current version's lines and this log keeps every version. A test holds the
newest heading here to `API_CONTRACT_VERSION`, so step 3 cannot be skipped unnoticed.

## Version 4 — 2026-09-24 (#551)

- `GET /api/v1/caller` says who the server takes the caller to be: `client` (the consumer the
  token authenticates as), `account` with `account_kind` (`person` | `service`) and
  `display_name` (on whose behalf it calls), `grants` as a list, and the token's `note`.
- It requires a token while gating is on, and no grant.
- `enforced` is the server's gating state. While it is false nothing verifies a presented token,
  so every identity field is null, even for a caller that sent a valid one.
- `RunResultRow.git_dirty` on `/sweeps/{sweep_id}` changed MEANING, not shape. It used to cover
  this repository only; it now covers every repository a component of the run came from, so a
  strategy from an uncommitted algo repository reads `true`. A code state that could not be
  determined also reads `true`, where it used to read `false` — nothing says it was clean.

## Version 3 — 2026-09-23 (#538, #546)

- `/reports/runs/{run_id}/config` serves the configuration a run was commissioned with, parsed,
  resolved from the run-config store through the run's `config_id`.
- `config_snapshot` on that response is the SOURCE file name. The per-run copy that used to sit
  in the run directory is retired.
- Two distinct 404s: `run_not_found` for an unknown identity, and `config_snapshot_missing` for a
  run that predates the store and therefore carries no id to resolve through.

## Version 2 — 2026-09-23 (#539, #537)

- `deployments`: three read-only routes — `/deployments`, `/deployments/{id}` and
  `/deployments/{id}/booking-periods` — on a new `deployments` grant surface.
- `/reports/runs/{run_id}/booking-periods` serves the run's booking periods as a stored artifact.
  A run from before it answers 404.
- `BookingPeriodsReport.reconciles` is THREE-state: `true` | `false` | `null`, where `null` means
  the run reports no figure in this currency and the check did NOT run.
- `BookingPeriodsReport.run_net_pnl` and `run_total_trades` are nullable for the same reason.
- `BookingPeriodsReport` gained `currencies`. One table is ONE account currency, and this names
  EVERY currency the run booked, the shown one included; the console is what filters the shown
  one out when it prints.
- `RunResultRow.segment_trade_count` was REMOVED. Read `total_trades`, which is the same record
  count and always was.
- `segment_max_drawdown` is now a MAGNITUDE, matching `account_max_drawdown` and the excursion
  columns; the sign is a display decision.
- Every list response declares `key` — what makes one of its rows unique.
- Deployment rows carry `bot_id`, the one identity that does not move.

## Version 1 — before the number existed

Responses carry no `X-Api-Contract` header. A fixture without the header was captured before
version 2 and predates everything above.
