"""
FiniexTestingIDE - Live Field Study Types (#332)

Runtime domain types for the LiveFieldStudy decision logic (CORE) and its phase
state machine. Phases are declared as decision_logic_config (parsed from a list of
dicts) and turned into typed FieldStudyPhase objects; the machine emits a typed
PhaseAction per tick that the decision logic dispatches via DecisionTradingApi.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional


class PhaseType(StrEnum):
    """The kind of operation a phase exercises."""
    MARKET_OPEN = 'market_open'
    MARKET_CLOSE_ALL = 'market_close_all'
    LIMIT_OPEN = 'limit_open'
    LIMIT_MODIFY = 'limit_modify'
    LIMIT_CANCEL = 'limit_cancel'
    MULTI_LIMIT = 'multi_limit'
    MULTI_CANCEL = 'multi_cancel'
    PARTIAL_CLOSE = 'partial_close'
    IDLE = 'idle'
    FORCE_CLOSE = 'force_close'
    FINAL_SUMMARY = 'final_summary'


class PhaseSide(StrEnum):
    """Trade side for a phase (NONE for side-agnostic phases like idle/close-all)."""
    LONG = 'long'
    SHORT = 'short'
    NONE = 'none'


class PhaseState(StrEnum):
    """Per-phase state-machine position."""
    PENDING = 'pending'              # not started yet
    SUBMIT = 'submit'                # ready to submit the (next) order
    AWAIT_FILL = 'await_fill'        # order submitted, waiting for fill/reject/timeout
    POST_FILL_WAIT = 'post_fill_wait'  # settle window after a fill
    CLOSE = 'close'                  # ready to close / cancel
    POST_CLOSE_WAIT = 'post_close_wait'  # settle window after a close/cancel
    DONE = 'done'                    # finished (see outcome)
    SKIPPED = 'skipped'              # disabled or adapter-incompatible


class PhaseOutcome(StrEnum):
    """Result of a completed phase (drives the certificate PASS/FAIL)."""
    PENDING = 'pending'
    PASS = 'pass'
    FAIL = 'fail'
    SKIPPED = 'skipped'
    EXPECTED_REJECTION = 'expected_rejection'
    INCONCLUSIVE = 'inconclusive'  # market-dependent non-fill — not a mechanical fail; non-pass-gating


class PhaseActionKind(StrEnum):
    """The concrete operation the machine asks the decision logic to perform this tick."""
    NONE = 'none'                    # nothing to do this tick (waiting)
    SUBMIT_MARKET = 'submit_market'
    SUBMIT_LIMIT = 'submit_limit'
    CLOSE_ALL = 'close_all'
    CLOSE_PARTIAL = 'close_partial'
    CANCEL = 'cancel'
    CANCEL_ALL = 'cancel_all'
    MODIFY_LIMIT = 'modify_limit'
    END_SESSION = 'end_session'


@dataclass
class FieldStudyPhase:
    """
    One phase of the Field Study sequence (typed view of a config dict).

    Args:
        phase_id: Stable identifier (e.g. 'market_long_open')
        phase_type: The operation kind
        side: Trade side (LONG/SHORT/NONE)
        enabled: Skip the phase when False
        expect_rejection: The submit is expected to be rejected (spot SHORT, sub-min lot)
        lots: Order size (None → use the decision logic's default lot_size)
        limit_offset_pct: How far the resting limit sits from the market (fraction, e.g. 0.002)
        order_count: Number of orders for MULTI_LIMIT
        fill_timeout_s: Wall-clock seconds to wait for a fill before timeout/re-arm/skip
        post_wait_s: Settle window after a fill / close before advancing
        idle_seconds: Quiet-period duration for IDLE phases
        rearm: Re-arm-until-fill for LIMIT_OPEN (re-price toward market on timeout)
    """
    phase_id: str
    phase_type: PhaseType
    side: PhaseSide = PhaseSide.NONE
    enabled: bool = True
    expect_rejection: bool = False
    lots: Optional[float] = None
    limit_offset_pct: Optional[float] = None
    order_count: int = 1
    fill_timeout_s: float = 30.0
    post_wait_s: float = 2.0
    idle_seconds: float = 20.0
    rearm: bool = False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> 'FieldStudyPhase':
        """
        Build a FieldStudyPhase from a raw config dict.

        Args:
            raw: Phase spec from the profile's phase_sequence

        Returns:
            Typed FieldStudyPhase with defaults applied
        """
        return cls(
            phase_id=raw['phase_id'],
            phase_type=PhaseType(raw['phase_type']),
            side=PhaseSide(raw.get('side', PhaseSide.NONE.value)),
            enabled=bool(raw.get('enabled', True)),
            expect_rejection=bool(raw.get('expect_rejection', False)),
            lots=raw.get('lots'),
            limit_offset_pct=raw.get('limit_offset_pct'),
            order_count=int(raw.get('order_count', 1)),
            fill_timeout_s=float(raw.get('fill_timeout_s', 30.0)),
            post_wait_s=float(raw.get('post_wait_s', 2.0)),
            idle_seconds=float(raw.get('idle_seconds', 20.0)),
            rearm=bool(raw.get('rearm', False)),
        )


@dataclass
class PhaseAction:
    """
    The operation the phase machine asks the decision logic to perform this tick.

    Args:
        kind: What to do (submit/close/cancel/modify/end-session/none)
        phase_id: Owning phase id
        side: Trade side for submit actions
        lots: Order size for submit/partial-close
        price: Limit price for SUBMIT_LIMIT / MODIFY_LIMIT
        order_id: Target order id for CANCEL / MODIFY_LIMIT
        close_fraction: Fraction of a position to close for CLOSE_PARTIAL (0..1)
        reason: Human-readable narration for logs and the recorder
    """
    kind: PhaseActionKind
    phase_id: str
    side: PhaseSide = PhaseSide.NONE
    lots: Optional[float] = None
    price: Optional[float] = None
    order_id: Optional[str] = None
    close_fraction: Optional[float] = None
    reason: str = ''


@dataclass
class PhaseContext:
    """
    Per-tick observation the decision logic feeds into the phase machine.

    The machine is a pure state machine driven by these observations — it does
    no I/O and tracks no order ids itself; the decision logic owns broker access
    and order-id bookkeeping.

    Args:
        now: Current tick wall-clock time (UTC)
        mid_price: Current mid price (limit pricing reference)
        open_position_count: Open positions right now (shadow state)
        active_limit_count: Resting limit orders right now
        has_pending: Any order in flight (pipeline / in-flight op)
        filled_since_submit: A fill was observed since the current phase's last submit
        rejected_since_submit: A rejection was observed since the last submit
        cancelled_since_submit: A cancellation was observed since the last submit
        current_position_lots: Lots of the current open position (None if flat) — partial-close tracking
        budget_ok: Realized session cost is still under the budget ceiling
    """
    now: datetime
    mid_price: float
    open_position_count: int
    active_limit_count: int
    has_pending: bool
    filled_since_submit: bool
    rejected_since_submit: bool
    cancelled_since_submit: bool
    current_position_lots: Optional[float]
    budget_ok: bool


@dataclass
class PhaseResult:
    """
    Outcome of a completed phase — consumed by the recorder and the certificate.

    Args:
        phase_id: Phase identifier
        phase_type: The phase kind
        outcome: PASS / FAIL / SKIPPED / EXPECTED_REJECTION
        reason: Human-readable explanation
        started_utc: When the phase began
        ended_utc: When the phase completed
        rearm_attempts: Re-arm count (LIMIT_OPEN)
    """
    phase_id: str
    phase_type: PhaseType
    outcome: PhaseOutcome
    reason: str
    started_utc: Optional[datetime] = None
    ended_utc: Optional[datetime] = None
    rearm_attempts: int = 0


@dataclass
class FieldStudyHeader:
    """
    First JSONL line — describes the run so analysis tools are self-orienting.

    Args:
        schema_version: Recorder schema version
        started_utc: ISO start timestamp
        profile: AutoTrader profile name
        symbol: Traded symbol
        release_target: Release version the run certifies (or 'dev')
        phases: Ordered phase ids in the run
        record_kind: Constant marker ('header')
    """
    schema_version: str
    started_utc: str
    profile: str
    symbol: str
    release_target: str
    phases: List[str]
    record_kind: str = 'header'


@dataclass
class FieldStudyEvent:
    """
    One JSONL event line.

    The core keys (ts_utc / seq / plane / event_type / phase / phase_index) are always
    present; the remaining fields are populated per event type and omitted when unset, so
    a `jq` one-liner or a pandas load stays trivial. `plane` separates bot-observed events
    from broker-truth snapshots; the two join on phase + order_id.

    Args:
        ts_utc: ISO event timestamp (UTC)
        seq: Monotonic sequence number within the run
        plane: 'bot' (bot-observed) or 'broker_truth' (pulled from the broker)
        event_type: Event kind (phase_start / order_filled / broker_snapshot / ...)
        phase: Owning phase id
        phase_index: Owning phase index
        order_id: Internal order/position id
        broker_ref: Broker order reference
        side: 'LONG' / 'SHORT'
        lots: Order/position size
        price: Relevant price
        status: Terminal/observed status
        detected_via: 'poll' today, 'push' once #331 lands
        slippage: Submission slippage block (#340)
        reconcile: Reconciliation block (#151)
        api_perf: Per-endpoint REST telemetry block (#351)
        cost: Realized-cost block
        extra: Event-specific overflow fields
    """
    ts_utc: str
    seq: int
    plane: str
    event_type: str
    phase: str
    phase_index: int
    order_id: Optional[str] = None
    broker_ref: Optional[str] = None
    side: Optional[str] = None
    lots: Optional[float] = None
    price: Optional[float] = None
    status: Optional[str] = None
    detected_via: Optional[str] = None
    slippage: Optional[Dict[str, Any]] = None
    reconcile: Optional[Dict[str, Any]] = None
    api_perf: Optional[Dict[str, Any]] = None
    cost: Optional[Dict[str, Any]] = None
    extra: Optional[Dict[str, Any]] = None
