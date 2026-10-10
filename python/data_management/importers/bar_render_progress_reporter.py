"""
FiniexTestingIDE - Bar Render Progress Reporter
Sends a bar-render worker's progress to its parent process, throttled.

A worker runs in its own process, and until #175 the only thing it handed back was its
result at the very end — a render of several minutes was silent in between. The parent
drains these events from a manager queue and shows them under the render step.
"""

import os
import time
from typing import Any, Optional

import psutil

from python.framework.types.import_types.import_progress_types import (
    BarRenderPhase,
    BarRenderProgress,
)

# A worker reports at most this often; a symbol with 810 tick files would otherwise send 810.
PROGRESS_INTERVAL_S = 0.2


class BarRenderProgressReporter:
    """
    Sends one worker's progress for one symbol.

    Args:
        progress_queue: The manager queue the parent drains, or None when nobody listens
        symbol: The symbol the worker renders
        broker_type: Its broker type
    """

    def __init__(self, progress_queue: Optional[Any], symbol: str, broker_type: str):
        self._queue = progress_queue
        self._symbol = symbol
        self._broker_type = broker_type
        self._process = psutil.Process() if progress_queue is not None else None
        self._last_phase: Optional[BarRenderPhase] = None
        self._last_sent = 0.0

    def report(self, phase: BarRenderPhase, done: int, total: int) -> None:
        """
        Send one event, unless the last one is younger than the throttle interval.

        A change of phase and the last unit of a phase are always sent.

        Args:
            phase: What the worker is doing
            done: Units finished in this phase
            total: Units the phase has

        Returns:
            None
        """
        if self._queue is None:
            return
        now = time.monotonic()
        if (phase == self._last_phase and done < total
                and now - self._last_sent < PROGRESS_INTERVAL_S):
            return
        self._last_phase = phase
        self._last_sent = now
        self._queue.put(BarRenderProgress(
            symbol=self._symbol,
            broker_type=self._broker_type,
            worker_pid=os.getpid(),
            phase=phase,
            done=done,
            total=total,
            rss_bytes=self._process.memory_info().rss,
        ))
