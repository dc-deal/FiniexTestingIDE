"""
The two signals a worker thread gives the AutoTrader's intake (#461): the wake, and the arrivals on
its two lanes — and the producers that give them.
"""

import queue
import threading
from datetime import datetime, timezone

from python.framework.autotrader.tick_sources.kraken_tick_source import KrakenTickSource
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.loop.intake.lane_arrivals import LaneArrivals
from python.framework.loop.intake.loop_wake import LoopWake
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.market_types.market_data_types import TickData

_T0 = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _tick(seconds: int) -> TickData:
    """A replayed tick, `seconds` after the start."""
    return TickData(timestamp=_T0.replace(second=seconds), symbol='BTCUSD', bid=100.0, ask=100.1)


class TestTheWake:
    """The execution lane wakes the loop on the queue it blocks on; the marker is no tick."""

    def test_a_wake_ends_the_loops_wait_on_the_tick_queue(self):
        lane = queue.Queue()
        wake = LoopWake(lane)
        threading.Timer(0.02, wake.notify).start()

        assert wake.is_wake(lane.get(timeout=10.0))

    def test_a_tick_and_the_lanes_end_are_no_wake(self):
        wake = LoopWake(queue.Queue())

        assert not wake.is_wake(_tick(1))
        assert not wake.is_wake(None)


class TestTheLaneArrivals:
    """A lane only records arrivals: the earliest receipt and how many, never what arrived."""

    def test_nothing_arrived_is_none(self):
        assert LaneArrivals().take() is None

    def test_the_earliest_receipt_and_the_count_are_handed_over_once(self):
        arrivals = LaneArrivals()
        arrivals.note(1_000_050)
        arrivals.note(1_000_020)
        arrivals.note(1_000_090)

        earliest, monotonic_s, count = arrivals.take()

        assert (earliest, count) == (1_000_020, 3)
        assert monotonic_s > 0
        assert arrivals.take() is None

    def test_arrivals_from_many_threads_are_all_counted(self):
        arrivals = LaneArrivals()
        threads = [threading.Thread(target=lambda: [arrivals.note(1_000) for _ in range(500)])
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert arrivals.take()[2] == 4_000


class TestTheProducersSignal:
    """A producer tells the loop after it put — never before, so the loop finds what it was told of."""

    def test_an_answer_put_in_the_inbox_is_announced_after_it_lands(self):
        """The processor stamps nothing: the session stamps the arrival on its own axis."""
        processor = LiveRequestProcessor(logger=get_global_logger(), timeout_config=TimeoutConfig())
        heard = []
        processor.set_arrival_listener(lambda: heard.append(processor._http_inbox.qsize()))

        processor._put_inbox('an answer')

        assert heard == [1]

    def test_a_frame_is_one_arrival_announced_after_it_is_put(self):
        ticks = queue.Queue()
        source = KrakenTickSource(symbol='BTCUSD', ws_pair='XBT/USD', tick_queue=ticks,
                                  quote_channel_enabled=False, logger=get_global_logger())
        heard = []
        source.set_arrival_listener(lambda: heard.append(ticks.qsize()))
        frame = [_tick(1), _tick(1), _tick(2)]

        source._emit(frame)

        assert heard == [1]
        assert ticks.get_nowait() == tuple(frame), 'the whole frame, as one item'

    def test_a_replay_announces_each_arrival_and_its_end(self):
        ticks = queue.Queue()
        source = MockTickSource(ticks=[_tick(1), _tick(2)], symbol='BTCUSD', tick_queue=ticks)
        heard = []
        source.set_arrival_listener(lambda: heard.append(ticks.qsize()))

        source.start()

        assert heard == [1, 2, 3]

    def test_a_replay_puts_one_millisecond_as_one_arrival(self):
        """However the loop's reads fall, it never reads one millisecond in two parts."""
        ticks = queue.Queue()
        first, second, later = _tick(1), _tick(1), _tick(2)
        MockTickSource(ticks=[first, second, later], symbol='BTCUSD', tick_queue=ticks).start()

        assert [ticks.get_nowait() for _ in range(3)] == [(first, second), (later,), None]
