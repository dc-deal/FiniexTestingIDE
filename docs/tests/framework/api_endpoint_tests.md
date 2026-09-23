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
| `TestBars` | `test_bars_carry_the_tick_count` | `tc` per bar — the activity measure on feeds whose volume is 0.0 |
| `TestBars` | `test_a_cut_response_says_that_it_was_cut` | `X-Bar-Truncated` / `Count` / `Total` / `Limit` on a capped range |
| `TestBars` | `test_a_complete_response_says_it_was_not_cut` | the same headers on an uncut range |
| `TestBars` | `test_every_response_states_its_own_semantics` | `X-Bar-Time-Basis` `open` · `X-Bar-Timezone` `UTC` · `X-Bar-Price-Basis` `mid` |
| `TestBars` | `test_a_limit_above_the_cap_is_refused_rather_than_clamped` | 400 + `error: invalid_limit` |
| `TestBars` | `test_a_limit_below_one_is_refused` | 400 + `error: invalid_limit` |
| `TestBars` | `test_invalid_timeframe_returns_400` | 400 + `error: invalid_timeframe` |
| `TestBars` | `test_from_after_to_returns_400` | 400 + `error: invalid_range` |
| `TestBars` | `test_unknown_broker_returns_404` | 404 + `error: not_found` |
| `TestReportRuns` | `test_list_runs` | Run index: `count`, newest-first order, `group` + `name` per row |
| `TestReportRuns` | `test_no_persisted_run_is_not_an_error` | Empty store returns `200` with an empty index, not 404 |

## Authentication (`test_api_auth.py`)

The API answered anyone who could reach it, and CORS is not access control — it is a browser
mechanism that a non-browser client simply does not consult. Two halves are tested, and the second
is the one that gets forgotten.

| Test class | What it pins |
|---|---|
| `TestTheScaffoldStateChangesNothing` | With no consumer configured every route still answers — the rollout's first step has to be byte-identical, or it is not reversible |
| `TestATokenIsRequired` | 401 without a header and for an unknown token; `WWW-Authenticate: Bearer` on the refusal, so a client can tell a dead credential from a transport fault; `/health` stays open |
| `TestHoldingATokenIsNotHoldingAGrant` | The walk over every identity route with a token holding NOTHING (403 required on each); a market-data token reaches its broker and is refused on a report; a grant for one broker does not carry to another |
| `TestTheTokenFileIsRefusedWhenItIsTheTrackedOne` | A live token answering from the committed file refuses the boot; an inactive entry there is fine, which is what lets the placeholder carry examples |
| `TestTheSurfaceVocabularyIsClosed` | An unknown surface fails when the token is parsed, not at request time; and the vocabulary is held to the same set as `api_app.ROUTER_SURFACES`, so a router mounted under a surface no token can name — or a surface no router serves — fails here rather than becoming a denial nobody can explain |
| `TestACollectionRouteIsGatedToo` | The hole the walk cannot see: a route with no path parameter had nothing for a grant to be about, so `/reports/runs`, `/sweeps` and `/deployments` would answer any authenticated token. Refusal and admission are both named by hand — a new collection route needs its own pair or nothing looks at it |
| `TestTheAppLevelRoutesAreADecision` | `/timeframes` open beside `/health`, `/brokers` requiring a token and taking no grant — pinned so neither drifts back to being accidental |
| `TestTheSchemaSurfaceIsOffWhereItCannotBeGuarded` | `/openapi.json`, `/docs` and `/redoc` are FastAPI's own routes at the APP ROOT — outside `/api/v1`, uncoverable by a router dependency, and outside the walk by construction (it filters on a path parameter). They are tied to the auth posture instead: present while nobody is configured, gone once somebody is, and a token does not bring them back |
| `TestTheCorsPreflightIsNeverGated` | An `OPTIONS` without `Authorization` is not refused, and `WWW-Authenticate` / `Retry-After` are exposed — invisible from every seat but a browser's |
| `TestTheRegistryNeverHoldsAToken` | Only digests are stored, and the boot line names consumers and never tokens |

The walk names one identity route per router as `required`: a router dropping out of the app would
otherwise leave it green while the surface it gated went unreachable.

## Mocking Strategy

`BarsIndexManager`, `MarketConfigManager` and `ReportStore` are patched at their import location in
each router module. `pd.read_parquet` is patched for the bars test to return a minimal in-memory
DataFrame. No filesystem access occurs during the test run.

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

## Deployment routes (`TestDeployments`)

A deployment is the life of ONE bot across its restarts, and it is not a run: no header, no
directory, no artifacts. Its rows live in the ledger and nowhere else, which is why these routes
read the ledger and only the ledger.

| Test | Description |
|------|-------------|
| `test_lists_recorded_deployments` | `/deployments` groups the ledger rows into one row per deployment |
| `test_a_deployments_pnl_sums_and_its_drawdown_does_not` | The one arithmetic this view must not get wrong: each live row carries the RUNNING decline against the inherited peak, so the reduction is `max()` and a sum counts one decline once per session that was still inside it |
| `test_no_deployment_is_not_an_error` | Nothing declared yet is a state, not a failure |
| `test_sessions_read_forwards` | Oldest first — the opposite order to the console, deliberately |
| `test_a_configuration_change_is_reported_before_the_table` | The advisory rides on the response rather than inside a row, so a client cannot render the table and drop the sentence that says whether the rows may be added up |
| `test_a_session_that_never_reached_its_close_is_counted` | The ledger row is written last, so a killed session is absent from the list by construction (§44) |
| `test_unknown_deployment_is_a_404_and_not_an_empty_history` | An empty list would read as a deployment that ran and did nothing |
| `test_the_detail_route_filters_in_the_store` | `read_rows(deployment_id=...)`, not a full read followed by a filter |
| `test_the_periods_of_every_session_come_back_in_one_call` | `/deployments/{id}/booking-periods` — the thirty-day picture without walking the sessions |
| `test_every_period_names_the_session_that_booked_it` | `segment_no` restarts wherever a session wrote no carry-over floor, so two periods of one deployment can both be #1; `run_id` is what tells them apart |
| `test_a_row_that_books_no_period_is_skipped_and_counted` | Every row written before the booking journal is one of those — skipped, and its session counted, so an incomplete history is not read as a quiet one |
| `test_the_periods_carry_their_own_band_not_the_cumulative_one` | `segment_max_drawdown`, not `account_max_drawdown` — the running figure would repeat the same number down the column |
| `test_there_is_no_reconciliation_and_that_is_deliberate` | Pinned as an ABSENCE: across many runs no second, independently derived figure exists, so a check could only compare the rows with themselves (§48) |
| `test_periods_of_an_unknown_deployment_are_a_404` | An id with no ledger rows |

## TestGaps — `/brokers/{broker}/symbols/{symbol}/gaps`

| Test | Description |
|------|-------------|
| `test_gaps_ok` | Both categories survive the projection — a weekend closure and a real outage |
| `test_empty_categories_are_not_reported` | A zero count is noise; the reader wants what DID happen |
| `test_missing_report_is_a_404` | No coverage report is a 404, not an empty success |

## TestAtrIndicator — `/brokers/{broker}/symbols/{symbol}/indicators/atr`

| Test | Description |
|------|-------------|
| `test_atr_ok` | A series whose every bar spans exactly 100 smooths to 100, whichever average is used |
| `test_the_response_declares_its_smoothing` | The default is Wilder and the headers say so — a caller cannot tell from the rows |
| `test_a_named_variant_is_reachable_and_declared` | `smoothing=ema` works and is reported back |
| `test_an_unknown_smoothing_is_refused` | An invented name is a 422, not a silent fallback to the default |
| `test_an_out_of_range_period_is_refused_not_clamped` | 400 `invalid_period` — a cap applied behind the caller's back is worse than a refusal |
| `test_an_empty_range_is_a_404` | A range with no bars is a 404, not an empty array |
