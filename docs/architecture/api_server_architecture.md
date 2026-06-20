# API Server Architecture

The FiniexTestingIDE HTTP API is a read-only FastAPI application that exposes existing tick and bar data — and the persisted run-report artifacts of both pipelines (#391) — over HTTP. It is the server-side counterpart of the FiniexViewer companion project and the foundation for any future remote-monitoring or tooling integrations.

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

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Server liveness — `{"status":"ok","version":"..."}` |
| GET | `/api/v1/timeframes` | All configured timeframes in sorted order |
| GET | `/api/v1/brokers` | Broker types available in bar index |
| GET | `/api/v1/brokers/{broker}/symbols` | Symbols for a broker with `market_type` |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/coverage` | Available date range and timeframes |
| GET | `/api/v1/brokers/{broker}/symbols/{symbol}/bars` | OHLCV bars (query: `timeframe`, `from`, `to`) |
| GET | `/api/v1/reports/runs/{run_id}/trade-history` | Trade-history report (query: `symbol`, `close_reason`, `start`, `end`) |
| GET | `/api/v1/reports/runs/{run_id}/order-history` | Order-history report (query: `symbol`, `status`) |
| GET | `/api/v1/reports/runs/{run_id}/portfolio` | Portfolio report (per-unit full projection + per-currency aggregates) |
| GET | `/api/v1/reports/runs/{run_id}/execution-stats` | Execution-stats report (per-unit order counts + summed totals) |
| GET | `/api/v1/reports/runs/{run_id}/pending-orders` | Pending-orders report (per-unit lifecycle + latency + active orders) |
| GET | `/api/v1/reports/runs/{run_id}/scenario-details` | Scenario-details report (per-scenario execution + signal metadata, sim-only) |
| GET | `/api/v1/reports/runs/{run_id}/run-summary` | Run-summary (cross-section KPIs: per-currency + global order counts) |
| GET | `/api/v1/reports/runs/{run_id}/execution-stats` | Execution-stats report (per-unit order counts + summed totals) |

### Timeframes Endpoint Details

Returns the globally configured timeframe list in ascending order (by bar duration). The list mirrors `TimeframeConfig._REGISTRY` — adding a new timeframe there automatically makes it appear here. No parameters.

### Bars Endpoint Details

- `from` and `to` are ISO-8601 UTC datetime strings (e.g. `2026-01-01T00:00:00Z`)
- Naive datetimes are treated as UTC
- Response timestamps `t` are **unix seconds UTC**
- Maximum bars per request: `MAX_BARS = 10_000` — prevents accidental huge responses
- Valid timeframes: M1, M5, M15, M30, H1, H4, D1 (via `TimeframeConfig`)

### Reports Endpoints Details

The reports endpoints serve the **persisted** run-report artifacts of the unified reporting
pipeline (#391) — the same canonical models the console and CSV render. They do **not** run or
re-derive anything: `ReportStore` resolves a run by `run_id` under the logs tree
(`logs/{scenario_sets,autotrader}/<owner>/<run_id>/`), reads the `trade_history.json` /
`order_history.json` / `portfolio.json` artifact, and applies the section's filters
server-side so the frontend renders rather than derives. A run without the requested artifact
returns `404 run_not_found`. The model definitions live in `framework/types/api/report_types.py`;
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

Project convention is `@dataclass` for all data structures (§6). The `api/` types use Pydantic `BaseModel` instead because FastAPI's OpenAPI schema generation and response validation depend on it. This exception is scoped to `python/framework/types/api/` only.

## Open Decisions

- **Authentication**: Deferred. JWT or OAuth2 would wrap the existing route layer without changing handlers. No auth in v1.
- **Production deployment**: Options are static hosting of the Vue build embedded in the FastAPI app vs. separate containers. Deferred until FiniexViewer v0.1 is stable (issue #5 there).
- **Version source**: `APP_VERSION` is a constant in `api_app.py`. Centralize once the project adopts a unified version file.

## Memory Cache Integration (V1.4 — #21)

The bars endpoint added in #298 reads Parquet files per request. Issue #21 introduces a `FileCache` with LRU eviction for exactly this pattern. When #21 is implemented, the integration point is the bar-file read inside the bars endpoint handler — replace `pd.read_parquet(path)` with `FileCache.get_or_load(broker, symbol, path)`. The `FileCache` class belongs in `python/framework/data_preparation/` alongside `tick_parquet_reader.py`.
