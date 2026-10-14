"""
FiniexTestingIDE - Acceptance Study Capture Types (#332, #566, #358)

The lines of an acceptance study's capture: one JSON object per line, a header first. Each line
has a type, and its status draws from a set that depends on that type — a phase's outcome, an
order event, a reconciliation state, a snapshot's flatness. Both are enums so a typo cannot pass
unnoticed; the written JSON carries their values and is unchanged by it. Which set a line's status
comes from is declared once, in STATUS_TYPE_BY_LINE.
"""

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional, Union

from python.framework.types.acceptance_study_types.study_phase_types import PhaseOutcome
from python.framework.types.live_types.reconciliation_types import ReconcileState
from python.framework.types.trading_env_types.order_event_types import (
    OrderEventPlane,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import FeeReport

# The marker phases outside the sequence: the venue's read before trading, and everything written
# once the session ends. A certificate selects the end snapshot by the second, never by position.
PREFLIGHT_PHASE = 'preflight'
SESSION_END_PHASE = 'session_end'


class CaptureLineType(StrEnum):
    """The type of one capture line after the header."""
    PHASE_START = 'phase_start'
    PHASE_RESULT = 'phase_result'
    ORDER_FILLED = 'order_filled'
    PARTIAL_CLOSE = 'partial_close'
    ORDER_REJECTED = 'order_rejected'
    ORDER_CANCELLED = 'order_cancelled'
    ORDER_UNACCOUNTED = 'order_unaccounted'
    RECONCILE_ALERT = 'reconcile_alert'
    BROKER_SNAPSHOT = 'broker_snapshot'
    RECONCILE_SUMMARY = 'reconcile_summary'
    API_PERF = 'api_perf'
    SESSION_END = 'session_end'


class SnapshotStatus(StrEnum):
    """What a `broker_snapshot` line found resting at the venue."""
    FLAT = 'flat'                    # no order rests
    NOT_FLAT = 'not_flat'            # at least one order rests
    UNREAD = 'unread'                # the venue's open orders could not be read


class CaptureCloseReason(StrEnum):
    """Why the capture was closed — the status of its `session_end` line."""
    SESSION_END = 'session end'
    PREFLIGHT_FAILED = 'preflight failed'


class DetectedVia(StrEnum):
    """How the session learned of an order's outcome."""
    POLL = 'poll'                    # the executor asked the venue


# A line's status, in whichever set its type draws from
CaptureStatus = Union[
    PhaseOutcome, OrderEventType, ReconcileState, SnapshotStatus, CaptureCloseReason]

# Which set each line's status comes from. None: the line carries no status. A test holds the map
# to the enum, so a new line type cannot arrive without saying what its status means.
STATUS_TYPE_BY_LINE: Dict[CaptureLineType, Optional[type]] = {
    CaptureLineType.PHASE_START: None,
    CaptureLineType.PHASE_RESULT: PhaseOutcome,
    CaptureLineType.ORDER_FILLED: OrderEventType,
    CaptureLineType.PARTIAL_CLOSE: OrderEventType,
    CaptureLineType.ORDER_REJECTED: OrderEventType,
    CaptureLineType.ORDER_CANCELLED: OrderEventType,
    CaptureLineType.ORDER_UNACCOUNTED: OrderEventType,
    CaptureLineType.RECONCILE_ALERT: ReconcileState,
    CaptureLineType.BROKER_SNAPSHOT: SnapshotStatus,
    CaptureLineType.RECONCILE_SUMMARY: None,
    CaptureLineType.API_PERF: None,
    CaptureLineType.SESSION_END: CaptureCloseReason,
}


@dataclass
class CaptureHeader:
    """
    First JSONL line — describes the run so analysis tools are self-orienting.

    Args:
        schema_version: Recorder schema version
        started_utc: ISO start timestamp
        profile: AutoTrader profile name
        symbol: Traded symbol
        phases: Ordered phase ids in the run
        fee_report: Where the session's venue reports the fee of a fill, as its adapter declared
            it when the capture opened (#508) — a description of the venue; whether a fill should
            have carried the venue's fee is venue_fee_expected's. None where the capture was
            opened without it
        venue_fee_expected: Whether this session's fills should carry the venue's own fee, as
            the executor answered it when the capture opened (#508) — False in a dry run, where
            the venue's declaration still stands but its answers are our simulator's. What the
            certificate judges an estimated fee against. None where the capture was opened
            without it
        record_kind: Constant marker ('header')
    """
    schema_version: str
    started_utc: str
    profile: str
    symbol: str
    phases: List[str]
    fee_report: Optional[FeeReport] = None
    venue_fee_expected: Optional[bool] = None
    record_kind: str = 'header'


@dataclass
class CaptureEvent:
    """
    One JSONL event line.

    The core keys (ts_utc / seq / plane / event_type / phase / phase_index) are always
    present; the remaining fields are populated per event type and omitted when unset, so
    a `jq` one-liner or a pandas load stays trivial. `plane` separates the study's own lines
    from what the venue answered; an order line joins the session's order-event stream by
    `extra.stream_seq`.

    Args:
        ts_utc: ISO event timestamp (UTC)
        seq: Monotonic sequence number within the run
        plane: The bot's own account, or what the venue answered when asked
        event_type: The line's type
        phase: Owning phase id
        phase_index: Owning phase index
        order_id: Internal order/position id
        side: 'LONG' / 'SHORT'
        lots: Order/position size
        price: Relevant price
        status: Terminal/observed status, from the set STATUS_TYPE_BY_LINE names for the type
        detected_via: How the outcome was learned — polling today, a push once #331 lands
        slippage: On a fill, what it is measured against (#566) — order type, trading side,
            the reference and its price; the certificate computes the figure
        reconcile: Reconciliation block — the divergence of a `reconcile_alert`, or the session's
            totals on the `reconcile_summary` line (#151)
        api_perf: Per-endpoint REST telemetry block (#351)
        extra: Event-specific overflow fields
    """
    ts_utc: str
    seq: int
    plane: OrderEventPlane
    event_type: CaptureLineType
    phase: str
    phase_index: int
    order_id: Optional[str] = None
    side: Optional[str] = None
    lots: Optional[float] = None
    price: Optional[float] = None
    status: Optional[CaptureStatus] = None
    detected_via: Optional[DetectedVia] = None
    slippage: Optional[Dict[str, Any]] = None
    reconcile: Optional[Dict[str, Any]] = None
    api_perf: Optional[Dict[str, Any]] = None
    extra: Optional[Dict[str, Any]] = None


def status_fits_line(event_type: CaptureLineType, status: Optional[Enum]) -> bool:
    """
    Whether a status belongs to the set its line type declares.

    Args:
        event_type: The line's type
        status: The status it would carry, or None

    Returns:
        True when the status is absent, or a member of the declared set
    """
    if status is None:
        return True
    expected = STATUS_TYPE_BY_LINE[event_type]
    return expected is not None and isinstance(status, expected)
