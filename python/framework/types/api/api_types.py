"""
API response types for the FiniexTestingIDE HTTP API.

Pydantic models are used here (exception to the project-wide @dataclass rule)
because FastAPI requires Pydantic for automatic OpenAPI schema generation
and response serialization.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class BrokerListResponse(BaseModel):
    brokers: list[str]


class SymbolInfo(BaseModel):
    symbol: str
    market_type: str


class SymbolListResponse(BaseModel):
    symbols: list[SymbolInfo]


class CoverageResponse(BaseModel):
    start: str          # ISO-8601 UTC
    end: str            # ISO-8601 UTC
    timeframes: list[str]


class BarResponse(BaseModel):
    t: int              # unix seconds UTC
    o: float
    h: float
    l: float
    c: float
    v: float


class TimeframeInfo(BaseModel):
    name: str
    minutes: int


class TimeframeListResponse(BaseModel):
    timeframes: list[TimeframeInfo]


class ApiException(Exception):
    """
    Raised by endpoint handlers to produce a structured JSON error response.

    Response body: {"error": "<error>", "detail": "<detail>"}
    """

    def __init__(self, status_code: int, error: str, detail: str):
        self.status_code = status_code
        self.error = error
        self.detail = detail
