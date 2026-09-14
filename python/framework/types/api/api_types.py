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
    t: int              # unix seconds UTC, bar OPEN time
    o: float
    h: float
    l: float
    c: float
    v: float            # traded volume; 0.0 on feeds that carry none (forex CFD)
    tc: int             # ticks aggregated into this bar


class TimeframeInfo(BaseModel):
    name: str
    minutes: int


class TimeframeListResponse(BaseModel):
    timeframes: list[TimeframeInfo]


class IndicatorPointResponse(BaseModel):
    t: int              # unix seconds UTC, bar OPEN time — same basis as BarResponse
    v: float            # the indicator's value at that bar


class GapResponse(BaseModel):
    """
    One interruption in a symbol's archive, with what it was.

    A venue outage and a quiet weekend are different facts, and `category` is what keeps
    them apart — the reader never has to infer it from the duration.
    """

    start: str                  # ISO-8601 UTC
    end: str                    # ISO-8601 UTC
    seconds: float
    category: str               # seamless | weekend | holiday | short | moderate | large
    reason: str


class CoverageGapsResponse(BaseModel):
    """Coverage span plus every gap inside it, categorised."""

    symbol: str
    broker: str
    start: str                  # ISO-8601 UTC
    end: str                    # ISO-8601 UTC
    gap_counts: dict[str, int]  # per category
    gaps: list[GapResponse]
