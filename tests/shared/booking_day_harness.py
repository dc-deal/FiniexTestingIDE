"""
FiniexTestingIDE - Booking Day Harness

One trading day's boundary, driven through both tick loops: flat BTCUSD ticks shifted so one
tick lands exactly on midnight UTC (kraken_spot is a crypto market, whose day flips there), a
price gap on that tick, and a position opened before midnight and closed after it. Shared by the
parity test that pins where a day ends (#476) and by the tests of what a session does at the
seal.
"""

import copy
import dataclasses
import queue
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.persistence_types import BaselineKind
from python.framework.types.process_data_types import (
    ProcessScenarioConfig,
    ProcessTickLoopResult,
)
from python.framework.types.run_results_types import BookingPeriod
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import OrderFeature
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.parity_fixtures import make_flat_btcusd_ticks

SYMBOL = 'BTCUSD'
# kraken_spot is a crypto market: its trading day flips at midnight UTC
MIDNIGHT = datetime(2026, 1, 25, 0, 0, 0, tzinfo=timezone.utc)
_TICK_COUNT = 900
_BOUNDARY_TICK = 300          # lands on midnight; one tick per second
GAP = 2_000.0                 # the boundary tick's drop — 20 USD on the position below
LOTS = 0.01

_LOGIC_CONFIG = {
    'trade_sequence': [
        {
            'tick_number': 100,   # opens before midnight
            'direction': 'LONG',
            'hold_ticks': 500,    # closes after it
            'lot_size': LOTS,
        }
    ],
    'lot_size': LOTS,
    'modify_sequence': [],
    'modify_limit_sequence': [],
    'modify_stop_sequence': [],
    'cancel_limit_sequence': [],
    'cancel_stop_sequence': [],
    'exit_mode': 'declared',
}

def ticks_across_midnight(quiet_seconds: int = 0, days_skipped: int = 0) -> List[TickData]:
    """
    Flat ticks, shifted so the boundary tick lands on midnight, with a price gap on the first
    tick at or after it.

    Flat everywhere else, because the two pipelines fill a market order one tick apart — a
    constant price makes that difference invisible, so whatever differs is the booking order.

    Args:
        quiet_seconds: No tick closer than this to midnight on either side, so the boundary
            passes between two ticks; 0 puts a tick on it
        days_skipped: Every tick from the boundary tick on lands this many days later, so the
            gap before it crosses one boundary more per day

    Returns:
        The ticks, oldest first
    """
    ticks = make_flat_btcusd_ticks(count=_TICK_COUNT)
    shifted = []
    for index, tick in enumerate(ticks):
        shift = MIDNIGHT - ticks[_BOUNDARY_TICK].timestamp
        if index >= _BOUNDARY_TICK:
            shift += timedelta(days=days_skipped)
        shift_ms = int(shift.total_seconds() * 1000)
        shifted.append(dataclasses.replace(
            tick, timestamp=tick.timestamp + shift, time_msc=tick.time_msc + shift_ms,
            collected_msc=tick.collected_msc + shift_ms))
    if quiet_seconds:
        quiet = timedelta(seconds=quiet_seconds)
        shifted = [tick for tick in shifted if abs(tick.timestamp - MIDNIGHT) >= quiet]
    first = next(index for index, tick in enumerate(shifted) if tick.timestamp >= MIDNIGHT)
    shifted[first] = dataclasses.replace(
        shifted[first], bid=shifted[first].bid - GAP, ask=shifted[first].ask - GAP)
    return shifted


def _balances(spot_mode: bool) -> Optional[Dict[str, float]]:
    """The spot inventory, or None at margin."""
    return {'USD': 10000.0, 'BTC': 0.0} if spot_mode else None


def _logger(name: str) -> ScenarioLogger:
    """A scenario logger for one pipeline's run."""
    return ScenarioLogger(
        scenario_set_name='parity', scenario_name=name,
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)))


def _probe(executor, logger: ScenarioLogger, stop_loss: Optional[float],
           hold_ticks: Optional[int] = None,
           stop_beyond_venue: bool = False) -> DeterministicProbe:
    """
    The decision logic that opens before midnight and closes after it.

    Args:
        executor: The pipeline's executor
        logger: The run's logger
        stop_loss: The position's stop level, or None for none — a position with one is held
            to the end of the feed, so the stop is what closes it
        hold_ticks: How many ticks the position is held; None keeps the fixture's, which close
            it after midnight
        stop_beyond_venue: Send the stop with the order, as the declared exception a backtest
            may make: the fixture's venue holds no stop, so a session keeps it as the probe's own

    Returns:
        The probe, wired to the executor
    """
    config = copy.deepcopy(_LOGIC_CONFIG)
    if hold_ticks is not None:
        config['trade_sequence'][0]['hold_ticks'] = hold_ticks
    if stop_loss is not None:
        config['trade_sequence'][0]['stop_loss'] = stop_loss
        config['trade_sequence'][0]['hold_ticks'] = _TICK_COUNT
        if not stop_beyond_venue:
            config['exit_mode'] = 'own'
    logic = DeterministicProbe(
        name='booking_parity_logic', logger=logger, config=config,
        trading_context=None)
    logic.set_trading_api(DecisionTradingApi(
        executor=executor,
        required_order_types=logic.get_required_order_types(config),
        order_guard_config=None,
        required_order_features=logic.get_required_order_features(config),
        features_beyond_venue=[OrderFeature.STOP_LOSS] if stop_beyond_venue else []))
    return logic


def _orchestrator(logic: DeterministicProbe) -> MagicMock:
    """Both loops ask the orchestrator for the decision; it delegates to the probe."""
    orchestrator = MagicMock()
    orchestrator.process_tick.side_effect = (
        lambda tick, current_bars, bar_history, bar_render_state=None:
        logic.compute_tick(tick, {}))
    # The probe does not take part in heartbeats, so an idle pass computes no decision
    orchestrator.process_heartbeat.return_value = None
    return orchestrator


def simulation_periods(ticks: List[TickData], spot_mode: bool,
                       stop_loss: Optional[float] = None) -> List[BookingPeriod]:
    """
    Run the simulation's tick loop over the ticks.

    Args:
        ticks: The input
        spot_mode: The account model
        stop_loss: The position's stop level, or None for none

    Returns:
        Its booking periods
    """
    return simulation_result(ticks, spot_mode, stop_loss).booking_periods


def simulation_result(ticks: List[TickData], spot_mode: bool,
                      stop_loss: Optional[float] = None) -> ProcessTickLoopResult:
    """
    Run the simulation's tick loop over the ticks.

    Args:
        ticks: The input
        spot_mode: The account model
        stop_loss: The position's stop level, or None for none

    Returns:
        Everything the loop returns — its booking periods and its execution statistics among it
    """
    logger = _logger('booking_parity_sim')
    simulator = TradeSimulator(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0, inbound_latency_max_ms=0,
        spot_mode=spot_mode, initial_balances=_balances(spot_mode))
    logic = _probe(simulator, logger, stop_loss, stop_beyond_venue=True)
    result = execute_tick_loop(
        config=ProcessScenarioConfig(
            name='booking_parity', symbol=SYMBOL, scenario_index=0,
            start_time=ticks[0].timestamp, broker_type=BrokerType.KRAKEN_SPOT,
            live_stats_config=LiveStatsExportConfig(enabled=False)),
        worker_coordinator=_orchestrator(logic),
        trade_simulator=simulator,
        bar_rendering_controller=BarRenderingController(logger=logger),
        decision_logic=logic,
        scenario_logger=logger,
        ticks=tuple(ticks),
        live_queue=None,
    )
    return result


def autotrader_periods(ticks: List[TickData], spot_mode: bool,
                       stop_loss: Optional[float] = None) -> List[BookingPeriod]:
    """
    Run the AutoTrader tick loop over the ticks.

    Args:
        ticks: The input
        spot_mode: The account model
        stop_loss: The position's stop level, or None for none

    Returns:
        Its booking periods
    """
    loop = autotrader_loop(ticks, spot_mode, stop_loss=stop_loss)
    loop.run()
    return loop.get_booking_periods()


def autotrader_loop(ticks: List[TickData], spot_mode: bool,
                    logger: Optional[ScenarioLogger] = None,
                    stop_loss: Optional[float] = None,
                    idle_heartbeats: int = 0,
                    hold_ticks: Optional[int] = None,
                    executor: Optional[LiveTradeExecutor] = None,
                    **loop_options: Any) -> AutotraderTickLoop:
    """
    The AutoTrader tick loop over the ticks, built the way a session builds it.

    A session always hands the loop its risk baseline tracker, and the daily loss limit rides the
    same per-tick read, so the tracker is part of the construction here.

    Args:
        ticks: The input
        spot_mode: The account model
        logger: The session logger; a fresh one when None
        stop_loss: The position's stop level, or None for none
        idle_heartbeats: How many idle heartbeats the feed allows after its last tick before
            it reports itself exhausted; 0 ends the feed with its last tick
        hold_ticks: How many ticks the position is held; None keeps the fixture's
        executor: The executor to trade through, so a caller can read it afterwards; a fresh
            one when None
        loop_options: Further collaborators the session would hand the loop, by name

    Returns:
        The loop, not yet run
    """
    logger = logger or _logger('booking_parity_at')
    tick_queue: queue.Queue = queue.Queue()
    for tick in ticks:
        tick_queue.put(tick)
    if not idle_heartbeats:
        tick_queue.put(None)

    executor = executor or live_executor(spot_mode, logger)
    logic = _probe(executor, logger, stop_loss, hold_ticks)
    tick_source = MagicMock()
    # Exhausted once the idle heartbeats it allows have passed, and from then on
    tick_source.is_exhausted.side_effect = (
        lambda: tick_source.is_exhausted.call_count > idle_heartbeats)
    # What an idle heartbeat asks a live source: a stream that never dropped
    tick_source.get_reconnect_count.return_value = 0
    tick_source.get_injected_outage_label.return_value = None

    loop = AutotraderTickLoop(
        config=AutoTraderConfig(
            profile_name='booking_parity', symbol=SYMBOL, broker_type='kraken_spot',
            adapter_type='mock'),
        tick_queue=tick_queue,
        tick_source=tick_source,
        executor=executor,
        bar_controller=BarRenderingController(logger=logger),
        worker_orchestrator=_orchestrator(logic),
        decision_logic=logic,
        clipping_monitor=LiveClippingMonitor(),
        logger=logger,
        trading_model=TradingModel.SPOT if spot_mode else TradingModel.MARGIN,
        run_dir=None,
        display_queue=None,
        dry_run=True,
        risk_baseline=RiskBaselineTracker(
            mode=BaselineKind.SESSION_FIXED, restored=None, spot_mode=spot_mode,
            exclusive_account=False, clock_fn=executor.get_current_time_if_set,
            logger=logger),
        **loop_options,
    )
    return loop


def live_executor(spot_mode: bool, logger: Optional[ScenarioLogger] = None) -> LiveTradeExecutor:
    """
    The executor an AutoTrader loop of this harness trades through.

    Args:
        spot_mode: The account model
        logger: The session logger; a fresh one when None

    Returns:
        A live executor over the instantly filling mock venue
    """
    return LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD',
        logger=logger or _logger('booking_parity_at'),
        spot_mode=spot_mode, initial_balances=_balances(spot_mode))
