"""
API response types for the FiniexTestingIDE HTTP API.

Pydantic models are used here (exception to the project-wide @dataclass rule)
because FastAPI requires Pydantic for automatic OpenAPI schema generation
and response serialization.
"""

from pydantic import BaseModel

from python.framework.types.config_types.api_auth_config_types import AccountKind


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


class CallerResponse(BaseModel):
    """
    Who the server takes the caller to be — which no other route tells them (#551).

    A token says which CLIENT is calling; the account says on whose BEHALF — a person, or a
    service such as a sibling project. Until a login exists, presenting a token IS acting as
    its account, so this answer is also what a run started through the API would record.

    `enforced` keeps two states apart that look the same from a request. While gating is off
    nothing verifies a presented token, so every identity field is null even for a caller that
    sent a valid one — a 200 here is not the token being accepted. While gating is on, a caller
    without a valid token never gets this answer: it is refused with 401.

    Args:
        enforced: Whether the bearer check is mounted — the server's gating state, never the
            caller's
        client: The consumer the token authenticates as; null while gating is off
        account: The account id the client acts for — a run started through the API would
            record it as its `person`
        account_kind: `person` or `service`
        display_name: The account's human-readable name
        grants: What the token may reach, as `<surface>:<name>` entries; empty while gating is off
        note: The token's own note — who holds it
    """
    enforced: bool
    client: str | None = None
    account: str | None = None
    account_kind: AccountKind | None = None
    display_name: str | None = None
    grants: list[str] = []
    note: str | None = None


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
