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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from python.api.endpoints import bars_router, broker_router
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.api.api_types import ApiException, BrokerListResponse, HealthResponse

APP_VERSION = '1.2.1'


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance with CORS, error handler, and routes registered.
    """
    app = FastAPI(
        title='FiniexTestingIDE API',
        version=APP_VERSION,
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
    )

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={'error': exc.error, 'detail': exc.detail},
        )

    @app.get('/api/v1/health', response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status='ok', version=APP_VERSION)

    @app.get('/api/v1/brokers', response_model=BrokerListResponse)
    def list_brokers() -> BrokerListResponse:
        index = BarsIndexManager()
        index.load_index()
        return BrokerListResponse(brokers=index.list_broker_types())

    app.include_router(broker_router.router, prefix='/api/v1')
    app.include_router(bars_router.router, prefix='/api/v1')

    return app
