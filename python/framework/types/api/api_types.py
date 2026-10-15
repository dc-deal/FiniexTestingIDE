"""
API response types for the FiniexTestingIDE HTTP API.

Pydantic models are used here (exception to the project-wide @dataclass rule)
because FastAPI requires Pydantic for automatic OpenAPI schema generation
and response serialization.
"""

from typing import Optional

from pydantic import BaseModel

from python.framework.types.config_types.api_auth_config_types import AccountKind
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.coverage_report_types import GapCategory
from python.framework.types.machine_resources_types import LimitSource, PressureLevel


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
    """
    Whether the server answers, and since when — open, so it carries nothing a stranger could use.

    Args:
        status: Always `ok` when it answers at all
        version: The app version
        started_at: When this server process started serving, ISO-8601 UTC — it changes on every
            restart, so a consumer sees a restart instead of guessing at one
        uptime_s: Seconds since then, measured by the server on its monotonic clock, so a
            consumer needs no clock of its own to tell a fresh server from an old one
    """
    status: str
    version: str
    started_at: str
    uptime_s: float


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


class MachineCpuResponse(BaseModel):
    """
    The processors the server's processes may use, and how busy the machine was (#595).

    Args:
        count: How many processors bind — a cgroup quota may be fractional
        count_source: `cgroup` when a quota below the machine's processors binds, else `machine`
        used_pct: How busy the machine's processors were over `window_s`; None when no time has
            passed since the previous reading
        window_s: The seconds since the previous reading of this route, by any caller
    """
    count: float
    count_source: LimitSource
    used_pct: float | None = None
    window_s: float | None = None


class MachineMemoryResponse(BaseModel):
    """
    The memory the server's processes may use, and how much is in use (#595).

    Args:
        limit_bytes: The limit that binds — the container's where it is set below the machine's
        used_bytes: How much of it is in use
        used_pct: The same as a share of the limit
        limit_source: `cgroup` or `machine` — where the limit was read
        warn_at_pct: The share from which `pressure_level` is `warn`
        critical_at_pct: The share from which it is `critical`
    """
    limit_bytes: int
    used_bytes: int
    used_pct: float
    limit_source: LimitSource
    warn_at_pct: float
    critical_at_pct: float


class MachineResourcesResponse(BaseModel):
    """
    What the machine this server runs on has and uses, read at one instant (#595).

    Args:
        measured_at: When it was read, ISO-8601 UTC
        cpu: The processors
        memory: The memory, with the thresholds it is graded against
        pressure_level: `ok`, `warn` or `critical` — graded on memory alone
        pressure_reasons: One sentence for a person per reason; empty when the level is `ok`
    """
    measured_at: str
    cpu: MachineCpuResponse
    memory: MachineMemoryResponse
    pressure_level: PressureLevel
    pressure_reasons: list[str] = []


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


class ValidationCheckRow(BaseModel):
    """
    One validation check, as a reader needs it.

    Args:
        check: The stable id a finding carries (`run-summary.units_absent[].checks`,
            `warnings-errors.warnings[].check`)
        title: A short name, fit for a label or a filter facet
        description: One sentence saying what the check asserts
    """
    check: str
    title: str
    description: str


class ValidationCheckListResponse(BaseModel):
    """Every check a finding can name — the vocabulary behind every `check` id served."""
    key: list[str] = ['check']
    checks: list[ValidationCheckRow]


class IndicatorPointResponse(BaseModel):
    t: int              # unix seconds UTC, bar OPEN time — same basis as BarResponse
    v: float            # the indicator's value at that bar


class GapResponse(BaseModel):
    """
    One interruption in a symbol's archive, with what it was and whose data it lies in.

    A venue outage and a quiet weekend are different facts, and `category` is what keeps
    them apart — the reader never has to infer it from the duration. `origin_class` says whose
    silence it is: in a venue's published history (`external`) nobody of ours was collecting,
    so a gap there is a stretch without trades at the venue, with nothing to re-collect.
    """

    start: str                  # ISO-8601 UTC
    end: str                    # ISO-8601 UTC
    seconds: float
    category: GapCategory
    reason: str
    # The origin class of the tick file the gap lies in or follows; null when no tick file
    # could be found for it
    origin_class: Optional[OriginClass] = None
    # The origin class of the tick file after the gap — it differs from `origin_class` only at
    # a seam, where one origin's data ends and another's begins
    origin_class_after: Optional[OriginClass] = None


class GapYearResponse(BaseModel):
    """The gaps that mean missing data, of one year and one data origin, counted per category."""

    year: int                   # the year a gap STARTED in
    # null for the gaps no tick file could be found for
    origin_class: Optional[OriginClass] = None
    short: int
    moderate: int
    large: int
    hours_without_data: float


class CoverageGapsResponse(BaseModel):
    """
    Coverage span, the gap count per category, the gaps counted per year and origin, and the
    gaps themselves — all of them, or those the filters asked for.
    """

    symbol: str
    broker: str
    start: str                  # ISO-8601 UTC
    end: str                    # ISO-8601 UTC
    gap_counts: dict[str, int]  # per category, over the whole archive
    # Weekend and holiday closures are not counted here: they are a market's expected silence.
    # Always over the whole archive — the filters narrow `gaps` only, so the overview stays whole.
    gaps_by_year: list[GapYearResponse]
    # What makes one row of each list unique. A gap is identified by when it STARTED: two gaps
    # of one symbol cannot open at the same instant, and the category is a classification of
    # the same interruption rather than a second one. A year holds one row per data origin.
    keys: dict[str, list[str]] = {'gaps': ['start'], 'gaps_by_year': ['year', 'origin_class']}
    gaps: list[GapResponse]
