"""
FiniexTestingIDE - Loop Pass Record (#458)

What a pass of the loop core leaves for the runner that drives it.
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional

from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import FormingBar


@dataclass(slots=True)
class LoopPassRecord:
    """
    What the loop core's passes leave for the runner that drives them, kept for a whole run.

    One per scenario or session, written by every pass. The runner reads it after a pass for the
    steps it runs in its own order — the state frame, the live export — and after the run for how
    far it got. Made once, so a pass allocates nothing for it.

    Args:
        ticks_applied: How many ticks have reached the broker path, counted right after each did,
            so a pass that fails later in its course has already counted its tick
        current_bars: The forming bars after the last tick, by timeframe
        closed_timeframes: The timeframes whose bar the last tick closed — read only when the run
            records state frames or indicator series, and empty otherwise
        decision: The last tick pass's decision
        order_result: What the last tick pass's decision sent, if anything
    """
    ticks_applied: int = 0
    current_bars: Dict[str, FormingBar] = field(default_factory=dict)
    closed_timeframes: FrozenSet[str] = frozenset()
    decision: Optional[Decision] = None
    order_result: Optional[AlgoOrderResult] = None
