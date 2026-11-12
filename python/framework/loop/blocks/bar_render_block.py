"""
FiniexTestingIDE - Bar Render Block

The loop core's step that renders the bars of every tick (#458). Moved unchanged from the tick
pipeline core both runners shared since #303: one implementation, so the two loops cannot order
bar rendering and the clipping gate differently (the #293 bug class).
"""

from typing import Dict

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.market_types.market_data_types import TickData


def render_bars_for_tick(tick: TickData, bar_controller: BarRenderingController) -> Dict:
    """
    Step 2 — render bars for EVERY tick, before any gate.

    Bars must reflect the complete market data stream — including ticks the
    algo path skips. Clipping simulates "algo was too slow to react", NOT
    "market data was incomplete", so this must run before the simulation
    clipping gate. Same ordering in both pipelines.

    Args:
        tick: Current tick
        bar_controller: Bar rendering controller

    Returns:
        current_bars dict from bar_controller.process_tick
    """
    return bar_controller.process_tick(tick)
