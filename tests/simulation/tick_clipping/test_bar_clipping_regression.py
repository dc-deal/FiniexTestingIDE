"""
Bar Rendering Clipping Regression Tests.

Verifies that bar rendering in the simulation tick loop processes ALL ticks —
including ticks flagged as is_clipped=True by the tick processing budget.

The tick processing budget simulates "algo was too slow to react to some ticks".
It does NOT mean the market data feed was incomplete. Therefore bars must
reflect the full tick stream (OHLC, volume, tick_count), while only the algo
path (workers, decision logic) skips clipped ticks.

These tests guard the ordering fix in process_tick_loop.py: Bar Rendering
must sit ABOVE the Clipping Gate (same ordering as AutoTrader tick loop).
"""

import queue
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import ProcessScenarioConfig


TIMEFRAME = 'M5'
SYMBOL = 'BTCUSD'
BAR_START = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _make_tick(
    seconds_offset: int,
    bid: float,
    volume: float,
    is_clipped: bool = False,
) -> TickData:
    """Build a synthetic tick inside a single M5 bar."""
    return TickData(
        timestamp=BAR_START + timedelta(seconds=seconds_offset),
        symbol=SYMBOL,
        bid=bid,
        ask=bid + 1.0,
        volume=volume,
        is_clipped=is_clipped,
    )


def _build_controller() -> BarRenderingController:
    """Real BarRenderingController with M5 timeframe registered."""
    logger = ScenarioLogger(
        scenario_set_name='tick_clipping_test',
        scenario_name='bar_clipping_regression',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    # Bypass worker registration — directly set required timeframes.
    controller._required_timeframes = {TIMEFRAME}
    return controller


def _build_config() -> ProcessScenarioConfig:
    return ProcessScenarioConfig(
        name='bar_clipping_test',
        symbol=SYMBOL,
        scenario_index=0,
        start_time=BAR_START,
        live_stats_config=LiveStatsExportConfig(enabled=False),
    )


def _build_mocks():
    """Build minimal mocks for dependencies unrelated to bar rendering."""
    trade_simulator = MagicMock()
    trade_simulator.portfolio = MagicMock()
    trade_simulator.portfolio.initial_balance = 10000.0

    # Portfolio statistics used by _print_tick_loop_finishing_log — must have
    # numeric fields so the f-string formatting does not blow up on MagicMock.
    portfolio_stats = MagicMock()
    portfolio_stats.total_profit = 0.0
    portfolio_stats.total_loss = 0.0
    portfolio_stats.currency = 'USD'
    trade_simulator.portfolio.get_portfolio_statistics.return_value = portfolio_stats

    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.return_value = MagicMock()

    decision_logic = MagicMock()

    logger = ScenarioLogger(
        scenario_set_name='tick_clipping_test',
        scenario_name='bar_clipping_regression',
        run_timestamp=datetime.now(tz=timezone.utc),
    )

    return trade_simulator, worker_coordinator, decision_logic, logger


def _run_loop(ticks):
    """Run execute_tick_loop with real BarRenderingController + mocks."""
    config = _build_config()
    controller = _build_controller()
    trade_simulator, worker_coordinator, decision_logic, logger = _build_mocks()

    execute_tick_loop(
        config=config,
        worker_coordinator=worker_coordinator,
        trade_simulator=trade_simulator,
        bar_rendering_controller=controller,
        decision_logic=decision_logic,
        scenario_logger=logger,
        ticks=tuple(ticks),
        live_queue=None,
    )

    return controller, worker_coordinator, trade_simulator


# =============================================================================
# TESTS
# =============================================================================

def test_volume_aggregation_includes_clipped_ticks():
    """Bar volume must include volume from clipped ticks.

    Regression guard for #293: if Bar Rendering sits below the Clipping Gate,
    clipped ticks are missed and bar.volume is systematically too low.
    """
    ticks = [
        _make_tick(seconds_offset=0, bid=100.0, volume=1.0, is_clipped=False),
        _make_tick(seconds_offset=10, bid=100.0, volume=2.0, is_clipped=True),
        _make_tick(seconds_offset=20, bid=100.0, volume=3.0, is_clipped=False),
        _make_tick(seconds_offset=30, bid=100.0, volume=4.0, is_clipped=True),
        _make_tick(seconds_offset=40, bid=100.0, volume=5.0, is_clipped=False),
    ]
    expected_total_volume = sum(t.volume for t in ticks)  # 15.0
    expected_tick_count = len(ticks)                       # 5

    controller, worker_coordinator, trade_simulator = _run_loop(ticks)

    current_bar = controller.get_current_bar(SYMBOL, TIMEFRAME)
    assert current_bar is not None, \
        'No bar was constructed — controller did not receive any ticks.'

    assert current_bar.volume == pytest.approx(expected_total_volume), (
        f'Bar volume {current_bar.volume} does not include clipped ticks. '
        f'Expected {expected_total_volume} (sum of ALL tick volumes).'
    )
    assert current_bar.tick_count == expected_tick_count, (
        f'Bar tick_count {current_bar.tick_count} != {expected_tick_count}. '
        f'Clipped ticks were not rendered into the bar.'
    )

    # Sanity: broker path saw all ticks, algo path only non-clipped ticks.
    assert trade_simulator.on_tick.call_count == len(ticks)
    non_clipped = sum(1 for t in ticks if not t.is_clipped)
    assert worker_coordinator.process_tick.call_count == non_clipped


def test_ohlc_reflects_extrema_from_clipped_ticks():
    """Bar high/low must reflect prices from clipped ticks.

    Regression guard for #293: if the extreme price occurs on a clipped tick,
    bar.high / bar.low must still capture it.
    """
    # Highest price is on a clipped tick, lowest is on a non-clipped tick.
    ticks = [
        _make_tick(seconds_offset=0, bid=100.0, volume=1.0, is_clipped=False),
        _make_tick(seconds_offset=10, bid=150.0, volume=1.0, is_clipped=True),
        _make_tick(seconds_offset=20, bid=90.0, volume=1.0, is_clipped=False),
        _make_tick(seconds_offset=30, bid=120.0, volume=1.0, is_clipped=True),
        _make_tick(seconds_offset=40, bid=110.0, volume=1.0, is_clipped=False),
    ]

    controller, _, _ = _run_loop(ticks)

    current_bar = controller.get_current_bar(SYMBOL, TIMEFRAME)
    assert current_bar is not None

    # Mid price = (bid + ask) / 2 = bid + 0.5 (since ask = bid + 1.0)
    expected_high = max(t.mid for t in ticks)       # 150.5
    expected_low = min(t.mid for t in ticks)        # 90.5
    expected_open = ticks[0].mid                    # 100.5 (first tick)
    expected_close = ticks[-1].mid                  # 110.5 (last tick)

    assert current_bar.high == pytest.approx(expected_high), (
        f'Bar high {current_bar.high} != {expected_high}. '
        f'Extreme price from clipped tick was missed.'
    )
    assert current_bar.low == pytest.approx(expected_low), (
        f'Bar low {current_bar.low} != {expected_low}.'
    )
    assert current_bar.open == pytest.approx(expected_open), (
        f'Bar open {current_bar.open} != {expected_open}.'
    )
    assert current_bar.close == pytest.approx(expected_close), (
        f'Bar close {current_bar.close} != {expected_close}.'
    )
    assert current_bar.symbol == SYMBOL
    assert current_bar.timeframe == TIMEFRAME


# =============================================================================
# AUTOTRADER SIDE — Regression guard that the AutoTrader loop keeps the
# correct ordering (Bar Rendering never behind a clipping gate).
# =============================================================================

def _build_autotrader_tick_loop(ticks_with_flags):
    """
    Construct an AutoTraderTickLoop wired to a real BarRenderingController
    and mocks for executor / orchestrator / decision_logic.

    The tick_queue is pre-filled with the given TickData objects followed
    by a sentinel (None) so the loop terminates after the last real tick.
    """
    tick_queue: queue.Queue = queue.Queue()
    for t in ticks_with_flags:
        tick_queue.put(t)
    tick_queue.put(None)  # Sentinel: end of stream

    config = AutoTraderConfig(
        name='bar_clipping_at_test',
        symbol=SYMBOL,
        broker_type='mock',
        adapter_type='mock',
    )
    # Safety disabled by default — do not touch executor.get_balance() etc.
    assert config.safety.enabled is False

    logger = ScenarioLogger(
        scenario_set_name='tick_clipping_test',
        scenario_name='bar_clipping_autotrader_regression',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {TIMEFRAME}

    # Executor mock: only the ctor-time broker.adapter chain needs real values.
    executor = MagicMock()
    symbol_spec = MagicMock()
    symbol_spec.base_currency = 'BTC'
    symbol_spec.quote_currency = 'USD'
    executor.broker.adapter.get_symbol_specification.return_value = symbol_spec

    # execute_decision must return an object whose .is_rejected is False,
    # otherwise the rejection branch triggers attribute access on MagicMock.
    order_result = MagicMock()
    order_result.is_rejected = False
    decision_logic = MagicMock()
    decision_logic.execute_decision.return_value = order_result

    worker_orchestrator = MagicMock()
    worker_orchestrator.process_tick.return_value = MagicMock()

    tick_source = MagicMock()
    tick_source.is_exhausted.return_value = True

    loop = AutotraderTickLoop(
        config=config,
        tick_queue=tick_queue,
        tick_source=tick_source,
        executor=executor,
        bar_controller=controller,
        worker_orchestrator=worker_orchestrator,
        decision_logic=decision_logic,
        clipping_monitor=LiveClippingMonitor(),
        logger=logger,
        trading_model=TradingModel.MARGIN,
        run_dir=None,
        display_queue=None,
        dry_run=True,
    )
    return loop, controller, executor, worker_orchestrator


def test_autotrader_loop_bar_rendering_covers_all_ticks():
    """AutoTrader tick loop must feed every tick through bar rendering.

    Regression guard for #293: guarantees the AutoTrader-side ordering stays
    correct (no clipping gate may ever appear between on_tick and bar
    rendering). The flag `is_clipped` is set here to mirror what a future
    clipping implementation might do — the bar must still aggregate all ticks.
    """
    # time_msc is required for the daily-rotation path and clipping monitor.
    base_msc = int(BAR_START.timestamp() * 1000)
    ticks = []
    prices = [100.0, 150.0, 90.0, 120.0, 110.0]
    clipped = [False, True, False, True, False]
    for i, (p, c) in enumerate(zip(prices, clipped)):
        t = TickData(
            timestamp=BAR_START + timedelta(seconds=i * 10),
            symbol=SYMBOL,
            bid=p,
            ask=p + 1.0,
            volume=1.0,
            time_msc=base_msc + i * 10_000,
            collected_msc=base_msc + i * 10_000,
            is_clipped=c,
        )
        ticks.append(t)

    loop, controller, executor, worker_orchestrator = \
        _build_autotrader_tick_loop(ticks)
    ticks_processed, _ = loop.run()

    assert ticks_processed == len(ticks), (
        f'AutoTrader loop processed {ticks_processed} ticks, '
        f'expected {len(ticks)}.'
    )

    current_bar = controller.get_current_bar(SYMBOL, TIMEFRAME)
    assert current_bar is not None, \
        'AutoTrader loop produced no bar — bar rendering was not reached.'

    # All ticks must be in the bar — AutoTrader has no clipping gate today,
    # and this test guards that invariant.
    assert current_bar.tick_count == len(ticks)
    assert current_bar.volume == pytest.approx(sum(t.volume for t in ticks))

    expected_high = max(t.mid for t in ticks)   # 150.5
    expected_low = min(t.mid for t in ticks)    # 90.5
    expected_open = ticks[0].mid                # 100.5
    expected_close = ticks[-1].mid              # 110.5

    assert current_bar.high == pytest.approx(expected_high)
    assert current_bar.low == pytest.approx(expected_low)
    assert current_bar.open == pytest.approx(expected_open)
    assert current_bar.close == pytest.approx(expected_close)
    assert current_bar.symbol == SYMBOL
    assert current_bar.timeframe == TIMEFRAME

    # Broker path saw every tick (AutoTrader has no clipping gate).
    assert executor.on_tick.call_count == len(ticks)
    assert worker_orchestrator.process_tick.call_count == len(ticks)
