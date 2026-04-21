"""
FiniexTestingIDE — HTTP API Application

Read-only FastAPI application exposing tick/bar data over HTTP.
Entry point: python/cli/api_server_cli.py

Endpoints in this module (#297 foundation):
  GET /api/v1/health
  GET /api/v1/brokers

Data endpoints (brokers/symbols/coverage/bars) are added in #298.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.api.api_types import BrokerListResponse, HealthResponse

APP_VERSION = '1.2.1'


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance with CORS and routes registered.
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

    @app.get('/api/v1/health', response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status='ok', version=APP_VERSION)

    @app.get('/api/v1/brokers', response_model=BrokerListResponse)
    def list_brokers() -> BrokerListResponse:
        index = BarsIndexManager()
        index.load_index()
        return BrokerListResponse(brokers=index.list_broker_types())

    return app
