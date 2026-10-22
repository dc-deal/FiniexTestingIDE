"""
FiniexTestingIDE - State Frame Types

What an AutoTrader session held and decided at one moment (#579 part A), one line of its
`io/state_frames.jsonl`. Only the STATE — what can only be read off the session by computing it:
the account's value, the open positions, the resting orders, the worker outputs and the decision.
What stays constant for the run is on its routes (header, configuration, broker), and the event
lists are too (trades, order events); a cursor selects from those without the frame.

A frame is taken on a cadence of the canonical clock, when a bar closes, when the decision sends an
order, and once at the session's end. The same rows the routes serve are used for its positions and
orders, so the frame written at shutdown agrees with the report by construction. #611 serves the
latest frame of each unit of a run with its age, and #579 part B is to record the same frame in the
simulation.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, JsonValue

from python.framework.types.api.recording_state_types import RecordingState
from python.framework.types.api.report_types import (
    ActiveOrderRow,
    OpenPositionRow,
    WarningsErrorsOutcome,
)
from python.framework.types.decision_logic_types import DecisionLogicAction

# The stream's name and the version of its line shapes, both written into its header
STATE_FRAMES_STREAM = 'state_frames'
STATE_FRAMES_SCHEMA_VERSION = 1


class StateFrameCause(StrEnum):
    """Why a frame was written."""
    CADENCE = 'cadence'        # the first pass after a cadence boundary of the canonical clock
    BAR_CLOSE = 'bar_close'    # a bar of a timeframe the workers read closed in this pass
    ORDER = 'order'            # the decision sent an order in this pass
    SHUTDOWN = 'shutdown'      # the session's last frame, at the state its report describes


class ProcessMemoryRow(BaseModel):
    """
    What the session's own process held when the frame was written (#611).

    `rss_bytes` counts every resident page, the libraries it shares included; `private_bytes` only
    the pages this process alone holds — what a leak grows. None where the system did not say.
    """
    rss_bytes: Optional[int] = None
    private_bytes: Optional[int] = None


class StateFrame(BaseModel):
    """
    One recorded moment of a session.

    `frame_time` is the canonical clock — in a mock session the replayed tick time — and
    `written_at` the wall clock at the write, which is what an age is measured from. A health the
    session did not check is None, never a default: `feed_stale` without a signal source,
    `reconcile_clean` without a reconciler, before its first cycle and after a cycle that could not
    reach the venue. `process_memory` is on every frame of a live session and on some of a replay's.
    """
    record_kind: Literal['frame'] = 'frame'
    frame_time: datetime
    written_at: datetime
    written_for: StateFrameCause
    cadence_slot: Optional[datetime] = None     # the cadence boundary this frame fills, if any
    ticks_processed: int
    symbol: str
    account_currency: str
    last_price: Optional[float] = None          # the mid the frame is valued at; None before a tick
    balance: float                              # the account currency's balance
    balances: Optional[Dict[str, float]] = None  # every currency held — spot only
    equity: Optional[float] = None              # the account value every risk measure reads
    unrealized_pnl: float = 0.0
    open_positions: List[OpenPositionRow] = []
    active_orders: List[ActiveOrderRow] = []
    safety_blocked: bool = False
    safety_drawdown_pct: Optional[float] = None
    market_stale: bool = False
    feed_stale: Optional[bool] = None
    reconcile_clean: Optional[bool] = None
    # As JSON: a worker may output a structure, not only a number (a probe's warmup status)
    worker_outputs: Dict[str, Dict[str, JsonValue]] = {}
    decision_action: Optional[DecisionLogicAction] = None
    decision_outputs: Dict[str, JsonValue] = {}
    closed_bars: Dict[str, datetime] = {}       # timeframe → the opening of the bar that closed
    order_id: Optional[str] = None              # the order the decision sent, for an order frame
    process_memory: Optional[ProcessMemoryRow] = None


@dataclass(frozen=True)
class StateFrameStreamEnds:
    """
    A unit's state-frame stream at its two ends, as the latest-frame route reads it (#611).

    Args:
        unit_name: The unit the stream belongs to, from its header; None before the header was
            written
        cadence_timeframe: The stream's cadence as a timeframe — one frame per period — from its
            header; None as above
        cadence_s: The stream's cadence in seconds of the canonical clock, from its header; None
            as above
        first: The unit's first frame, or None when it has written none yet
        last: Its latest frame, or None when it has written none yet
        truncated_tail: Whether a last line cut off mid-write was left out
    """
    unit_name: Optional[str]
    cadence_timeframe: Optional[str]
    cadence_s: Optional[float]
    first: Optional[StateFrame]
    last: Optional[StateFrame]
    truncated_tail: bool


class FirstFrameRow(BaseModel):
    """
    A unit's first frame, as far as the check-in needs it: when it was written and what the
    session's process held then — the growth since is one subtraction (#611). Two points are not a
    series: a climb that fell back reads as flat.
    """
    written_at: datetime
    process_memory: Optional[ProcessMemoryRow] = None


class LatestStateFrameUnit(BaseModel):
    """
    One unit's latest state frame, with its age and whether the session still records (#611).

    `age_s` runs from `written_at` to the answer. The session writes and the API server reads — two
    processes — and the wall clock is the one clock both read that also survives a restart of the
    machine; a negative age, a clock stepped back, is None. `recording` is None where it is not
    judged: a session replaying the archive before its end, whose frames come at replay speed.
    `outcome` is the run's end classification exactly as the warnings-errors section serves it,
    once the session has ended and its report phase wrote it.
    """
    unit_name: str
    cadence_timeframe: str
    cadence_s: float
    written_at: datetime
    age_s: Optional[float] = None
    recording: Optional[RecordingState] = None
    truncated_tail: bool = False
    first_frame: Optional[FirstFrameRow] = None
    outcome: Optional[WarningsErrorsOutcome] = None
    frame: StateFrame


class LatestStateFramesResponse(BaseModel):
    """
    The latest state frame of every unit of a run — the check-in on a running session (#611).

    Args:
        key: What makes one unit's row unique
        served_at: When the answer was made, on the API server's wall clock
        units: One row per unit that has written a frame
    """
    key: List[str] = ['unit_name']
    served_at: datetime
    units: List[LatestStateFrameUnit]
