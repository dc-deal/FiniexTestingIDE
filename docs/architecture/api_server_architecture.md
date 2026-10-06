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

Once every router is mounted, the factory prints a short overview beside the authentication boot
line, so a restart shows in its console which contract it serves and what it mounted:

```
📜 API contract 15 · app 1.4.0 · 35 routes under /api/v1
   per surface: brokers 1 · bars 4 · deployments 3 · directory 2 · reports 17 · sweeps 2 · top-level 6
   moved into contract 15 (4), in full at GET /api/v1/contract:
     · reports/runs: every run says what it DID — `results`, one entry per account currency …
```

The routes are counted from the routers `ROUTER_SURFACES` mounts plus the ones the factory mounts
itself; "top-level" is the second group — `/health`, `/contract` and the other app-level reads.

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

**`expose_headers` is the half that is easy to forget.** A browser hides every response header
that is not CORS-safelisted, so a header the server sends is invisible to a browser client unless
it is listed. Four groups are published, and three of them carry something the body cannot: the
authentication headers (`WWW-Authenticate`, `Retry-After`), the contract stamp, `Link` — the
document describing the route — and the seven `X-Bar-*` plus three `X-Indicator-*` headers, which
are the only place a bare-array response says what its rows mean. The bar headers were served and
unlisted until contract 22, which made them unreadable by the one consumer that is a browser.

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
| `docs` | `docs_router` | `docs:*` — the documents are one set, and a grant per document would name files rather than data |
| `reports` | `reports_router` | `reports:*` |
| `sweeps` | `sweeps_router` | `sweeps:*` |

The vocabulary is closed: a grant naming anything else fails when the credentials file is parsed,
at boot, rather than becoming a denial at request time that nobody can explain.

## Every list declares what makes one of its rows unique

An unordered list of objects says nothing about its own identity, and a consumer keying on the
obvious field folds two rows into one — silently, and in the direction that loses data. So every
list response carries a `key`, or `keys` where it serves several lists.

The rule, the shapes, the one-unit-four-names join and the measurement behind the trade key are
in the document the API serves for it, [`consumer/row-keys.md`](../consumer/row-keys.md) — written
for the consumer who has to act on it, and reachable by them at `/api/v1/docs/row-keys`. The gate
that holds every served list to its declaration is `tests/framework/api/test_row_keys.py`.

What belongs HERE is the half a consumer never sees: how the gating around those lists works.


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

**The same holds for every port that FORWARDS to it.** The viewer's Vite server proxies `/api` to
this API, so `docker-compose.finiexviewer.yml` binds its ports — the dev server's and `vite
preview`'s — to `127.0.0.1` too. Published on every interface, either would reach the API from the
network around the loopback binding above.

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

The route table is not here, and deliberately not anywhere in this repository twice. **Each route
declares the document that describes it**, in its own decorator:

```python
@router.get('/reports/runs/{run_id}/booking-periods', response_model=BookingPeriodsReport,
            openapi_extra=describes('booking-periods'))
```

That declaration does three things. It rides into the published OpenAPI schema, so the whole
mapping is readable in one request. It is what the `Link: <…>; rel="describedby"` header on every
answer is built from. And `tests/framework/api/test_docs_endpoints.py` walks every mounted route
and fails on one that declares nothing, so a new route cannot be added without naming its
document.

The documents themselves are `docs/consumer/`, one per route family, served by this API at
`/api/v1/docs` — they are the reference for what each route answers with and what each field
means. `api_app.ROUTER_SURFACES` remains the authoritative mount table.

Every `GET` route answers `HEAD` as well, the way HTTP expects: the same status and headers,
`X-Api-Contract` included, and no body. FastAPI registers only the methods a route names, so a
small middleware serves `HEAD` as the `GET` it asks about.

### Error responses

All errors return structured JSON — no raw FastAPI tracebacks:
```json
{"error": "symbol_not_found", "detail": "No symbol 'XYZ' for broker 'mt5' in the bar index"}
```

**`error` is for a program, `detail` is for a person.** A consumer branches on the code and may
show the sentence as it comes: it names what happened and, where the remedy is a setting, the
setting — never a field of a response or the code behind it (contract 10; a test refuses a
backtick in any of them).

**`error` names the CAUSE, never only the status.** One absence usually has several causes, and a
consumer renders each differently — so there is no bare `not_found` (contract 6). Every code is
declared once, with its status and its sentence, in `python/api/api_error_catalog.py`; a route
raises `api_error(KIND, …)` and never an `ApiException` with a literal. The authentication codes
are the shared `finiex_auth` package's own closed vocabulary (`AuthErrorCode`).

**The table of codes lives in [`consumer/errors.md`](../consumer/errors.md)**, because the people
who branch on them are the ones who cannot read this repository.
`tests/framework/api/test_api_error_catalog.py` holds that document, the catalog and the routes to
one set, in every direction.


## Extension Guide — Adding a New Endpoint

Routers are split per domain under `python/api/endpoints/`:

1. Create `python/api/endpoints/<domain>_router.py`
2. Define an `APIRouter` instance and add the routes there, each with
   `openapi_extra=describes('<document>')`
3. Add it to `ROUTER_SURFACES` in `api_app.py` with the surface its grants name, and add the
   same surface to `ConsumerToken.GRANT_SURFACES` — a test holds the two to one set.
   `create_app()` mounts every entry under `/api/v1` with the bearer check and the grant check;
   a router included by hand carries neither
4. Write or extend `docs/consumer/<document>.md`. It is SERVED, so it is written for a reader
   with no repository: no file path, no class name, no issue number, nothing about how this
   project is built
5. Raise the contract and record the change (below)

A new SURFACE is not finished when it is mounted. The tracked placeholder gains the grant, but
the operator's own token file is theirs — until a consumer's token names the new surface they get
a **403**, which reads exactly like a defect in the route. Say so in the closing report, and say
that the server has to be restarted: the token registry is read at boot.

Response models go in `python/framework/types/api/api_types.py` (or `report_types.py` for
report sections).

### Raising the contract

In the same change that moves a route, moves a response model, or changes what a field MEANS — a
change of meaning counts as much as a change of shape, and more, because nothing fails to parse:

1. Raise `API_CONTRACT_VERSION` in `python/api/api_contract.py` by one.
2. Replace `CHANGES` with the new version's lines — one line per change, written for someone who
   cannot read this repository. The old lines are NOT kept in code: the log is the history.
3. Add the new version at the top of [`consumer/contract-log.md`](../consumer/contract-log.md),
   with its date and its issue. A test holds that log's newest heading to the constant, so this
   step cannot be skipped unnoticed.

The log is compressed on the README pattern — the newest five versions in full, older ones one
line each — so it stops growing. Compressing the sixth-newest is part of adding a new one.

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
