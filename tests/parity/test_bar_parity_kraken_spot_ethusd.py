"""
Bar Parity Test — Kraken Spot ETHUSD

Verifies that the simulation pipeline (execute_tick_loop) and the AutoTrader
pipeline (AutotraderTickLoop.run()) produce identical M1 bars and trades when
fed the same deterministic tick stream.

Phase 2 — Bar parity: 1000 synthetic ticks at 1 tick/second yield 16 complete
M1 bars. Every 10th tick is pre-flagged as is_clipped=True (#293 regression guard).

Phase 3 — Trade parity: flat-price ticks eliminate the 1-tick fill-timing
asymmetry between pipelines.
"""

import queue
from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.core.backtesting.backtesting_deterministic import BacktestingDeterministic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.types.trading_env_types.broker_types import BrokerType

from tests.shared.parity_comparators import assert_bars_equal, assert_portfolio_equal, assert_trades_equal
from tests.shared.parity_fixtures import flag_clipped_ticks, make_flat_ethusd_ticks, make_synthetic_ethusd_ticks


SYMBOL = 'ETHUSD'
TIMEFRAME = 'M1'

# Phase 3 — trade parity parameters
_TRADE_OPEN_TICK = 200
_TRADE_HOLD_TICKS = 500       # closes at tick 700
_TRADE_LOTS = 0.01
_TRADE_LOGIC_CONFIG = {
    'trade_sequence': [
        {
            'tick_number': _TRADE_OPEN_TICK,
            'direction': 'LONG',
            'hold_ticks': _TRADE_HOLD_TICKS,
            'lot_size': _TRADE_LOTS,
        }
    ],
    'lot_size': _TRADE_LOTS,
    'modify_sequence': [],
    'modify_limit_sequence': [],
    'modify_stop_sequence': [],
    'cancel_limit_sequence': [],
    'cancel_stop_sequence': [],
}


def _run_simulation(ticks):
    """Run execute_tick_loop in-process and return the bar controller."""
    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name='bar_parity_kraken_spot_ethusd_sim',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {TIMEFRAME}

    config = ProcessScenarioConfig(
        name='bar_parity_kraken_spot_ethusd',
        symbol=SYMBOL,
        scenario_index=0,
        start_time=ticks[0].timestamp,
        live_stats_config=LiveStatsExportConfig(enabled=False),
    )

    trade_simulator = MagicMock()
    trade_simulator.portfolio = MagicMock()
    trade_simulator.portfolio.initial_balance = 10000.0
    portfolio_stats = MagicMock()
    portfolio_stats.total_profit = 0.0
    portfolio_stats.total_loss = 0.0
    portfolio_stats.currency = 'USD'
    trade_simulator.portfolio.get_portfolio_statistics.return_value = portfolio_stats

    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.return_value = MagicMock()

    decision_logic = MagicMock()

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
    return controller


def _run_autotrader(ticks):
    """Run AutotraderTickLoop in-process and return the bar controller."""
    tick_queue: queue.Queue = queue.Queue()
    for t in ticks:
        tick_queue.put(t)
    tick_queue.put(None)

    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name='bar_parity_kraken_spot_ethusd_at',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {TIMEFRAME}

    config = AutoTraderConfig(
        name='bar_parity_kraken_spot_ethusd',
        symbol=SYMBOL,
        broker_type='mock',
        adapter_type='mock',
    )

    executor = MagicMock()
    symbol_spec = MagicMock()
    symbol_spec.base_currency = 'ETH'
    symbol_spec.quote_currency = 'USD'
    executor.broker.adapter.get_symbol_specification.return_value = symbol_spec

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
        trading_model=TradingModel.SPOT,
        run_dir=None,
        display_queue=None,
        dry_run=True,
    )
    loop.run()
    return controller


# =============================================================================
# PHASE 3 — TRADE PARITY HELPERS
# =============================================================================

def _build_mock_broker_config() -> BrokerConfig:
    """Minimal BrokerConfig backed by MockBrokerAdapter (ETHUSD spec built-in)."""
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    return BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)


def _wire_decision_logic(executor, logger) -> BacktestingDeterministic:
    """Create a BacktestingDeterministic instance wired to the given executor."""
    logic = BacktestingDeterministic(
        name='trade_parity_logic',
        logger=logger,
        config=_TRADE_LOGIC_CONFIG,
        trading_context=None,
    )
    required_types = logic.get_required_order_types(_TRADE_LOGIC_CONFIG)
    api = DecisionTradingApi(
        executor=executor,
        required_order_types=required_types,
        order_guard_config=None,
    )
    logic.set_trading_api(api)
    return logic


def _run_simulation_trades(ticks):
    """Run execute_tick_loop with real TradeSimulator and BacktestingDeterministic."""
    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name='trade_parity_kraken_spot_ethusd_sim',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {TIMEFRAME}

    config = ProcessScenarioConfig(
        name='trade_parity_kraken_spot_ethusd',
        symbol=SYMBOL,
        scenario_index=0,
        start_time=ticks[0].timestamp,
        live_stats_config=LiveStatsExportConfig(enabled=False),
    )

    sim_executor = TradeSimulator(
        broker_config=_build_mock_broker_config(),
        initial_balance=10000.0,
        account_currency='USD',
        logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
        spot_mode=True,
        initial_balances={'USD': 10000.0, 'ETH': 0.0},
    )

    decision_logic = _wire_decision_logic(sim_executor, logger)

    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.side_effect = (
        lambda tick, current_bars, bar_history: decision_logic.compute(tick, {})
    )

    execute_tick_loop(
        config=config,
        worker_coordinator=worker_coordinator,
        trade_simulator=sim_executor,
        bar_rendering_controller=controller,
        decision_logic=decision_logic,
        scenario_logger=logger,
        ticks=tuple(ticks),
        live_queue=None,
    )
    return controller, sim_executor


def _run_autotrader_trades(ticks):
    """Run AutotraderTickLoop with real LiveTradeExecutor and BacktestingDeterministic."""
    tick_queue: queue.Queue = queue.Queue()
    for t in ticks:
        tick_queue.put(t)
    tick_queue.put(None)

    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name='trade_parity_kraken_spot_ethusd_at',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {TIMEFRAME}

    config = AutoTraderConfig(
        name='trade_parity_kraken_spot_ethusd',
        symbol=SYMBOL,
        broker_type='mock',
        adapter_type='mock',
    )

    at_executor = LiveTradeExecutor(
        broker_config=_build_mock_broker_config(),
        initial_balance=10000.0,
        account_currency='USD',
        logger=logger,
        spot_mode=True,
        initial_balances={'USD': 10000.0, 'ETH': 0.0},
    )

    decision_logic = _wire_decision_logic(at_executor, logger)

    worker_orchestrator = MagicMock()
    worker_orchestrator.process_tick.side_effect = (
        lambda tick, current_bars, bar_history: decision_logic.compute(tick, {})
    )

    tick_source = MagicMock()
    tick_source.is_exhausted.return_value = True

    loop = AutotraderTickLoop(
        config=config,
        tick_queue=tick_queue,
        tick_source=tick_source,
        executor=at_executor,
        bar_controller=controller,
        worker_orchestrator=worker_orchestrator,
        decision_logic=decision_logic,
        clipping_monitor=LiveClippingMonitor(),
        logger=logger,
        trading_model=TradingModel.SPOT,
        run_dir=None,
        display_queue=None,
        dry_run=True,
    )
    loop.run()
    return controller, at_executor


# =============================================================================
# TESTS
# =============================================================================

def test_bar_parity_kraken_spot_ethusd():
    """Simulation and AutoTrader produce identical M1 bars for ETHUSD under identical input.

    Uses 1000 deterministic synthetic ticks (seeded RNG). Every 10th tick is
    pre-flagged as is_clipped=True (#293 regression guard).
    """
    ticks = make_synthetic_ethusd_ticks(count=1000)
    ticks = flag_clipped_ticks(ticks, every_n=10)

    sim_controller = _run_simulation(ticks)
    at_controller = _run_autotrader(ticks)

    assert_bars_equal(sim_controller, at_controller, SYMBOL, TIMEFRAME)


def test_trade_parity_kraken_spot_ethusd():
    """Both pipelines produce identical trades and portfolio stats for ETHUSD.

    Uses 1000 flat-price ticks (bid=3500, ask=3503.5) to eliminate the 1-tick
    fill-timing asymmetry between pipelines.

    BacktestingDeterministic opens LONG at tick 200, closes at tick 700 (hold 500).
    """
    ticks = make_flat_ethusd_ticks(count=1000)

    sim_controller, sim_executor = _run_simulation_trades(ticks)
    at_controller, at_executor = _run_autotrader_trades(ticks)

    assert_bars_equal(sim_controller, at_controller, SYMBOL, TIMEFRAME)
    assert_trades_equal(
        sim_executor.get_trade_history(),
        at_executor.get_trade_history(),
    )
    assert_portfolio_equal(
        sim_executor.portfolio.get_portfolio_statistics(),
        at_executor.portfolio.get_portfolio_statistics(),
    )
