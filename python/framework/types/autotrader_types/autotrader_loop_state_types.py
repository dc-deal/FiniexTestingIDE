"""
FiniexTestingIDE - AutoTrader Loop State
What the units of an AutoTrader session's event loop share while it runs.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import TickData


@dataclass
class AutotraderLoopState:
    """
    The running state of an AutoTrader session that more than one unit of its event loop reads
    (#461): one instance, written where its value changes — the loop counts the ticks and records
    the last tick the algo ran on, the pass hooks note the tick span — and read by the others.

    Args:
        ticks_processed: The ticks the session's broker path has seen
        ticks_clipped: Of them, the ticks the algo did not compute on, as the clipping monitor's
            periodic reports counted them
        tick_passes: The instants that carried ticks — the hard stop's drain window counts these
        first_tick_time: The first tick's venue stamp; None before the first tick
        last_tick_time: The latest tick's venue stamp; None before the first tick
        last_real_tick: The newest tick of the last slice the algo ran on; None before it ran
        last_real_decision: The decision the algo returned then; None before it ran
        last_real_tick_wall_time: When that was, on the wall clock — read back only as an age;
            0.0 before the first slice
        loop_started_monotonic: When the event loop started, on the monotonic clock; 0.0 before
    """
    ticks_processed: int = 0
    ticks_clipped: int = 0
    tick_passes: int = 0
    first_tick_time: Optional[datetime] = None
    last_tick_time: Optional[datetime] = None
    last_real_tick: Optional[TickData] = None
    last_real_decision: Optional[Decision] = None
    last_real_tick_wall_time: float = 0.0
    loop_started_monotonic: float = 0.0
