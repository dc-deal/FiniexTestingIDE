"""
FiniexTestingIDE — HTTP API Application

Read-only FastAPI application over the data and the records this project already writes: market
data by venue, a run's persisted report sections, a sweep's ranked combinations, a live bot's
history across its restarts. Entry point: python/cli/api_server_cli.py

The routes are NOT listed here. They are one table in docs/architecture/api_server_architecture.md,
which says for each what it serves and what it deliberately does not — a second list beside it
would be the copy nobody updates. `ROUTER_SURFACES` below is the authoritative mount table.
"""

import textwrap
import time
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from python.api.api_auth_setup import setup_api_auth
from python.api.api_contract import API_CONTRACT_VERSION, CHANGES, CONTRACT_HEADER
from python.api.api_error_catalog import IDENTITY_UNBOUND, api_error
from python.api.head_request_middleware import HeadRequestMiddleware
from python.api.endpoints import (
    bars_router,
    broker_router,
    deployments_router,
    directory_router,
    reports_router,
    sweeps_router,
)
from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.types.api.api_identity_types import ApiConsumerIdentity
from python.framework.types.api.api_types import (
    ApiContractResponse,
    BrokerListResponse,
    CallerResponse,
    HealthResponse,
    TimeframeInfo,
    TimeframeListResponse,
    ValidationCheckListResponse,
    ValidationCheckRow,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS

# Every gated router and the surface its grants name. The surface is the ROUTER's name, and a
# grant names the thing a route addresses — its first path parameter. So `bars:kraken_spot` is
# one venue's bar data, while a run report is addressed by a generated id nobody would write
# into a token and `reports:*` is the realistic grant there.
#
# Declared at module level rather than inside the factory because it is the half of a PAIR: the
# other half is `ConsumerToken.GRANT_SURFACES`, the closed vocabulary a grant is parsed against.
# A test holds the two to the same set, so a router mounted under a surface no token can name —
# and a surface no router serves — fails there instead of becoming a denial nobody can explain.
ROUTER_SURFACES = (
    (broker_router.router, 'brokers'),
    (bars_router.router, 'bars'),
    (deployments_router.router, 'deployments'),
    (directory_router.router, 'directory'),
    (reports_router.router, 'reports'),
    (sweeps_router.router, 'sweeps'),
)

# How much of one CHANGES line the boot overview shows: the route and the gist. The full
# sentence is what `/api/v1/contract` serves.
_CHANGE_PREVIEW_CHARS = 96


def _describe_caller(request: Request, enforced: bool,
                     identities: Dict[str, ApiConsumerIdentity]) -> CallerResponse:
    """
    Say who the server takes this request's caller to be.

    The bearer check puts the verified consumer's name on `request.state.consumer`. While gating
    is off that check is not mounted, so nothing is there, and the answer names nobody, even
    when the caller sent a valid token.

    Args:
        request: The request being answered
        enforced: Whether the bearer check is mounted
        identities: Consumer name → identity, as the boot bound them

    Returns:
        The caller's client, account and grants, or just the gating state while it is off
    """
    consumer = getattr(request.state, 'consumer', None)
    if consumer is None:
        return CallerResponse(enforced=enforced)
    identity = identities.get(consumer)
    if identity is None:
        # The boot refuses a live token without an account, so a verified consumer with no
        # identity is a defect on this side. Answering it as an anonymous caller would hide it.
        raise api_error(IDENTITY_UNBOUND, consumer=consumer)
    return CallerResponse(
        enforced=enforced,
        client=identity.consumer,
        account=identity.account.account_id,
        account_kind=identity.account.kind,
        display_name=identity.account.display_name,
        grants=list(identity.grants),
        note=identity.note,
    )


def _describe_the_api(app: FastAPI, app_version: str) -> List[str]:
    """
    The console overview printed once every route is mounted: which contract this process
    serves, what moved into it, and how many routes each surface carries.

    Args:
        app: The fully mounted application
        app_version: The app version from the configuration

    Returns:
        The lines to print — the routes COUNTED from the app, never taken from a list
    """
    # Counted from the ROUTERS the mount loop includes, not from `app.routes`: FastAPI keeps an
    # included router there as one wrapper object, so `app.routes` lists only the routes the
    # factory mounts itself — health, contract and the other app-level reads.
    top_level = sum(isinstance(route, APIRoute) and route.path.startswith('/api/v1')
                    for route in app.routes)
    per_surface = [(surface, sum(isinstance(route, APIRoute) for route in router.routes))
                   for router, surface in ROUTER_SURFACES]
    total = top_level + sum(count for _, count in per_surface)
    surfaces = ' · '.join(f'{surface} {count}' for surface, count in per_surface)

    lines = [
        f'📜 API contract {API_CONTRACT_VERSION} · app {app_version} · '
        f'{total} routes under /api/v1',
        f'   per surface: {surfaces} · top-level {top_level}',
        f'   moved into contract {API_CONTRACT_VERSION} ({len(CHANGES)}), '
        f'in full at GET /api/v1/contract:',
    ]
    lines.extend(
        f'     · {textwrap.shorten(change, width=_CHANGE_PREVIEW_CHARS, placeholder=" …")}'
        for change in CHANGES)
    return lines


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance with CORS, error handler, and routes registered.
    """
    app_version = AppConfigManager().get_version()
    # When THIS process started serving — a provenance stamp, so the wall clock is right for it
    # (§9) — and the monotonic reading the uptime is measured from, so a clock step cannot make
    # the server look younger or older than it is.
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    auth = setup_api_auth()
    print(auth.boot_line)

    # Every protected router carries the bearer check, and it is mounted on the ROUTER rather
    # than on its routes: a route added later inherits it by construction, so the failure this
    # exists to prevent — an endpoint shipped unprotected because somebody forgot — cannot be
    # reached by forgetting. Empty while no consumer is configured (the scaffold state).
    guarded = [Depends(auth.bearer)] if auth.bearer is not None else []

    # The schema and its two browsers are DEVELOPMENT surfaces, and they are switched off the
    # moment a consumer is configured. Three properties make this the seam rather than a
    # nicety. FastAPI mounts them at the APP ROOT, outside `/api/v1`, so a reverse proxy
    # scoped to the versioned prefix does not reach them either way. Authentication is
    # attached per ROUTER (below), which cannot cover a route the framework mounts itself.
    # And the walk that proves no identity route is ungated filters on a path parameter, so a
    # parameterless root route is outside it BY CONSTRUCTION — it reports nothing because it
    # never looks. Gating them instead of removing them would not work: Swagger UI fetches the
    # schema from the browser with no bearer, so a gated `/docs` is a broken `/docs`.
    interactive = auth.bearer is None
    app = FastAPI(
        title='FiniexTestingIDE API',
        version=app_version,
        description='Read-only HTTP interface for tick and bar data.',
        openapi_url='/openapi.json' if interactive else None,
        docs_url='/docs' if interactive else None,
        redoc_url='/redoc' if interactive else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            'http://localhost:5173',
            'http://127.0.0.1:5173',
            'http://localhost:8000',
            'http://127.0.0.1:8000',
        ],
        allow_methods=['GET', 'HEAD'],
        allow_headers=['*'],
        # A browser hides every response header that is not CORS-safelisted, so without this
        # a cross-origin client sees the STATUS of a 401 or 429 and neither the scheme to
        # retry with nor how long to wait. Measured by the package's author against their own
        # client; invisible from here, because our other consumer is server-side.
        expose_headers=['WWW-Authenticate', 'Retry-After', CONTRACT_HEADER],
    )
    # Every GET route answers HEAD too, the way HTTP expects — a consumer reading only the
    # contract header asks with HEAD, and FastAPI alone refuses that with a 405.
    app.add_middleware(HeadRequestMiddleware)

    @app.middleware('http')
    async def stamp_the_contract(request: Request, call_next):
        """
        Put the contract version on EVERY response, including the refusals.

        On every response because that is what makes a saved fixture self-describing: a
        consumer records our answer as a mock, and the number it was captured under travels
        with it. Their test then asserts locally, with no connection — which is the whole
        reason this is a header rather than only a route.
        """
        response = await call_next(request)
        response.headers[CONTRACT_HEADER] = str(API_CONTRACT_VERSION)
        return response

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={'error': exc.error, 'detail': exc.detail},
            headers=exc.headers,
        )

    @app.get('/api/v1/health', response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status='ok', version=app_version,
                              started_at=started_at.isoformat(),
                              uptime_s=round(time.monotonic() - started_monotonic, 1))

    # Open beside /health, decided rather than inherited: a timeframe list is the app's own
    # static configuration, not data about a venue or a run, and it is none of the
    # surfaces a grant can name. Gating it would make a market-data grant the precondition for
    # a list that reveals nothing about market data.
    # Open beside /health for the same reason: a consumer has to be able to ask which contract
    # they are talking to BEFORE they hold a token, or a version mismatch and a credential
    # failure look alike from outside.
    @app.get('/api/v1/contract', response_model=ApiContractResponse)
    def api_contract() -> ApiContractResponse:
        return ApiContractResponse(
            contract=API_CONTRACT_VERSION, app_version=app_version, changes=CHANGES)

    @app.get('/api/v1/timeframes', response_model=TimeframeListResponse)
    def list_timeframes() -> TimeframeListResponse:
        return TimeframeListResponse(timeframes=[
            TimeframeInfo(name=tf, minutes=TimeframeConfig.get_minutes(tf))
            for tf in TimeframeConfig.sorted()
        ])

    # Open beside /timeframes for the same reason: the check vocabulary is the app's own static
    # declaration — what an id MEANS — and says nothing about a venue, a run or an account.
    @app.get('/api/v1/validation-checks', response_model=ValidationCheckListResponse)
    def list_validation_checks() -> ValidationCheckListResponse:
        return ValidationCheckListResponse(checks=[
            ValidationCheckRow(check=info.check, title=info.title, description=info.description)
            for info in VALIDATION_CHECKS
        ])

    @app.get('/api/v1/brokers', response_model=BrokerListResponse,
             dependencies=guarded)
    def list_brokers() -> BrokerListResponse:
        index = BarsIndexManager()
        index.load_index()
        return BrokerListResponse(brokers=index.list_broker_types())

    # Token-only like /brokers, and for the same reason it takes no grant: it is about the
    # CALLER, and there is no path parameter for a grant to name. Deliberately not in
    # ROUTER_SURFACES — a surface here would be a grant a token needs in order to ask what it
    # holds.
    @app.get('/api/v1/caller', response_model=CallerResponse, dependencies=guarded)
    def caller(request: Request) -> CallerResponse:
        return _describe_caller(request, auth.bearer is not None, auth.identities)

    for router, surface in ROUTER_SURFACES:
        app.include_router(
            router,
            prefix='/api/v1',
            dependencies=guarded + [Security(auth.grant, scopes=[surface])],
        )

    print('\n'.join(_describe_the_api(app, app_version)))
    return app
