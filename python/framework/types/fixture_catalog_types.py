"""
Fixture catalog types (#576) — the runs a consumer pins, as declared data.

An entry names what produces it, the properties its run must carry, and who consumes it. A
production is the record of one time the entry was produced: which runs it made and whether
they carried every property. The CURRENT fixture of an entry is derived from those records —
the newest verified production — and is never written anywhere.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

from python.framework.types.api.report_types import (
    DeploymentBookingPeriodsResponse,
    DeploymentDetailResponse,
    DeploymentSummary,
    OrderHistoryReport,
    PortfolioReport,
    RunInfo,
    RunSummary,
    SweepDetailResponse,
    TradeHistoryReport,
    WarningsErrorsReport,
)


class FixtureProducerKind(StrEnum):
    """
    What produces an entry.

    SCENARIO_SET: one backtest of a scenario set
    PROFILE: one AutoTrader session of a profile
    SESSION_SEQUENCE: several AutoTrader sessions of one bot, each with its own changes — a
        deployment with a history
    SWEEP: a parameter sweep over a scenario set
    """
    SCENARIO_SET = 'scenario_set'
    PROFILE = 'profile'
    SESSION_SEQUENCE = 'session_sequence'
    SWEEP = 'sweep'


@dataclass(frozen=True)
class FixtureSession:
    """
    One session of a session sequence, as declared data.

    Args:
        label: What the session is there to show, printed while it runs
        start_date: The replay window it starts at — each session replays its OWN day, so the
            booking periods of a sequence do not stack on one another
        overrides: Values set on the base profile, by dotted path
            (`strategy_config.decision_logic_config.rsi_oversold`)
        new_deployment: Start a fresh deployment history with this session
        kill_after_seconds: Kill the session after this long, so it never reaches its close —
            the way a run with a header and no ledger row is produced
    """
    label: str
    start_date: str
    overrides: Dict[str, Any] = field(default_factory=dict)
    new_deployment: bool = False
    kill_after_seconds: Optional[float] = None


@dataclass
class FixtureEvidence:
    """
    What one production of an entry is checked against, read through the same readers the API
    serves from.

    Args:
        runs: Every run the production made, with what the ledger says each did
        order_history: Run id → its order history
        trade_history: Run id → its trade history
        run_summary: Run id → its run summary
        warnings_errors: Run id → its warnings and errors
        portfolio: Run id → its portfolio
        deployment_rows: The production's deployments as the deployment list serves them
        deployments: Deployment id → its detail
        deployment_periods: Deployment id → its booking periods
        sweeps: Sweep id → its detail
    """
    runs: List[RunInfo] = field(default_factory=list)
    order_history: Dict[str, OrderHistoryReport] = field(default_factory=dict)
    trade_history: Dict[str, TradeHistoryReport] = field(default_factory=dict)
    run_summary: Dict[str, RunSummary] = field(default_factory=dict)
    warnings_errors: Dict[str, WarningsErrorsReport] = field(default_factory=dict)
    portfolio: Dict[str, PortfolioReport] = field(default_factory=dict)
    deployment_rows: List[DeploymentSummary] = field(default_factory=list)
    deployments: Dict[str, DeploymentDetailResponse] = field(default_factory=dict)
    deployment_periods: Dict[str, DeploymentBookingPeriodsResponse] = field(default_factory=dict)
    sweeps: Dict[str, SweepDetailResponse] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureProperty:
    """
    One property an entry's production must carry.

    Args:
        property_id: Its identity, unique within the entry
        sentence: What it asserts, in one sentence a consumer can read
        check: Whether the evidence carries it
    """
    property_id: str
    sentence: str
    check: Callable[[FixtureEvidence], bool]


@dataclass(frozen=True)
class FixtureEntry:
    """
    One entry of the fixture catalog.

    Args:
        entry_id: Its identity — what `produce` is given
        title: What its runs show, in one sentence
        producer: What produces it
        source: The configuration it starts from, relative to the project root — the scenario
            set, the profile, the sweep specification, or the base profile of a session sequence
        properties: What every production must carry
        consumers: Who relies on it
        sessions: The sessions of a session sequence, in order; empty for every other producer
        session_profile_name: The profile name every session of a sequence runs under — one bot
            across all of them; empty for every other producer
        session_bot_id: The bot id every session of a sequence declares; empty otherwise
        session_max_ticks: How much of each session's replay to consume; 0 otherwise
    """
    entry_id: str
    title: str
    producer: FixtureProducerKind
    source: str
    properties: Tuple[FixtureProperty, ...]
    consumers: Tuple[str, ...]
    sessions: Tuple[FixtureSession, ...] = ()
    session_profile_name: str = ''
    session_bot_id: str = ''
    session_max_ticks: int = 0


class FixtureProduction(BaseModel):
    """
    One time an entry was produced — a line of the production record.

    Args:
        entry_id: The entry produced
        produced_at: When the production finished, ISO-8601 UTC
        run_ids: Every run it made
        deployment_ids: The deployments its sessions belong to; empty for a backtest
        sweep_ids: The sweeps it ran; empty otherwise
        verified: Whether every property held
        failed_properties: The properties that did not hold
        report_contract: The API contract its runs' reports were written under — which code
            made them is on each run's own header
    """
    entry_id: str
    produced_at: str
    run_ids: List[str]
    deployment_ids: List[str] = []
    sweep_ids: List[str] = []
    verified: bool
    failed_properties: List[str] = []
    report_contract: int
