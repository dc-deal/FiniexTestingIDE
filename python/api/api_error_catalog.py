"""
The API's error vocabulary — every answer that is not a success, declared once.

Each entry pairs an HTTP status with the CAUSE a consumer renders and the sentence it reads. Before
this catalog the codes were string literals at the raising line: the same `run_not_found` sentence
stood in two routes, the same `invalid_limit` in two more, and eight different absences on the
bars and broker routes shared one bare `not_found` — a code that tells a consumer a status it
already has and nothing it can act on (contract 6).

A route raises `api_error(KIND, **values)`, never an `ApiException` with a literal code. The
authentication answers are the one exception, and a deliberate one: their codes belong to the
shared `finiex_auth` package and reach the API through the error factory in `api_auth_setup.py`.

Held by `tests/framework/api/test_api_error_catalog.py`: every code is unique, every entry is
raised somewhere, no route raises a literal, and the error table in
`docs/architecture/api_server_architecture.md` lists exactly these codes.
"""

from typing import Tuple

from python.framework.exceptions.api_errors import ApiException
from python.framework.types.api.api_error_types import ApiErrorKind

# --- Reports: a run and its sections ---------------------------------------------------------

RUN_NOT_FOUND = ApiErrorKind(404, 'run_not_found', "No run '{run_id}' in the run index")
REPORTS_NOT_COMMISSIONED = ApiErrorKind(
    404, 'reports_not_commissioned',
    "Run '{run_id}' was started without report artifacts (reporting: none)")
RUN_NOT_COMPLETED = ApiErrorKind(
    404, 'run_not_completed',
    "Run '{run_id}' has no report artifacts yet — it is still running, or it ended before its "
    "report phase; from here the two look the same")
ARTIFACT_NOT_PRODUCED = ApiErrorKind(
    404, 'artifact_not_produced',
    "Run '{run_id}' persisted {artifact_count} report artifact(s) but no {section} — its "
    "pipeline does not write this section, or its outcome left nothing to write. The run "
    "list's `artifacts` names what it has")
ARTIFACT_UNREADABLE = ApiErrorKind(409, 'artifact_unreadable', '{reason}')
CONFIG_SNAPSHOT_MISSING = ApiErrorKind(
    404, 'config_snapshot_missing',
    "Run '{run_id}' declares a configuration snapshot that was never filed")
INVALID_TIMESTAMP = ApiErrorKind(
    400, 'invalid_timestamp', "'{field}' must be ISO-8601, got '{value}'")

# --- Market data: brokers, symbols, bars, coverage -------------------------------------------

BROKER_NOT_FOUND = ApiErrorKind(404, 'broker_not_found', "No broker '{broker}' in the bar index")
SYMBOL_NOT_FOUND = ApiErrorKind(
    404, 'symbol_not_found', "No symbol '{symbol}' for broker '{broker}' in the bar index")
NO_BARS_INDEXED = ApiErrorKind(
    404, 'no_bars_indexed', "The bar index lists '{broker}/{symbol}' but holds no bars for it")
TIMEFRAME_NOT_RENDERED = ApiErrorKind(
    404, 'timeframe_not_rendered', "No bars for '{broker}/{symbol}' at timeframe '{timeframe}'")
COVERAGE_REPORT_UNAVAILABLE = ApiErrorKind(
    404, 'coverage_report_unavailable',
    "No coverage report for '{broker}/{symbol}' — the coverage cache holds none for it yet")
NO_BARS_IN_RANGE = ApiErrorKind(
    404, 'no_bars_in_range', "No bars for '{broker}/{symbol}' in the requested range")
INVALID_TIMEFRAME = ApiErrorKind(
    400, 'invalid_timeframe', "Timeframe '{timeframe}' is not valid. Valid: {valid}")
INVALID_LIMIT = ApiErrorKind(
    400, 'invalid_limit', "'limit' must be between 1 and {maximum}, got {limit}.")
INVALID_PERIOD = ApiErrorKind(
    400, 'invalid_period', "'period' must be between 1 and {maximum}, got {period}.")
INVALID_RANGE = ApiErrorKind(400, 'invalid_range', "'from' must be earlier than 'to'.")
MARKET_TYPE_NOT_CONFIGURED = ApiErrorKind(
    500, 'market_type_not_configured', "No market_type configured for broker '{broker}'")

# --- Ledger: deployments and sweeps ----------------------------------------------------------

DEPLOYMENT_NOT_FOUND = ApiErrorKind(
    404, 'deployment_not_found', "No deployment '{deployment_id}' in the run-results ledger")
SWEEP_NOT_FOUND = ApiErrorKind(
    404, 'sweep_not_found', "No sweep '{sweep_id}' in the run-results ledger")

# --- Identity ---------------------------------------------------------------------------------

IDENTITY_UNBOUND = ApiErrorKind(
    500, 'identity_unbound',
    'Consumer {consumer!r} was authenticated but is bound to no account.')

# Every entry above, for the completeness test and the documentation table.
API_ERRORS: Tuple[ApiErrorKind, ...] = (
    RUN_NOT_FOUND, REPORTS_NOT_COMMISSIONED, RUN_NOT_COMPLETED, ARTIFACT_NOT_PRODUCED,
    ARTIFACT_UNREADABLE, CONFIG_SNAPSHOT_MISSING, INVALID_TIMESTAMP,
    BROKER_NOT_FOUND, SYMBOL_NOT_FOUND, NO_BARS_INDEXED, TIMEFRAME_NOT_RENDERED,
    COVERAGE_REPORT_UNAVAILABLE, NO_BARS_IN_RANGE, INVALID_TIMEFRAME, INVALID_LIMIT,
    INVALID_PERIOD, INVALID_RANGE, MARKET_TYPE_NOT_CONFIGURED,
    DEPLOYMENT_NOT_FOUND, SWEEP_NOT_FOUND,
    IDENTITY_UNBOUND,
)


def api_error(kind: ApiErrorKind, **values: object) -> ApiException:
    """
    The exception a route raises for one catalog entry.

    Args:
        kind: The catalog entry
        values: The placeholders its message names

    Returns:
        The ApiException carrying the entry's status, code and filled sentence
    """
    return ApiException(kind.status, kind.code, kind.message.format(**values))
