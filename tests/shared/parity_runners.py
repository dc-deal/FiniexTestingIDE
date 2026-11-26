"""
FiniexTestingIDE - The Two Pipelines Run Over a Tick List, for Bar Parity

Both event loops, run in-process with every collaborator but the bar renderer stubbed, so a parity
test can feed each side its ticks and compare the bars the two renderers produce.
"""

import queue
from datetime import datetime, timezone
from typing import List
from unittest.mock import MagicMock

from python.framework.autotrader.event_loop.autotrader_event_loop import AutotraderEventLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_event_loop import execute_event_loop
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_types.market_data_types import TickData, arrival_slices
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.utils.run_id_utils import mint_run_id


def run_simulation_bars(ticks: List[TickData], symbol: str, timeframe: str = 'M1') -> BarRenderingController:
    """
    Run the backtest's event loop in-process over a tick list and return its bar controller.

    Args:
        ticks: The ticks, in the order the backtest reads them
        symbol: The symbol of the kraken_spot market they belong to
        timeframe: The timeframe rendered

    Returns:
        The bar controller, holding the rendered bars
    """
    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name=f'bar_parity_kraken_spot_{symbol.lower()}_sim',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {timeframe}

    config = ProcessScenarioConfig(
        name=f'bar_parity_kraken_spot_{symbol.lower()}',
        symbol=symbol,
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
    # The loop asks the canonical clock on every pass since #537 — the booking period's
    # boundary is an instant, and a MagicMock cannot be compared with a date. The stub answers
    # with the first tick's time, which is what a real executor holds before the first on_tick.
    trade_simulator.get_current_time.return_value = ticks[0].timestamp
    trade_simulator.portfolio.sample_equity.return_value = None
    # The backtest's timeline asks for the next due time after every instant (#460); nothing
    # is in flight here
    trade_simulator.peek_next_due_msc.return_value = None
    trade_simulator.is_session_end_requested.return_value = False

    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.return_value = MagicMock()

    decision_logic = MagicMock()

    execute_event_loop(
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


def run_autotrader_bars(ticks: List[TickData], symbol: str, base_currency: str,
                        timeframe: str = 'M1') -> BarRenderingController:
    """
    Run the AutoTrader's event loop in-process over a tick list and return its bar controller.

    Args:
        ticks: The ticks, in the order they arrive
        symbol: The symbol of the kraken_spot market they belong to
        base_currency: The symbol's base asset, as the venue's specification names it
        timeframe: The timeframe rendered

    Returns:
        The bar controller, holding the rendered bars
    """
    tick_queue: queue.Queue = queue.Queue()
    for arrival in arrival_slices(ticks):
        tick_queue.put(arrival)
    tick_queue.put(None)  # Sentinel: signals end of stream

    logger = ScenarioLogger(
        scenario_set_name='parity',
        scenario_name=f'bar_parity_kraken_spot_{symbol.lower()}_at',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {timeframe}

    config = AutoTraderConfig(
        profile_name=f'bar_parity_kraken_spot_{symbol.lower()}',
        symbol=symbol,
        # The VENUE, not the tick source: `adapter_type='mock'` is what makes this a mock
        # session, while broker_type names the market whose rules apply — and since #476 the
        # event loop resolves this market's trading-day anchor at construction, so a broker
        # market_config.json does not know is refused rather than silently defaulted.
        broker_type='kraken_spot',
        adapter_type='mock',
    )

    executor = MagicMock()
    symbol_spec = MagicMock()
    symbol_spec.base_currency = base_currency
    symbol_spec.quote_currency = 'USD'
    executor.broker.adapter.get_symbol_specification.return_value = symbol_spec
    executor.portfolio.get_spot_equity.return_value = 1000.0
    # The loop does ARITHMETIC with these two on every tick (#356): it measures the account
    # value against the risk baseline whether the circuit breaker is armed or not, so a bare
    # MagicMock reaches a comparison and raises. Same reason the two stubs around it exist —
    # a mock executor that answers a number where the loop needs a number.
    executor.portfolio.get_account_value.return_value = 1000.0
    executor.portfolio.initial_balance = 1000.0
    # Same reason as the stubs around it: since #537 the loop asks the canonical clock on every
    # pass to see whether the booking period's boundary was crossed, and a bare MagicMock
    # cannot be compared with a date. None is what a real executor answers before its first
    # tick, and it is what tells the recorder there is no instant to open a period at yet.
    executor.get_current_time_if_set.return_value = None
    # No session-end request in this fixture — a bare MagicMock would return a
    # truthy mock and break the loop after the first tick (#348).
    executor.is_session_end_requested.return_value = False

    order_result = MagicMock()
    order_result.is_refused = False
    decision_logic = MagicMock()
    decision_logic.execute_decision.return_value = order_result
    decision_logic.performance_logger = None

    worker_orchestrator = MagicMock()
    worker_orchestrator.process_tick.return_value = MagicMock()

    tick_source = MagicMock()
    tick_source.is_exhausted.return_value = True
    # A replay's own time, the front of its queue: the session stamps the venue's answers on it
    tick_source.get_idle_pass_time.return_value = ticks[-1].timestamp

    loop = AutotraderEventLoop(
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
