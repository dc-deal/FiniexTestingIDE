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
| OpenAPI/Swagger UI | Available at `/docs` out of the box — no extra setup |
| Pydantic response models | Typed schema, automatic serialization, validated API surface |
| ASGI / uvicorn | Async-capable, low overhead, industry standard for Python APIs |
| Minimal boilerplate | Route definitions stay close to the handler logic |

## Module Layout

```
python/
  api/
    api_app.py          ← FastAPI app factory (create_app())
    endpoints/          ← Router modules (broker_router, bars_router, reports_router)
  cli/
    api_server_cli.py   ← Entry point (argparse, no logic)
  framework/
    types/
      api/
        api_types.py    ← Pydantic response models (exception to @dataclass rule)
        report_types.py ← Unified report models (#391) served by reports_router
```

The `endpoints/` directory holds one `APIRouter` module per domain. The §26 threshold is reached (`broker_router`, `bars_router`, `reports_router`), each registered in `create_app()` via `app.include_router(..., prefix='/api/v1')`.

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

Every route but `/api/v1/health` requires a bearer token, and holding a token is not the same as
being entitled to what it asks for. The model is not this project's own: it is the shared
`finiex_auth` package, installed from a pinned public tag, so the security vocabulary exists once
rather than once per service.

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
| `reports` | `reports_router` | `reports:*` |
| `sweeps` | `sweeps_router` | `sweeps:*` |

The vocabulary is closed: a grant naming anything else fails when the credentials file is parsed,
at boot, rather than becoming a denial at request time that nobody can explain.

**A COLLECTION route has no path parameter, so a grant has nothing to be about — and that was a
hole.** Measured 2026-09-13 against a token holding only `bars:*` and `brokers:*`:
`/api/v1/reports/runs` answered 200 with the full run index, naming every live run, and
`/api/v1/sweeps` answered 200 — while every identity route beside them was correctly refused. The
package closes it with a floor: a collection route requires **at least one** grant on its router's
surface, and a caller entitled to part of a list still reaches the handler, which filters it.

**The walk cannot see this.** It calls routes whose path contains a parameter, so a collection route
gives it nothing to call. Those refusals are named by hand in the suite, and a new collection route
needs its own test or nothing looks at it.

**Two routes are declared on the app rather than on a router**, so a dependency given at
`include_router` never reaches them. Both states are chosen rather than inherited:
`/api/v1/timeframes` is **open** beside `/health` — the app's own static configuration, none of the
four surfaces, and gating it would make a market-data grant the precondition for a list that reveals
nothing about market data. `/api/v1/brokers` **requires a token** but takes no grant: which venues
this installation carries is a fact about the installation.

**For a browser client**, `CORSMiddleware` answers the `OPTIONS` preflight before routing, so the
preflight — which carries no `Authorization`, by specification — is never gated. `expose_headers`
lists `WWW-Authenticate` and `Retry-After`, because a browser hides every response header that is
not CORS-safelisted: without it a cross-origin client sees a 401's status and not the scheme to
retry with.

**Where tokens live.** `user_configs/credentials/api_tokens.json`, with a tracked placeholder at
`configs/credentials/api_tokens.json` whose entries are all switched off — an example in a template
file then cannot gate or grant anything by accident. The registry holds only SHA-256 digests, so a
configuration file that leaks is not a leaked credential; a lost token is re-minted, never
recovered. A live token answering from the TRACKED file refuses the boot: that is a real key in the
repository, which is the expensive half of the credential rule.

Mint one with `python python/cli/api_token_cli.py mint --consumer <name> --grants 'bars:*'`.

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
      · 2 consumer(s) [ragengine, viewer] from user_configs/credentials/api_tokens.json
```

It is a state to pass through, not one to stay in.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Server liveness — `{"status":"ok","version":"..."}` |
| GET | `/api/v1/timeframes` | All configured timeframes in sorted order |
| GET | `/api/v1/brokers` | Broker types available in bar index |
| GET | `/api/v1/brokers/{broker}/symbols` | Symbols for a broker with `market_type` |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/coverage` | Available date range and timeframes |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/bars` | OHLCV bars (query: `timeframe`, `from`, `to`, `limit`) |
| GET | `/api/v1/reports/runs` | Index of EVERY run, newest first — `run_id`, `group` ∈ `simulation` \| `live`, the set / profile name, `artifacts` (every report file the run persisted, by name), and — from the run's header (#475) — `start_time`, `parent_id` (the sweep or session this run belongs to; null when it stands alone), `app_version`, `git_commit` and `config_snapshot`. **`group` is the PIPELINE, never the nesting:** a sweep combination is a `simulation` whose `parent_id` names its sweep, and a live day fragment (#476) will be a `live` whose `parent_id` names its session. `has_reports` is still served, now derived as `artifacts` being non-empty, so the two can never disagree. **`reporting`** (`expected` \| `none`) says whether the run was COMMISSIONED to report — read it together with `artifacts`: empty + `expected` means still running or died before reporting, empty + `none` means it was never meant to. Without the pair a crashed run is indistinguishable from a deliberately silent one. **`artifacts` is what a consumer should read:** the two pipelines produce DIFFERENT sets (a live session has no `scenario_details` / `profiling` / `run_meta` / `aggregated_portfolio`), so a client that guessed would get a 404 for the difference. Served from the derived run index, built from each run's `header.json`; a lookup is an exact match against that index. A run with no artifacts exists as logs only (a test session writes none). The entry point the routes below are addressed by |
| GET | `/api/v1/sweeps` | Every recorded parameter sweep, newest first — id, start, duration, combination + ok/error counts, algo, objective. Served from the run-results ledger (#390) |
| GET | `/api/v1/sweeps/{sweep_id}` | One sweep's combinations, RANKED by the objective the sweep declared. Each row carries its `run_id`, the hinge into the report routes |
| GET | `/api/v1/reports/runs/{run_id}/trade-history` | Trade-history report (query: `symbol`, `close_reason`, `start`, `end`) |
| GET | `/api/v1/reports/runs/{run_id}/order-history` | Order-history report (query: `symbol`, `status`) |
| GET | `/api/v1/reports/runs/{run_id}/portfolio` | Portfolio report (per-unit full projection + per-currency aggregates) |
| GET | `/api/v1/reports/runs/{run_id}/execution-stats` | Execution-stats report (per-unit order counts + summed totals) |
| GET | `/api/v1/reports/runs/{run_id}/pending-orders` | Pending-orders report (per-unit lifecycle + latency + active orders) |
| GET | `/api/v1/reports/runs/{run_id}/scenario-details` | Scenario-details report (per-scenario execution + signal metadata, sim-only) |
| GET | `/api/v1/reports/runs/{run_id}/run-summary` | Run-summary (cross-section KPIs: per-currency + global order counts) |
| GET | `/api/v1/reports/runs/{run_id}/signal` | Signal-configuration report (per-source provenance + the run's decision basis: fresh / stale / blind ticks) |
| GET | `/api/v1/reports/runs/{run_id}/worker-decision` | Worker/decision report (per-unit component stats) |
| GET | `/api/v1/reports/runs/{run_id}/profiling` | Profiling report (per-operation timings + inter-tick stats) |
| GET | `/api/v1/reports/runs/{run_id}/aggregated-portfolio` | Aggregated-portfolio report (cross-unit, per-currency) |
| GET | `/api/v1/reports/runs/{run_id}/warnings-errors` | Warnings/errors report (the run's tiered advisory + error pot). `errors[].logged_errors` carries `LogEntryRow` objects — level, `observed_at`, `event_time`, `scope`, `message` — not bare strings. An artifact written before that shape answers **409 `artifact_unreadable`**, never 500: run output is regenerated, not migrated (§27) |
| GET | `/api/v1/reports/runs/{run_id}/broker` | Broker report (broker + symbol specifications the run executed against) |
| GET | `/api/v1/reports/runs/{run_id}/feed-stability` | Feed-stability report (disturbance episodes as observed spans) |

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
| `X-Bar-Price-Basis` | `mid` — OHLC is `(bid + ask) / 2`, not a traded price |

The last three are facts a caller cannot infer from the rows and gets no second chance to get
right: reading a bar stamp as a close-time, or a mid as a traded price, produces a plausible
number that is wrong.

Bars are **rendered from ticks** (a DERIVED store, §44): periods with no ticks produce no bar —
gaps are omitted, never zero-filled.

### Reports Endpoints Details

The reports endpoints serve the **persisted** run-report artifacts of the unified reporting
pipeline (#391) — the same canonical models the console and CSV render. They do **not** run or
re-derive anything: `ReportStore` resolves a run by `run_id` under the logs tree
(`logs/{scenario_sets,autotrader}/<owner>/<run_id>/io/`, the `io/` subfolder holding the
report artifacts), reads the `trade_history.json` / `order_history.json` / `portfolio.json`
artifact, and applies the section's filters
server-side so the frontend renders rather than derives. A run without the requested artifact
returns `404 run_not_found`.

`GET /api/v1/reports/runs` is the index the `{run_id}` routes are addressed by: a consumer
discovers runs there rather than guessing timestamp directory names. Each row carries the log
group (`scenario_sets` = simulation, `autotrader` = live) and the owning set / profile name, so
a run picker needs no follow-up request per run. A run appears once it has a trade-history
artifact; an empty index is a normal `200`, never a 404. The model definitions live in `framework/types/api/report_types.py`;
the pipeline is documented in [reporting_pipeline.md](reporting_pipeline.md).

### Error Responses

All errors return structured JSON — no raw FastAPI tracebacks:
```json
{"error": "not_found", "detail": "Symbol 'XYZ' not found for broker 'mt5'."}
```

| HTTP | `error` key | Condition |
|---|---|---|
| 400 | `invalid_timeframe` | Timeframe not in `TimeframeConfig` registry |
| 400 | `invalid_range` | `from >= to` |
| 404 | `not_found` | Unknown broker or symbol |
| 500 | `config_error` | Broker in bar index but missing from `market_config.json` |

## Extension Guide — Adding a New Endpoint

Routers are split per domain under `python/api/endpoints/`:

1. Create `python/api/endpoints/<domain>_router.py`
2. Define an `APIRouter` instance and add the routes there
3. Register via `app.include_router(router, prefix='/api/v1')` in `create_app()`

Response models go in `python/framework/types/api/api_types.py` (or `report_types.py` for
report sections).

## Pydantic Exception Note

Project convention is `@dataclass` for all data structures (§6). The `api/` types use Pydantic
`BaseModel` instead because FastAPI's OpenAPI schema generation and response validation depend on
it. This exception is scoped to `python/framework/types/api/` only.

## Open Decisions

- **Authentication**: Deferred. JWT or OAuth2 would wrap the existing route layer without changing handlers. No auth in v1.
- **Production deployment**: Options are static hosting of the Vue build embedded in the FastAPI app vs. separate containers. Deferred until FiniexViewer v0.1 is stable (issue #5 there).
- **Version source**: `APP_VERSION` is a constant in `api_app.py`. Centralize once the project adopts a unified version file.

## Memory Cache Integration (V1.4 — #21)

The bars endpoint added in #298 reads Parquet files per request. Issue #21 introduces a `FileCache`
with LRU eviction for exactly this pattern. When #21 is implemented, the integration point is the
bar-file read inside the bars endpoint handler — replace `pd.read_parquet(path)` with
`FileCache.get_or_load(broker, symbol, path)`. The `FileCache` class belongs in
`python/framework/data_preparation/` alongside `tick_parquet_reader.py`.
