"""
LiveTradeExecutor — How a Session's Instant Resolves What the Venue Answered (#461)

The session hands its executor one instant at a time: the clock moves, every tick of the slice
applies, then what the venue answered resolves once. That resolution has two forms. After a tick it
is the tick's own order processing, which asks the venue synchronously about a MARKET order or a
close in transit — so a market fill the venue already made is booked in the instant of the tick, as
before the timeline. Without a tick — a heartbeat, a venue's answer alone, a timer — it is the
heartbeat's work: the drain, the timeouts, the re-poll of resting orders.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.log_level import LogLevel
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.booking_day_harness import LOTS, SYMBOL, live_executor

_T0 = datetime(2026, 1, 25, 12, 0, 0, tzinfo=timezone.utc)


def _msc(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


@pytest.fixture
def executor():
    built = live_executor(spot_mode=False)
    yield built
    built.get_request_processor().stop_worker(0.0)


def _route(executor: LiveTradeExecutor, monkeypatch) -> List[str]:
    calls: List[str] = []
    monkeypatch.setattr(executor, '_process_pending_orders', lambda: calls.append('tick'))
    monkeypatch.setattr(executor, 'heartbeat', lambda: calls.append('heartbeat'))
    return calls


class TestTheTwoFormsOfResolution:

    def test_after_a_tick_the_instant_resolves_by_the_ticks_own_processing(
            self, executor, monkeypatch):
        calls = _route(executor, monkeypatch)
        executor.begin_instant(_msc(_T0))
        executor.apply_tick(TickData(timestamp=_T0, symbol='BTCUSD', bid=50000.0, ask=50001.0))

        executor.resolve_due(_msc(_T0))

        assert calls == ['tick']

    def test_without_a_tick_the_instant_resolves_by_the_heartbeats_work(
            self, executor, monkeypatch):
        calls = _route(executor, monkeypatch)
        executor.begin_instant(_msc(_T0))
        executor.apply_tick(TickData(timestamp=_T0, symbol='BTCUSD', bid=50000.0, ask=50001.0))
        executor.resolve_due(_msc(_T0))
        later = _T0 + timedelta(milliseconds=500)

        executor.begin_instant(_msc(later))
        executor.resolve_due(_msc(later))

        assert calls == ['tick', 'heartbeat'], 'the tick of the instant before does not count'

    def test_the_instant_sets_the_clock_to_its_stamp(self, executor):
        executor.begin_instant(_msc(_T0))

        assert executor.get_current_time() == _T0


class TestAFailingArrivalListener:
    """
    The arrival listener runs on the request processor's worker thread. A listener that fails
    costs the wake, never the worker: the answer already lies in the inbox, and a worker that died
    would leave every later write unsent while the session reports success.
    """

    def test_it_costs_the_wake_not_the_worker(self):
        logger = ScenarioLogger(
            scenario_set_name='arrival_listener', scenario_name='fails',
            run_timestamp=_T0, run_id=mint_run_id(_T0))
        executor = live_executor(spot_mode=False, logger=logger)
        processor = executor.get_request_processor()
        try:
            executor.begin_instant(_msc(_T0))
            executor.apply_tick(TickData(timestamp=_T0, symbol=SYMBOL, bid=50000.0, ask=50001.0))
            executor.resolve_due(_msc(_T0))

            def listener_bug() -> None:
                raise RuntimeError('listener bug')

            executor.set_arrival_listener(listener_bug)
            for _ in range(2):
                executor.open_order(OpenOrderRequest(
                    symbol=SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
                    lots=LOTS))
                assert processor.flush_outbox(2.0), 'the worker died on the answer before'
            later = _T0 + timedelta(seconds=1)
            executor.begin_instant(_msc(later))
            executor.apply_tick(TickData(timestamp=later, symbol=SYMBOL, bid=50000.0, ask=50001.0))
            executor.resolve_due(_msc(later))

            assert len(executor.get_open_positions()) == 2
            said = [record.message for record in logger.get_records(LogLevel.ERROR)
                    if 'could not be told' in record.message]
            assert len(said) == 1, 'into the error pot, and said once'
        finally:
            processor.stop_worker(0.0)

