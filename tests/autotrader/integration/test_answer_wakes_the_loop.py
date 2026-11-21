"""
A Venue's Answer Wakes the AutoTrader's Loop on Arrival (#461)

Between two ticks of a throttled replay the loop waits on the tick queue. An answer that lands
there is drained at once when the execution lane wakes the loop and hands an instant of its own,
or one tick gap later, by the next tick, when nothing wakes it. The session's outcome is the same
either way, and an unthrottled replay is taken whole before any answer arrives, so only a
throttled one tells the two apart: every answer is timed from its put in the inbox to its take.
"""

import json
import queue
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from tests.shared.fixture_helpers import remove_run_dir

PROFILE = 'configs/autotrader_profiles/mock/tp_triggered_test.json'
TICK_GAP_MS = 50
REPLAY_TICKS = 40   # the probe enters at tick 10; its answer lands while the replay still runs


class _TimedInbox(queue.Queue):
    """The request processor's inbox, timing each answer from its put to its take."""

    def __init__(self) -> None:
        super().__init__()
        self._put_at: Dict[int, float] = {}
        self._waits_ms: List[float] = []

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None) -> None:
        """
        Note when an answer was put, then put it.

        Args:
            item: The answer
            block: As queue.Queue.put
            timeout: As queue.Queue.put
        """
        self._put_at[id(item)] = time.monotonic()
        super().put(item, block, timeout)

    def get_nowait(self) -> Any:
        """
        Take an answer and record how long it waited.

        Returns:
            The answer
        """
        item = super().get_nowait()
        put_at = self._put_at.pop(id(item), None)
        if put_at is not None:
            self._waits_ms.append((time.monotonic() - put_at) * 1000)
        return item

    def get_waits_ms(self) -> List[float]:
        """
        Returns:
            How long each answer taken so far waited, in ms
        """
        return list(self._waits_ms)


def test_a_venue_answer_wakes_the_loop_between_ticks(tmp_path, monkeypatch):
    """The answer reaches the execution lane and is drained well inside one tick gap."""
    inboxes: List[_TimedInbox] = []
    build_processor = LiveRequestProcessor.__init__

    def build_with_timed_inbox(self, *args, **kwargs):
        build_processor(self, *args, **kwargs)
        self._http_inbox = _TimedInbox()
        inboxes.append(self._http_inbox)

    monkeypatch.setattr(LiveRequestProcessor, '__init__', build_with_timed_inbox)
    profile = json.loads(Path(PROFILE).read_text())
    profile['tick_source']['tick_delay_ms'] = TICK_GAP_MS
    profile['scenario_settings']['max_ticks'] = REPLAY_TICKS
    profile_path = tmp_path / 'throttled_replay.json'
    profile_path.write_text(json.dumps(profile))

    trader = AutotraderMain(load_autotrader_config(str(profile_path)))
    result = trader.run()
    remove_run_dir(trader.get_run_dir())

    assert result.intake_stats is not None and result.intake_stats.execution_arrivals > 0, (
        f'no answer reached the execution lane in a throttled replay: {result.intake_stats} — '
        f'the executor is not told where to report an arrival')
    waits_ms = inboxes[0].get_waits_ms()
    assert waits_ms, 'the probe placed no order, so nothing was timed'
    assert max(waits_ms) < TICK_GAP_MS / 2, (
        f'answers waited {[round(wait, 1) for wait in waits_ms]} ms for a loop that should have '
        f'woken at once; a tick comes every {TICK_GAP_MS} ms')
