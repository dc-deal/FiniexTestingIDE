# API Server Architecture

The FiniexTestingIDE HTTP API is a read-only FastAPI application that exposes existing tick and bar
data — and the persisted run-report artifacts of both pipelines (#391) — over HTTP. It is the
server-side counterpart of the FiniexViewer companion project and the foundation for any future
remote-monitoring or tooling integrations.

---

## Purpose & Scope

- Thin HTTP wrapper over existing data managers (`BarsIndexManager`, `ParquetTickReader`)
- No new data logic — the API exposes what already exists
- Read-only: no write endpoints, no trade execution, no scenario control
- Does not replace the CLI — complements it for UI and remote-access use cases

## Why FastAPI

| Feature | Value |
|---|---|
| OpenAPI/Swagger UI | At `/docs` while no consumer is configured; switched off once one is (see *Schema surface* below) |
| Pydantic response models | Typed schema, automatic serialization, validated API surface |
| ASGI / uvicorn | Async-capable, low overhead, industry standard for Python APIs |
| Minimal boilerplate | Route definitions stay close to the handler logic |

## Module Layout

```
python/
  api/
    api_app.py          ← FastAPI app factory (create_app())
    endpoints/          ← Router modules, one per domain — the surface table below lists them
  cli/
    api_server_cli.py   ← Entry point (argparse, no logic)
  framework/
    types/
      api/
        api_types.py    ← Pydantic response models (exception to @dataclass rule)
        report_types.py ← Unified report models (#391) served by reports_router
```

The `endpoints/` directory holds one `APIRouter` module per domain, well past the three files at which a concern gets its own directory. Each is registered in `create_app()` from `ROUTER_SURFACES` — the mount table that pairs a router with the surface its grants name — via `app.include_router(..., prefix='/api/v1')`.

## Request Lifecycle

```
python cli/api_server_cli.py --reload
  └─ uvicorn.run(create_app(), host, port, reload)
       └─ FastAPI app (CORS middleware applied)
            └─ Route handler
                 └─ BarsIndexManager / ParquetTickReader
                      └─ Pydantic response → JSON
```

## CORS Configuration

During development the Vite dev server runs on `:5173` and the API on `:8000`. Both localhost origins are explicitly allowed:

```python
allow_origins=[
    'http://localhost:5173',
    'http://127.0.0.1:5173',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]
```

For production use, restrict `allow_origins` to the actual deployment domain. No additional changes are needed — the CORS list is the only configuration surface.

## Authentication

Every route but the deliberately open ones — `/api/v1/health`, `/api/v1/contract`,
`/api/v1/timeframes` and `/api/v1/validation-checks`, each a decision the endpoint table
explains — requires a bearer token, and
holding a token is not the same as being entitled to what it asks for. The model is not this
project's own: it is the shared `finiex_auth` package, installed from a pinned public tag, so the
security vocabulary exists once rather than once per service.

**Two checks, and they fail differently.** The bearer check is mounted on the ROUTER, so a route
added later inherits it by construction — the failure it prevents cannot be reached by forgetting.
The grant check is declared PER router with `Security(..., scopes=['<surface>'])`, so a router
mounted without its scopes is authenticated but ungated, and looks identical to one that is not.
Only a walk over the surface tells them apart, which is why `assert_no_identity_route_is_ungated`
runs in the suite.

**A grant names a thing, not a route:** `<surface>:<name>`, where the surface is the router and the
name is the route's first path parameter. So `bars:kraken_spot` is one venue's bar data. Report
routes are addressed by a generated run id nobody would write into a token, so `reports:*` is the
realistic grant there — the model degrades to surface level by design.

| Surface | Router | Typical grant |
|---|---|---|
| `brokers` | `broker_router` | `brokers:*` |
| `bars` | `bars_router` | `bars:kraken_spot`, `bars:mt5` |
| `deployments` | `deployments_router` | `deployments:*`, `deployments:deploy_20260918_091413` |
| `directory` | `directory_router` | `directory:*` — it names the operator's own configuration files |
| `reports` | `reports_router` | `reports:*` |
| `sweeps` | `sweeps_router` | `sweeps:*` |

The vocabulary is closed: a grant naming anything else fails when the credentials file is parsed,
at boot, rather than becoming a denial at request time that nobody can explain.

## Every list declares what makes one of its rows unique

An unordered list of objects says nothing about its own identity, and a consumer keying on the
obvious field folds two rows into one — silently, and in the direction that loses data. So every
list response carries a `key`:

```json
{ "key": ["run_id"],                            "runs":        [ ... ] }
{ "key": ["sweep_id"],                          "sweeps":      [ ... ] }
{ "key": ["deployment_id", "currency"],         "deployments": [ ... ] }
{ "key": ["run_id", "currency"],                "sessions":    [ ... ] }
{ "key": ["run_id", "unit_name", "period_no"], "periods":     [ ... ] }
```

A response serving SEVERAL lists declares `keys` instead, one entry per list, because a single
key over two row types names fields one of them does not have (contract 9):

```json
{ "keys": { "units": ["name"], "aggregates": ["currency"] },
  "units": [ ... ], "aggregates": [ ... ] }                                      // portfolio
{ "keys": { "trades": ["scenario_name", "position_id", "exit_tick_index"],
            "analytics": ["currency"], "scenario_totals": ["scenario_name", "currency"] } }
```

A trade is NOT its position: a partial close books several records of one position, and two
scenarios of one symbol each count from `pos_<symbol>_1`. Measured 2026-09-27, `position_id`
alone repeats in 3 of 11 runs on disk; the declared key in none.

**A run's report sections declare keys where a consumer iterates units** — `scenario-details`,
`portfolio`, `trade-history`, `broker`, `run-summary`, `booking-periods`, `warnings-errors`. An
EMPTY key is a declaration too, and it means the row's identity IS its position: a warning is an
event, nothing folds two identical ones into one, and a session that logs one twice has two rows
with the same text (`warnings-errors`: `errors` → `["name"]`, `warnings` → `[]`). The other sections of a
run are exempt for now: their identity is the run they were asked for. **The per-unit lists share
one key, the unit's `name`, and that is a JOIN, meant as one:** the roster in `scenario-details`
knows every scenario, the figures in `portfolio.units` only the ones that produced, and
`run-summary.units_absent` the ones that did not — join them on `name`. A set naming one scenario
twice is refused, and both refused copies are listed; that is the one case the name does not
separate, and their reason says why.

**One unit, four field names — one identity.** The same unit is `name` on `scenario-details` and
`portfolio`, `scenario_name` on `trade-history`, and `unit_name` on `booking-periods`. In a
simulation all four are the scenario's name; in an AutoTrader session all four are the profile's
`profile_name`, else its symbol, and that rule is one method (`AutoTraderConfig.get_unit_name()`)
rather than a copy per section. A selection carried by this value narrows every section, and a unit
that traded nothing is present in the roster with no trades — never missing from it.

Both of the cases that prompted it are ones where the obvious key is wrong: a deployment row is
one per (deployment × account currency), and a booking period's running number restarts per bot,
so two rows of one deployment can both be number 1. It is machine-readable on purpose — a
consumer can assert it rather than read it: where correct USE depends on knowledge, the
knowledge is declared beside the thing rather than left in prose.

**It is NOT the store's key.** A store entry's identity (`StoreEntry.key`) answers how one
ENTRY is addressed; this answers what makes one ROW of THIS response unique, and the two differ
wherever a route aggregates: `/deployments` groups ledger rows by (deployment_id, currency),
while the ledger's own row identity is (run_id, currency, unit_name, period_no).

**A COLLECTION route has no path parameter, so a grant has nothing to be about — and that was a
hole.** Measured 2026-09-13 against a token holding only `bars:*` and `brokers:*`:
`/api/v1/reports/runs` answered 200 with the full run index, naming every AutoTrader session, and
`/api/v1/sweeps` answered 200 — while every identity route beside them was correctly refused. The
package closes it with a floor: a collection route requires **at least one** grant on its router's
surface, and a caller entitled to part of a list still reaches the handler, which filters it.

**The walk cannot see this.** It calls routes whose path contains a parameter, so a collection route
gives it nothing to call. Those refusals are named by hand in the suite, and a new collection route
needs its own test or nothing looks at it.

**Some routes are declared on the app rather than on a router**, so a dependency given at
`include_router` never reaches them. Each state is chosen rather than inherited:
`/api/v1/timeframes` is **open** beside `/health` — the app's own static configuration, none of the
surfaces a grant can name, and gating it would make a market-data grant the precondition for a list
that reveals nothing about market data. `/api/v1/brokers` **requires a token** but takes no grant:
which venues this installation carries is a fact about the installation. `/api/v1/caller` is
token-only too: it is about the caller, and a grant needed to ask what one holds would be circular.

**For a browser client**, `CORSMiddleware` answers the `OPTIONS` preflight before routing, so the
preflight — which carries no `Authorization`, by specification — is never gated. `expose_headers`
lists `WWW-Authenticate` and `Retry-After`, because a browser hides every response header that is
not CORS-safelisted: without it a cross-origin client sees a 401's status and not the scheme to
retry with.

### Schema surface — present in development, gone in production

`/openapi.json`, `/docs` and `/redoc` are FastAPI's own routes, and the framework mounts them at
the **app root** — outside `/api/v1`, where every endpoint of this application lives. Three
consequences follow, and each one alone would make their availability a decision rather than a
default:

- A reverse proxy scoped to the versioned prefix does not forward them either way.
- Authentication is attached per **router** (see above), so it cannot reach a route the framework
  mounts itself.
- The walk that proves no identity route is ungated filters on a path parameter, so a
  parameterless root route is outside it **by construction**. It reported nothing because it never
  looked, which is the worst shape a check can have.

**They are therefore tied to the auth posture: present while no consumer is configured, absent the
moment one is.** Gating them instead was considered and does not work — Swagger UI fetches the
schema from the browser with no bearer, so a gated `/docs` is a broken `/docs`. Absent is also the
stronger answer: holding a credential is not a way back in.

The practical effect is that the schema is a development convenience and never a production
surface. A consumer that needs the contract reads this document and the router modules, which is
what the other two peers already do.

### Who can reach the port at all, and where that is decided

The `reports` surface names every run this installation has ever recorded, and the browser client's
grant on it was issued **on the condition that the API is reachable from the operator's machine
only**. That condition needs a home in a file, or it expires with the conversation that agreed it.

Its home is `docker-compose.yml`: the port is published as `127.0.0.1:8000:8000`. The server itself
binds `0.0.0.0` inside the container, which is correct and says nothing about exposure — a
container's own interface is not a network boundary. **The publish is the boundary**, and that is
why the condition lives there rather than in a startup check: code running inside the container
cannot observe how its port was published, so a check would have to test `--host` instead, where
`0.0.0.0` is the normal and correct value. It would be red on every ordinary start, and a gate that
is red on day one is a gate that gets switched off.

Before this line the port was reachable only because a developer tool happened to forward it, which
made the condition true by accident and invisible to anyone reading the repository. Publishing it
is purely ADDITIVE — a forwarding IDE keeps working — so no consumer's address changes.

**What to do instead of a tripwire:** if the bind is ever widened, the `reports` grants are re-asked
for, and the change is announced to the consumers over the bus. That is a review obligation on this
line, and the comment beside it says so.

**Where tokens live.** `user_configs/credentials/inbound/consumer_tokens.json`, with a tracked
placeholder at `configs/credentials/inbound/consumer_tokens.json` whose entries are all switched
off — an example in a template file then cannot gate or grant anything by accident. The in-memory
registry holds only SHA-256 digests, but the **file holds the plaintext token** — a leaked token
file is a leaked credential (see [`credentials_layout.md`](credentials_layout.md)). A live token
answering from the TRACKED file refuses the boot: that is a real key in the repository, which is
the expensive half of the credential rule. It is checked FIRST, on the raw entries, so it is the
refusal the operator sees even when the same entry also lacks an account or would not parse —
every later refusal says to edit the file that answered, and for this file the only right edit is
to move the key out. An entry counts as live unless its `active` is declared false. Under config
isolation only the tracked copy is read.

**Every token acts for an ACCOUNT.** The token says which client is calling; the account, in
`inbound/accounts.json` beside it, says on whose behalf — a `person`, or a `service` for a machine
consumer such as a sibling project. Until a login exists, presenting a token IS acting as its
account. The two rules differ in reach:

- **Every entry names an account**, a switched-off one too — switching it on is one flag, and that
  flip must not produce a token acting for nobody. The boot refuses an entry that names none and
  lists every such consumer at once, with the command that fixes it.
- **Only a LIVE token's account must exist and be active.** A switched-off example in the tracked
  placeholder may therefore name an account the accounts file does not hold.

`operator` is reserved for the console and can never be an account. After the boot,
`ApiAuthBundle.identities` maps each live consumer to its account and its grants as a list.

Both commands print a block to paste and write nothing:

```
python python/cli/api_token_cli.py account --id <id> --kind person|service --display-name '<name>'
python python/cli/api_token_cli.py mint --consumer <name> --account <id> --grants 'bars:*'
```

`mint` refuses an account the accounts file does not hold, so the account comes first.

**Two switches, not one, and they are separate on purpose.** The bearer dependency is built only
when `api.require_auth` is on AND a consumer is configured. Otherwise nothing places a consumer on
the request, the grant check finds nobody to hold a grant, and behaviour is exactly what it was
before.

Collapsing them would make the first token written into the credentials file gate every route in
the same instant — and a consumer has to HOLD its token before it can start sending the header, so
the rollout needs the window in between. `api.require_auth` defaults to false: the safe direction
is the one that cannot lock out a consumer nobody has told yet.

**What that window can and cannot prove.** With gating off, a request carrying a wrong token — or
nonsense — still answers 200, because nothing verifies it. So the window de-risks the CLIENT (is the
header attached, does anything break by sending it) and not the CREDENTIAL (is this token right, are
its grants right). That is answered the instant gating goes on. Do not read a 200 in this state as
the token being accepted.

The boot line names both conditions, because from a request the two states are indistinguishable:

```
    API authentication: NOT enforced (api.require_auth is off — tokens exist, nothing is gated)
      · 2 consumer(s) [ragengine (ragengine), viewer (analyst)]
      from user_configs/credentials/inbound/consumer_tokens.json
```

Each consumer is named beside the account it acts for.

It is a state to pass through, not one to stay in.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Server liveness — `status`, `version`, `started_at` (when this process started serving, ISO-8601 UTC, new on every restart) and `uptime_s` (seconds since, on the server's monotonic clock). OPEN, so it carries nothing a stranger could use — no commit, no host, no auth state |
| GET | `/api/v1/contract` | Which CONTRACT this server serves, beside the app version — they move on different clocks, and a model can change shape inside one app version. `changes` is one line per change that moved into the current contract; every earlier version is in [`api_contract_log.md`](api_contract_log.md). OPEN like `/health`: a consumer must be able to ask which contract they face before they hold a token, or a version mismatch and a credential failure look alike. Every response also carries `X-Api-Contract`, so a saved fixture is self-describing and a consumer's assertion stays local. Deliberately NOT a deprecation channel — no compatibility layers ship, so a number to compare is the honest offer |
| GET | `/api/v1/timeframes` | All configured timeframes in sorted order |
| GET | `/api/v1/validation-checks` | Every validation check a finding can name — `check` (the stable id served in `run-summary.units_absent[].checks` and `warnings-errors.warnings[].check`), a `title` fit for a label or a facet, and a one-sentence `description`. Declared once in `python/framework/validators/validation_check_catalog.py`, and a test holds it to the ids the code emits in both directions. OPEN like `/timeframes`: what an id means is the app's own declaration, not data. `key` = `["check"]` |
| GET | `/api/v1/brokers` | Broker types available in bar index |
| GET | `/api/v1/caller` | Who the server takes the caller to be (#551): `client` (the consumer the token authenticates as), `account` with `account_kind` (`person` \| `service`) and `display_name` (on whose behalf it calls), `grants` as a list, and the token's `note`. Requires a token while gating is on and takes NO grant — like `/brokers`, it is about the caller, and a grant needed to ask what one holds would be circular. `enforced` is the SERVER's gating state: while it is false nothing verifies a presented token, so every identity field is null even for a caller that sent a valid one, and a 200 is not the token being accepted. A verified consumer bound to no account answers **500 `identity_unbound`** — the boot refuses that state, so reaching it is a defect here, never an anonymous caller |
| GET | `/api/v1/brokers/{broker}/symbols` | Symbols for a broker with `market_type` |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/coverage` | Available date range and timeframes |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/bars` | OHLCV bars (query: `timeframe`, `from`, `to`, `limit`) |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/gaps` | Every interruption in the archive, each with its CATEGORY — a venue outage and a quiet weekend are different facts and the caller never has to infer which from a duration. Served from the discovery cache; computes nothing |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/indicators/atr` | Average True Range per bar (query: `timeframe`, `from`, `to`, `period`, `smoothing`, `limit`) |
| GET | `/api/v1/reports/runs` | Index of EVERY run, newest first — `run_id`, `group` ∈ `simulation` \| `autotrader`, the set / profile name, `ticks_from` (`archive` \| `venue`) and `orders_to` (`simulated` \| `venue`) — which kind of run this is, recorded at its start from the resolved configuration, null on a run recorded before contract 12 — `data_windows` (the market window each unit was DECLARED to cover, one per unit, `end_date` null = open; what a scenario actually processed is on `scenario-details`), `artifacts` (every report file the run persisted, by name), and — from the run's header (#475) — `start_time`, `parent_id` (the sweep, deployment or session this run belongs to; null when it stands alone), `parent_kind` (`sweep` \| `deployment` — WHICH of those the id names, since every parent id is a prefix plus a timestamp and they are otherwise indistinguishable; null when the run stands alone, and also on a run indexed before this field existed, where the kind is unknown rather than absent), `app_version`, `git_commit` and `config_snapshot`. **`group` is the PIPELINE, never the nesting:** a sweep combination is a `simulation` whose `parent_id` names its sweep, and a day record (#476) will be an `autotrader` run whose `parent_id` names its session. `has_reports` is still served, now derived as `artifacts` being non-empty, so the two can never disagree. **`reporting`** (`expected` \| `none`) says whether the run was COMMISSIONED to report — read it together with `artifacts`: empty + `expected` means still running or died before reporting, empty + `none` means it was never meant to. Without the pair a crashed run is indistinguishable from a deliberately silent one. **`artifacts` is what a consumer should read:** the two pipelines produce DIFFERENT sets (an AutoTrader session has no `scenario_details` / `profiling` / `run_meta` / `aggregated_portfolio`), so a client that guessed would get a 404 for the difference. Served from the derived run index, built from each run's `header.json`; a lookup is an exact match against that index. A run with no artifacts exists as logs only (a test session writes none). The entry point the routes below are addressed by |
| GET | `/api/v1/sweeps` | Every recorded parameter sweep, newest first — id, start, duration, combination + ok/error counts, algo, objective. Served from the run-results ledger (#390) |
| GET | `/api/v1/sweeps/{sweep_id}` | One sweep's combinations, RANKED by the objective the sweep declared. Each row carries its `run_id`, the hinge into the report routes. A row's `git_dirty` covers every repository a component of the run came from, not only this one, and reads true when the code state could not be determined — nothing says it was clean (#551, contract 4) |
| GET | `/api/v1/directory` | Every configuration file that can start a run — scenario sets and AutoTrader profiles — including files that never ran (`run_count: 0`), from the config directory's cache (#554, contract 7). A row says what the file DECLARES, read from its raw JSON: scenario counts declared and enabled, symbols, market types, decision logic and workers after the per-scenario cascade, and for a profile its bot id, adapter and declared `dry_run`. `status` is `readable` or `unreadable` with a `reason` — read, never validated: a file being edited is a row, not an error. A file whose NAME is also a configuration of the other kind is `unreadable` too, with that as its reason, and no run starts from it (contract 11). `origin` is `configs` / `user_configs` / `user_algos`; a private path never leaves the server. Run figures come from the run index, matched on `config_snapshot` and the run type. At most `FRESHNESS_S` (30 s) old; `?refresh=true` walks the roots now. `key` = `["file"]` — a file name is ONE entry across every root and both kinds, resolved by precedence (contract 9; it was `["kind", "file"]`) |
| GET | `/api/v1/directory/{file}` | One file: its row, its scenarios read fresh from the file, and the run ids started from it, newest first. An unknown file is `404 config_file_not_found` |
| GET | `/api/v1/reports/runs/{run_id}/trade-history` | Trade-history report (query: `symbol`, `close_reason`, `start`, `end`) |
| GET | `/api/v1/reports/runs/{run_id}/order-history` | Order-history report (query: `symbol`, `status`) |
| GET | `/api/v1/reports/runs/{run_id}/portfolio` | Portfolio report (per-unit full projection + per-currency aggregates) |
| GET | `/api/v1/reports/runs/{run_id}/execution-stats` | Execution-stats report (per-unit order counts + summed totals) |
| GET | `/api/v1/reports/runs/{run_id}/pending-orders` | Pending-orders report (per-unit lifecycle + latency + active orders) |
| GET | `/api/v1/reports/runs/{run_id}/scenario-details` | Scenario-details report (per-scenario execution + signal metadata, sim-only) — the authority for which scenarios a run has, failed ones included; each row carries its `market_type`. `buy_signals` / `sell_signals` / `flat_signals` / `trades_requested` are `null` when nothing counted them — the decision tracker is off by default in the simulation (`performance_tracking.worker_decision_tracking`); how many trades a scenario CLOSED is `portfolio.units[].total_trades`, joined on `name`. `worker_count` is what the scenario declares |
| GET | `/api/v1/reports/runs/{run_id}/run-summary` | Run-summary (cross-section KPIs: per-currency + global order counts) |
| GET | `/api/v1/reports/runs/{run_id}/signal` | Signal-configuration report (per-source provenance + the run's decision basis: fresh / stale / blind ticks) |
| GET | `/api/v1/reports/runs/{run_id}/worker-decision` | Worker/decision report (per-unit component stats) |
| GET | `/api/v1/reports/runs/{run_id}/profiling` | Profiling report (per-operation timings + inter-tick stats) |
| GET | `/api/v1/reports/runs/{run_id}/aggregated-portfolio` | Aggregated-portfolio report (cross-unit, per-currency) |
| GET | `/api/v1/reports/runs/{run_id}/warnings-errors` | Warnings/errors report (the run's tiered advisory + error pot). `errors[].logged_errors` carries `LogEntryRow` objects — level, `observed_at`, `event_time`, `scope`, `message` — not bare strings. An artifact written before that shape answers **409 `artifact_unreadable`**, never 500: run output is regenerated, not migrated |
| GET | `/api/v1/reports/runs/{run_id}/broker` | Broker report (broker + symbol specifications the run executed against) |
| GET | `/api/v1/reports/runs/{run_id}/feed-stability` | Feed-stability report (disturbance episodes as observed spans) |
| GET | `/api/v1/reports/runs/{run_id}/config` | The configuration the run was commissioned with, PARSED, with the file name and the content id (#538) the index attributes to it. The index carried only those two pointers, so a reader who saw a change mark between two sessions of a deployment could not ask what changed. Resolved from the run-config STORE through `config_id` — the per-run copy was retired (#546), because the store holds the same content, is not governed by the file-logging switch the copy was, and writes once per distinct content rather than once per run. The store's INDEX is never served: its `source_path` column carries an operator's private workspace path. Two distinct 404s: `run_not_found` for an unknown identity, `config_snapshot_missing` for a run that declared a snapshot it never filed, which is ordinary because the header is written at run start and the file is copied later |
| GET | `/api/v1/reports/runs/{run_id}/booking-periods` | The run's ledger (#537): one summary per booking period — a trading day, or the stretch the run actually covered — plus its COMPLETENESS check. `reconciles` is three-state: true / false / **null when the run reports no figure in this currency**, which is an absent check and not a passed one. What it proves is that every closed trade reached exactly one period; it cannot prove a P&L is right, because both figures carry the same per-trade value along two routes. One table is ONE account currency — `currencies` names the others, whose periods are ledger rows like these. Served from the stored artifact, never rebuilt from the ledger: recomputed there the check would be `sum(rows) − sum(rows)` and could never fail. A run from before the artifact existed answers 404 |
| GET | `/api/v1/deployments` | Every recorded deployment, newest first — one entry per (deployment × account currency), because a P&L column added over two currencies is not a number. `net_pnl` SUMS over the sessions; `max_drawdown` is their MAXIMUM and never a sum, since each AutoTrader row carries the running decline against the inherited peak. `changed` marks a deployment whose sessions were not all produced by one configuration. Served from the run-results ledger (#497) |
| GET | `/api/v1/deployments/{deployment_id}` | One deployment's sessions, OLDEST first — a life reads forwards, the opposite order to the console. Each session carries `ran_hours`, the `gap_hours` BEFORE it (with `gap_between_starts` where only a start-to-start measure was possible, which overstates it) and the two change marks. `advisory` says whether the rows may be read as one series at all; `unfinished` counts the runs that never reached their close, absent from the sessions by construction because the ledger row is written last. **No reconciliation line, and there cannot be one** — over many runs there is no single run summary to sum against |
| GET | `/api/v1/deployments/{deployment_id}/booking-periods` | Every booking period the deployment booked, across ALL of its sessions — the thirty-day picture in one call, where the run-scoped route would be an N+1 walk. Same row shape as `/reports/runs/{run_id}/booking-periods` plus `run_id`, which across a deployment is the only thing that tells two periods apart (`period_no` is a per-BOT counter that continues across restarts; until 2026-09-23 the floor was persisted before the last period was sealed, so sessions REPEATED a number rather than continuing it — runs recorded before that date carry the repeats) and is the hinge into that run's report routes. Rows that book no period are skipped and their sessions counted in `sessions_without_periods`, so an incomplete history is not read as a quiet one. **No reconciliation, by construction** — see the note under the route above |

### Timeframes Endpoint Details

Returns the globally configured timeframe list in ascending order (by bar duration). The list mirrors `TimeframeConfig._REGISTRY` — adding a new timeframe there automatically makes it appear here. No parameters.

### Bars Endpoint Details

- `from` and `to` are ISO-8601 UTC datetime strings (e.g. `2026-01-01T00:00:00Z`)
- Naive datetimes are treated as UTC
- Response timestamps `t` are **unix seconds UTC** and mark the bar's **OPEN**
- `limit` is the caller's own row cap. Omitted it applies `MAX_BARS`; above it the request is
  refused (`400 invalid_limit`) rather than clamped, so a cap is never applied behind a caller's back
- Maximum bars per request: `MAX_BARS = 10_000` — prevents accidental huge responses
- Valid timeframes: M1, M5, M15, M30, H1, H4, D1 (via `TimeframeConfig`)
- `v` is traded volume and is **0.0 on feeds that carry none** (forex CFD); `tc` is the number of
  ticks aggregated into the bar and is the activity measure on those feeds

#### What the response says about itself

The body is a bare array, because that is what existing clients read. Everything a consumer needs
*about* the rows therefore travels as response headers — additive by construction, so a client that
ignores them is unaffected, and a shortened payload can no longer end in silence.

| Header | Meaning |
|---|---|
| `X-Bar-Count` | Rows in this response |
| `X-Bar-Total` | Rows matching the range **before** the cap — what makes the rest reachable |
| `X-Bar-Limit` | The cap that was applied |
| `X-Bar-Truncated` | `true` when the range held more than the cap |
| `X-Bar-Time-Basis` | `open` — the stamp is the period's start, never its close |
| `X-Bar-Timezone` | `UTC` |
| `X-Bar-Price-Basis` | the price basis the requested bar FILE was stamped with at render: `order_driven` (OHLC of the traded price) · `quote_driven` (OHLC of the midpoint) · `unknown` (a file rendered before the stamp) |

The last three are facts a caller cannot infer from the rows and gets no second chance to get
right: reading a bar stamp as a close-time, or a midpoint as a traded price, produces a plausible
number that is wrong.

Bars are **rendered from ticks** (a DERIVED store): periods with no ticks produce no bar —
gaps are omitted, never zero-filled.

### Gaps Endpoint Details

Per symbol, not per timeframe: the coverage analysis runs at one configured granularity, and a
gap in the tick stream is a gap at every timeframe above it.

`gap_counts` reports only the categories that actually occurred — a zero is noise. Categories
come from the market's own rules, so a weekend is `weekend` on forex and never appears on
crypto, which does not close.

### ATR Endpoint Details

`GET …/indicators/atr?timeframe=M5&from=<iso>&to=<iso>&period=14&smoothing=rma`

Three things this route does that a naive implementation would not:

**It computes a lead-in.** Wilder's smoothing is recursive, so the value at the first requested
bar depends on bars BEFORE it. Computing only the requested range would return a seed rather
than an ATR — and it would look like a number. The lead-in is taken by ROW COUNT, never by a
calendar offset: a calendar window silently under-delivers across a market closure.

**It declares its convention.** "ATR" means Wilder's smoothing everywhere outside this project,
and a caller cannot tell from the rows which average produced them. Three headers say so:

| Header | Meaning |
|---|---|
| `X-Indicator-Smoothing` | `rma` (Wilder, the default and the standard) \| `ema` \| `sma` |
| `X-Indicator-Period` | The period the values were computed with |
| `X-Indicator-Timeframe` | The bar timeframe underneath them |

The bar-semantics headers (`X-Bar-Time-Basis`, `X-Bar-Timezone`) and the row-count headers
(`X-Bar-Count`, `X-Bar-Total`, `X-Bar-Limit`, `X-Bar-Truncated`) travel with it, since the
points carry the same stamps as the bars they came from.

**It refuses rather than clamps.** A `period` above `MAX_INDICATOR_PERIOD` or a `limit` above
`MAX_BARS` answers `400`, the same rule the bars route follows: a cap applied behind the
caller's back is worse than a refusal.

### Reports Endpoints Details

The reports endpoints serve the **persisted** run-report artifacts of the unified reporting
pipeline (#391) — the same canonical models the console and CSV render. They do **not** run or
re-derive anything: `ReportStore` resolves a run by `run_id` through the run index under the run
tree (`runs/{simulation,autotrader}/<owner>/<run_id>/io/`, the `io/` subfolder holding the report
artifacts), reads the section's artifact, and applies the section's filters server-side so the
frontend renders rather than derives.

**A missing section says WHY (contract 5).** One absence has four causes, and a consumer renders
each differently, so the 404's `error` names which — read from the run's index row, which already
records `reporting` and the artifacts the run persisted:

| `error` | Meaning |
|---|---|
| `run_not_found` | no such run in the run index |
| `reports_not_commissioned` | the run was started with `reporting: none` |
| `run_not_completed` | no report artifact YET — the run is still running, or it ended before its report phase; from the server's side the two look the same (a running session and a dead one both have a header and nothing else) |
| `artifact_not_produced` | the run persisted other sections but not this one — its pipeline does not write it (an AutoTrader session has no `scenario-details`, `profiling` or `aggregated-portfolio`), or its outcome left nothing to write |

`/config` keeps its own pair (`run_not_found`, `config_snapshot_missing`), because a configuration
is registered at run START and a report section at its end.

`GET /api/v1/reports/runs` is the index the `{run_id}` routes are addressed by: a consumer
discovers runs there rather than guessing timestamp directory names. Each row carries the run's
type as `group` (`simulation` | `autotrader`), the owning set / profile name, and — since contract
12 — which KIND of run it is (`ticks_from` × `orders_to`, the table in
[the introduction](../introduction_to_the_ide.md#the-kinds-of-run)) and the market window each
unit covers (`data_windows`), so a run picker needs no follow-up request per run. Every indexed run appears, whether or not it produced a report —
`artifacts` names what it has and `has_reports` says whether it has any; an empty index is a normal
`200`, never a 404. The model definitions live in `framework/types/api/report_types.py`; the
pipeline is documented in [reporting_pipeline.md](reporting_pipeline.md).

### Which list is complete — declared, attempted, produced, counted

A simulation run's scenarios appear in four places, and the four counts differ on purpose: each
route answers a different question. Four correct answers still read as a contradiction unless the
questions are written down, so here they are:

| Stage | What it is | Where |
|---|---|---|
| **declared** | every scenario the configuration names, `enabled: false` ones included | `/config` → `config.scenarios` |
| **attempted** | every ENABLED scenario the engine tried, with an outcome — a scenario rejected by validation or by the data-quality phase is here with `status: failed` and its reason | `/scenario-details` → `units[].status` |
| **produced** | every scenario that ran and wrote results | `/portfolio` → `units`, `/broker` → `units[].scenarios`, and the other per-unit sections |
| **counted** | what the run's KPIs are summed over | `/run-summary` → `unit_count` |

**`run-summary` states the difference itself (contract 6)**, in both pipelines, so a consumer
reading the figures never has to compare routes to learn what they leave out:

```json
"units_declared": 10, "units_disabled": 0, "unit_count": 8,
"units_absent": [ { "name": "ETHUSD_blocks_01",
                    "reason": "Scenario 'ETHUSD_blocks_01' failed validation: … Warmup for M30 has 1/20 bars",
                    "reason_code": "ValidationError", "checks": ["warmup_quality"] },
                  { "name": "ETHUSD_blocks_02", "reason": "…", "reason_code": "ValidationError",
                    "checks": ["warmup_quality"] } ]
```

`units_declared == units_disabled + len(units_absent) + unit_count`, and the two sides come from two
sources — declared from the configuration, absent and counted from the results. An AutoTrader
session is declared 1: counted when it ran, absent with its emergency cause when it aborted at
startup. **A run recorded before contract 6 states none of the three — they are `null`**, and the
equation holds wherever they are stated (contract 9; before it they read 0, which the equation then
disproved).

`reason_code` is the cause for a program, in the vocabulary of `scenario-details`' `error_type`:
`ValidationError` for a refusal before the run, the exception's class for a crash (an AutoTrader
session aborted at startup carries its exception's class too), `NoResults` when nothing failed and
nothing was produced. `checks` names, for a refusal, the stable ids of the checks that refused it —
`warmup_quality`, `tick_stretch_gap`, `data_availability`, … — so "which scenarios did warmup cost
me" is a filter, not a text search.

**`scenario-details` is the authority for "which scenarios does this run have"**: it is the one
section built from the batch itself rather than from the results, so a scenario that never produced
anything is still a row. The gap between declared and attempted is the disabled scenarios. An
AutoTrader session has no scenario grid at all — a session IS one unit, and the list of a bot's
sessions is `/deployments/{deployment_id}`.

### Error Responses

All errors return structured JSON — no raw FastAPI tracebacks:
```json
{"error": "symbol_not_found", "detail": "No symbol 'XYZ' for broker 'mt5' in the bar index"}
```

**`error` is for a program, `detail` is for a person.** A consumer branches on the code and may
show the sentence as it comes: it names what happened and, where the remedy is a setting, the
setting — never a field of a response or the code behind it (contract 10; a test refuses a
backtick in any of them). The authentication answers' sentences are the shared package's.

**`error` names the CAUSE, never only the status.** One absence usually has several causes, and a
consumer renders each differently — so there is no bare `not_found` (contract 6). Every code is
declared once, with its status and its sentence, in `python/api/api_error_catalog.py`; a route
raises `api_error(KIND, …)` and never an `ApiException` with a literal. The authentication codes
are the shared `finiex_auth` package's own closed vocabulary (`AuthErrorCode`). A test holds this
table to both, in both directions.

| HTTP | `error` | Cause |
|---|---|---|
| 400 | `invalid_timestamp` | a report filter is not ISO-8601 |
| 400 | `invalid_timeframe` | the timeframe is not in `TimeframeConfig` |
| 400 | `invalid_limit` | `limit` outside 1 … `MAX_BARS` — refused, never clamped |
| 400 | `invalid_period` | an indicator period outside 1 … `MAX_INDICATOR_PERIOD` |
| 400 | `invalid_range` | `from` is not earlier than `to` |
| 401 | `unauthenticated` | no token, or one the registry does not know (`finiex_auth`) — carries `WWW-Authenticate: Bearer` |
| 403 | `forbidden` | the token holds no grant for this surface or name (`finiex_auth`) |
| 404 | `run_not_found` | no such run in the run index |
| 404 | `reports_not_commissioned` | the run was started with `reporting: none` |
| 404 | `run_not_completed` | the run has no report artifact yet — running, or it ended before its report phase |
| 404 | `artifact_not_produced` | the run persisted other sections but not this one |
| 404 | `config_snapshot_missing` | the run declares a configuration snapshot that was never filed |
| 404 | `broker_not_found` | the broker is not in the bar index |
| 404 | `symbol_not_found` | the broker has no such symbol in the bar index |
| 404 | `no_bars_indexed` | the symbol is indexed but holds no bars |
| 404 | `timeframe_not_rendered` | no bars exist for that timeframe |
| 404 | `coverage_report_unavailable` | the coverage cache holds no report for the symbol yet |
| 404 | `no_bars_in_range` | bars exist, but none in the requested window |
| 404 | `deployment_not_found` | no such deployment in the run-results ledger |
| 404 | `sweep_not_found` | no such sweep in the run-results ledger |
| 404 | `config_file_not_found` | the config directory lists no such file |
| 409 | `artifact_unreadable` | an artifact exists but no longer matches its model — usually its age |
| 429 | `rate_limited` | too many attempts (`finiex_auth`) — carries `Retry-After` |
| 500 | `market_type_not_configured` | a broker in the bar index has no `market_type` in `market_config.json` |
| 500 | `identity_unbound` | a verified token is bound to no account — a defect on this side |

## Extension Guide — Adding a New Endpoint

Routers are split per domain under `python/api/endpoints/`:

1. Create `python/api/endpoints/<domain>_router.py`
2. Define an `APIRouter` instance and add the routes there
3. Add it to `ROUTER_SURFACES` in `api_app.py` with the surface its grants name, and add the
   same surface to `ConsumerToken.GRANT_SURFACES` — a test holds the two to one set.
   `create_app()` mounts every entry under `/api/v1` with the bearer check and the grant check;
   a router included by hand carries neither
4. Raise the contract and record the change — the steps are in
   [`api_contract_log.md`](api_contract_log.md#how-a-change-is-recorded)

Response models go in `python/framework/types/api/api_types.py` (or `report_types.py` for
report sections).

## Pydantic Exception Note

Project convention is `@dataclass` for runtime data structures. The `api/` types use Pydantic
`BaseModel` instead because FastAPI's OpenAPI schema generation and response validation depend on
it. This exception is scoped to `python/framework/types/api/` only.

## Open Decisions

- **Production deployment**: Options are static hosting of the Vue build embedded in the FastAPI app vs. separate containers. Deferred until FiniexViewer v0.1 is stable (issue #5 there).

## Memory Cache Integration (V1.4 — #21)

The bars endpoint added in #298 reads Parquet files per request. Issue #21 introduces a `FileCache`
with LRU eviction for exactly this pattern. When #21 is implemented, the integration point is the
bar-file read inside the bars endpoint handler — replace `pd.read_parquet(path)` with
`FileCache.get_or_load(broker, symbol, path)`. The `FileCache` class belongs in
`python/framework/data_preparation/` alongside `tick_parquet_reader.py`.
