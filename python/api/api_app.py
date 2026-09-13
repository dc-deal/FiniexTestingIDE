"""
FiniexTestingIDE — HTTP API Application

Read-only FastAPI application exposing tick/bar data over HTTP.
Entry point: python/cli/api_server_cli.py

Foundation endpoints (#297):
  GET /api/v1/health
  GET /api/v1/brokers

Data endpoints (#298):
  GET /api/v1/brokers/{broker}/symbols
  GET /api/v1/brokers/{broker}/symbols/{symbol}/coverage
  GET /api/v1/brokers/{broker}/symbols/{symbol}/bars
"""

from fastapi import Depends, FastAPI, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from python.api.api_auth_setup import setup_api_auth
from python.api.endpoints import bars_router, broker_router, reports_router, sweeps_router
from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.exceptions.api_errors import ApiException
from python.framework.types.api.api_types import (
    BrokerListResponse,
    HealthResponse,
    TimeframeInfo,
    TimeframeListResponse,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance with CORS, error handler, and routes registered.
    """
    app_version = AppConfigManager().get_version()
    auth = setup_api_auth()
    print(auth.boot_line)

    # Every protected router carries the bearer check, and it is mounted on the ROUTER rather
    # than on its routes: a route added later inherits it by construction, so the failure this
    # exists to prevent — an endpoint shipped unprotected because somebody forgot — cannot be
    # reached by forgetting. Empty while no consumer is configured (the scaffold state).
    guarded = [Depends(auth.bearer)] if auth.bearer is not None else []

    app = FastAPI(
        title='FiniexTestingIDE API',
        version=app_version,
        description='Read-only HTTP interface for tick and bar data.',
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            'http://localhost:5173',
            'http://127.0.0.1:5173',
            'http://localhost:8000',
            'http://127.0.0.1:8000',
        ],
        allow_methods=['GET'],
        allow_headers=['*'],
        # A browser hides every response header that is not CORS-safelisted, so without this
        # a cross-origin client sees the STATUS of a 401 or 429 and neither the scheme to
        # retry with nor how long to wait. Measured by the package's author against their own
        # client; invisible from here, because our other consumer is server-side.
        expose_headers=['WWW-Authenticate', 'Retry-After'],
    )

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={'error': exc.error, 'detail': exc.detail},
            headers=exc.headers,
        )

    @app.get('/api/v1/health', response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status='ok', version=app_version)

    # Open beside /health, decided rather than inherited: a timeframe list is the app's own
    # static configuration, not data about a venue or a run, and it is none of the four
    # surfaces a grant can name. Gating it would make a market-data grant the precondition for
    # a list that reveals nothing about market data.
    @app.get('/api/v1/timeframes', response_model=TimeframeListResponse)
    def list_timeframes() -> TimeframeListResponse:
        return TimeframeListResponse(timeframes=[
            TimeframeInfo(name=tf, minutes=TimeframeConfig.get_minutes(tf))
            for tf in TimeframeConfig.sorted()
        ])

    @app.get('/api/v1/brokers', response_model=BrokerListResponse,
             dependencies=guarded)
    def list_brokers() -> BrokerListResponse:
        index = BarsIndexManager()
        index.load_index()
        return BrokerListResponse(brokers=index.list_broker_types())

    # The surface is the ROUTER's name, and a grant names the thing a route addresses — its
    # first path parameter. So `bars:kraken_spot` is one venue's bar data, while a run report
    # is addressed by a generated id nobody would write into a token and `reports:*` is the
    # realistic grant there.
    for router, surface in (
        (broker_router.router, 'brokers'),
        (bars_router.router, 'bars'),
        (reports_router.router, 'reports'),
        (sweeps_router.router, 'sweeps'),
    ):
        app.include_router(
            router,
            prefix='/api/v1',
            dependencies=guarded + [Security(auth.grant, scopes=[surface])],
        )

    return app
