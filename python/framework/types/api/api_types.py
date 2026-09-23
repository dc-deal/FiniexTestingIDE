"""
API response types for the FiniexTestingIDE HTTP API.

Pydantic models are used here (exception to the project-wide @dataclass rule)
because FastAPI requires Pydantic for automatic OpenAPI schema generation
and response serialization.
"""

from pydantic import BaseModel


class ApiContractResponse(BaseModel):
    """
    Which CONTRACT this server serves, beside which app version happens to be running.

    The two move on different clocks: the app version changes every release, the contract only
    when a route or a response model does. A consumer records `contract` with its fixtures and
    asserts it at start-up; `changes` says what moved into the current one, so they can decide
    whether they care without reading a repository they do not have.

    Deliberately NOT a deprecation channel — this project ships no compatibility layers (§27),
    so the honest offer is a number to compare, not a promise that the old shape still works.
    """
    contract: int
    app_version: str
    changes: list[str] = []


class HealthResponse(BaseModel):
    status: str
    version: str


class BrokerListResponse(BaseModel):
    brokers: list[str]


class SymbolInfo(BaseModel):
    symbol: str
    market_type: str


class SymbolListResponse(BaseModel):
    key: list[str] = ['symbol']
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
    # What makes one row unique (§49) — every served list says so, and a consumer can
    # assert it rather than read it.
    key: list[str] = ['name']
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
    # A gap is identified by when it STARTED: two gaps of one symbol cannot open at the
    # same instant, and the category is a classification of the same interruption rather
    # than a second one.
    key: list[str] = ['start']
    gaps: list[GapResponse]
