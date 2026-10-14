"""
FiniexTestingIDE - Acceptance Study Phase Types (#332, #358)

The phase protocol every acceptance study shares — a real-money session whose record becomes a
release certificate. A study declares its phases as decision_logic_config, its phase machine turns
them into typed phases and emits one typed PhaseAction per pass, and the decision logic dispatches
that action through the DecisionTradingApi. What a phase DOES is the study's own (its phase type);
how a phase starts, waits, times out and ends is common to all of them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional

from python.framework.types.trading_env_types.order_types import RejectionReason


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
    # #503 — the entry filled; waiting for the venue to confirm the protective order the
    # framework placed for it. A distinct state because a position that is open but not
    # yet protected is exactly the window the feature exists to close.
    AWAIT_PROTECTION = 'await_protection'
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
    SUBMIT_STOP = 'submit_stop'
    CLOSE_ALL = 'close_all'
    CLOSE_PARTIAL = 'close_partial'
    CANCEL = 'cancel'
    CANCEL_ALL = 'cancel_all'
    MODIFY_LIMIT = 'modify_limit'
    END_SESSION = 'end_session'


@dataclass
class StudyPhase:
    """
    What every study's phase carries — the part the shared phase machine reads.

    Args:
        phase_id: Stable identifier (e.g. 'market_long_open')
        phase_type: The study's own kind of phase — each study declares its own set
        enabled: Skip the phase when False
        fill_timeout_s: Wall-clock seconds to wait for an answer before the phase times out
        side: Trade side (LONG/SHORT/NONE)
    """
    phase_id: str
    phase_type: StrEnum
    enabled: bool = True
    fill_timeout_s: float = 30.0
    side: PhaseSide = PhaseSide.NONE


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
        stop_loss: Protective level to DECLARE on the resulting position (#503). The
            framework turns it into an order at the venue where the profile opts in —
            which is the only way a declared level ever leaves this process
        reason: Human-readable narration for logs and the recorder
    """
    kind: PhaseActionKind
    phase_id: str
    side: PhaseSide = PhaseSide.NONE
    lots: Optional[float] = None
    price: Optional[float] = None
    order_id: Optional[str] = None
    close_fraction: Optional[float] = None
    stop_loss: Optional[float] = None
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
        active_stop_count: Resting stop orders right now — a separate world with its own
            cancel and modify paths, so a stop phase cannot read the limit count (#500)
        has_pending: Any order in flight (pipeline / in-flight op)
        filled_since_submit: A fill was observed since the current phase's last submit
        rejected_since_submit: A rejection was observed since the last submit
        cancelled_since_submit: A cancellation was observed since the last submit
        unaccounted_since_submit: An order the framework stopped asking about since the last
            submit — the venue may still hold it, so it is no rejection a phase may wait for
        current_position_lots: Lots of the current open position (None if flat) — partial-close tracking
        budget_ok: Realized session cost is still under the budget ceiling
        refusal_reason: Why the order sent since the last submit was refused — a denial here or a
            rejection by the venue; None while nothing was refused
        suppressed_since_submit: The last submit never left: the safety circuit breaker had
            blocked new entries and turned it into no order at all
        base_holding: How much of the symbol's base the account holds — read only for a study
            whose phases need it, None otherwise
    """
    now: datetime
    mid_price: float
    open_position_count: int
    active_limit_count: int
    active_stop_count: int
    has_pending: bool
    filled_since_submit: bool
    rejected_since_submit: bool
    cancelled_since_submit: bool
    unaccounted_since_submit: bool
    current_position_lots: Optional[float]
    budget_ok: bool
    refusal_reason: Optional[RejectionReason] = None
    suppressed_since_submit: bool = False
    base_holding: Optional[float] = None


@dataclass
class PhaseResult:
    """
    Outcome of a completed phase — consumed by the recorder and the certificate.

    Args:
        phase_id: Phase identifier
        phase_type: The phase kind, in the study's own set
        outcome: PASS / FAIL / SKIPPED / EXPECTED_REJECTION
        reason: Human-readable explanation
        started_utc: When the phase began
        ended_utc: When the phase completed
        rearm_attempts: Re-arm count (LIMIT_OPEN)
    """
    phase_id: str
    phase_type: StrEnum
    outcome: PhaseOutcome
    reason: str
    started_utc: Optional[datetime] = None
    ended_utc: Optional[datetime] = None
    rearm_attempts: int = 0
