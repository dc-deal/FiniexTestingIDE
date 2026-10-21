"""
FiniexTestingIDE - State Frame Types

What an AutoTrader session held and decided at one moment (#579 part A), one line of its
`io/state_frames.jsonl`. Only the STATE — what can only be read off the session by computing it:
the account's value, the open positions, the resting orders, the worker outputs and the decision.
What stays constant for the run is on its routes (header, configuration, broker), and the event
lists are too (trades, order events); a cursor selects from those without the frame.

A frame is taken on a cadence of the canonical clock, when a bar closes, when the decision sends an
order, and once at the session's end. The same rows the routes serve are used for its positions and
orders, so the frame written at shutdown agrees with the report by construction. #611 is to serve
the latest frame as it stands, and #579 part B to record the same frame in the simulation.
"""

from datetime import datetime
from enum import StrEnum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, JsonValue

from python.framework.types.api.report_types import ActiveOrderRow, OpenPositionRow
from python.framework.types.decision_logic_types import DecisionLogicAction

# The version of the stream's line shapes, written into its header
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
