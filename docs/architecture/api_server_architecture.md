# API Server Architecture

The FiniexTestingIDE HTTP API is a read-only FastAPI application that exposes existing tick and bar data over HTTP. It is the server-side counterpart of the FiniexViewer companion project and the foundation for any future remote-monitoring or tooling integrations.

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
    endpoints/          ← Future routers land here once §26 threshold is reached
  cli/
    api_server_cli.py   ← Entry point (argparse, no logic)
  framework/
    types/
      api/
        api_types.py    ← Pydantic response models (exception to @dataclass rule)
```

The `endpoints/` directory is reserved for router modules added in #298 and beyond. Until three or more router files exist (§26), endpoints live inline in `api_app.py`.

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

## Endpoints (#297 Foundation)

| Method | Path | Handler | Description |
|---|---|---|---|
| GET | `/api/v1/health` | `health()` | Server liveness check |
| GET | `/api/v1/brokers` | `list_brokers()` | Broker types available in bar index |

Full data endpoints (symbols, coverage, bars) are added in #298.

## Extension Guide — Adding a New Endpoint

**While endpoint count is below the §26 threshold (< 3 router files):**

Add the route inline in `api_app.py` next to the existing endpoints.

**Once 3+ router files are warranted (at or after #298):**

1. Create `python/api/endpoints/<domain>_router.py`
2. Define an `APIRouter` instance and move related routes there
3. Register via `app.include_router(router, prefix='/api/v1')` in `create_app()`

Response models for new endpoints go in `python/framework/types/api/api_types.py`.

## Pydantic Exception Note

Project convention is `@dataclass` for all data structures (§6). The `api/` types use Pydantic `BaseModel` instead because FastAPI's OpenAPI schema generation and response validation depend on it. This exception is scoped to `python/framework/types/api/` only.

## Open Decisions

- **Authentication**: Deferred. JWT or OAuth2 would wrap the existing route layer without changing handlers. No auth in v1.
- **Production deployment**: Options are static hosting of the Vue build embedded in the FastAPI app vs. separate containers. Deferred until FiniexViewer v0.1 is stable (issue #5 there).
- **Version source**: `APP_VERSION` is a constant in `api_app.py`. Centralize once the project adopts a unified version file.

## Memory Cache Integration (V1.4 — #21)

The bars endpoint added in #298 reads Parquet files per request. Issue #21 introduces a `FileCache` with LRU eviction for exactly this pattern. When #21 is implemented, the integration point is the bar-file read inside the bars endpoint handler — replace `pd.read_parquet(path)` with `FileCache.get_or_load(broker, symbol, path)`. The `FileCache` class belongs in `python/framework/data_preparation/` alongside `tick_parquet_reader.py`.
