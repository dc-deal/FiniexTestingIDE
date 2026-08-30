# API Endpoint Tests

Tests for all FiniexTestingIDE HTTP API endpoints. Uses `FastAPI TestClient` with mocked data managers — no parquet files or bar index required.

**Suite:** `tests/framework/api/test_api_endpoints.py`
**Runner:** `🧩 Pytest: API Endpoints (All)` or `pytest tests/framework/api/ -v`

## Coverage

| Class | Test | Validates |
|---|---|---|
| `TestTimeframes` | `test_list_timeframes_structure` | Response has `timeframes` list, each entry has `name` and `minutes` |
| `TestTimeframes` | `test_list_timeframes_contains_known_entries` | M1=1min, H1=60min, D1=1440min are present and correct |
| `TestTimeframes` | `test_timeframes_sorted_ascending_by_minutes` | List is sorted from shortest to longest bar duration |
| `TestHealth` | `test_health_ok` | Status + version in response |
| `TestBrokers` | `test_list_brokers` | Broker list from mocked index |
| `TestSymbols` | `test_list_symbols` | Symbols with correct `market_type` |
| `TestSymbols` | `test_unknown_broker_returns_404` | 404 + `error: not_found` |
| `TestCoverage` | `test_coverage_ok` | start/end/timeframes fields present |
| `TestCoverage` | `test_unknown_symbol_returns_404` | 404 + `error: not_found` |
| `TestBars` | `test_bars_ok` | OHLCV shape, correct field names |
| `TestBars` | `test_invalid_timeframe_returns_400` | 400 + `error: invalid_timeframe` |
| `TestBars` | `test_from_after_to_returns_400` | 400 + `error: invalid_range` |
| `TestBars` | `test_unknown_broker_returns_404` | 404 + `error: not_found` |
| `TestReportRuns` | `test_list_runs` | Run index: `count`, newest-first order, `group` + `name` per row |
| `TestReportRuns` | `test_no_persisted_run_is_not_an_error` | Empty store returns `200` with an empty index, not 404 |

## Mocking Strategy

`BarsIndexManager`, `MarketConfigManager` and `ReportStore` are patched at their import location in each router module. `pd.read_parquet` is patched for the bars test to return a minimal in-memory DataFrame. No filesystem access occurs during the test run.

## Sweep routes (`TestSweeps`)

A sweep is a family of runs, so it has its own routes. These pin what the run index must NOT do
and what the sweep view must:

| Test | Description |
|------|-------------|
| `test_lists_recorded_sweeps` | `/sweeps` groups the ledger rows into one row per sweep |
| `test_no_sweep_is_not_an_error` | An empty ledger is a legitimate empty list, never a 404 |
| `test_combinations_are_ranked_by_the_sweeps_own_objective` | `/sweeps/{id}` ranks by the objective the spec declared — ranking by anything else answers a question the sweep did not ask |
| `test_each_combination_carries_its_run_id` | The hinge into the report routes; without it a sweep view is a dead end |
| `test_unknown_sweep_is_a_404` | An id with no ledger rows |

