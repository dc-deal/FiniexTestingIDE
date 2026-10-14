"""
Sim/Live Parity — a trading day's booking period holds its own closing value

Both pipelines seal a trading day's booking period on the first tick past the market's
boundary, and the seal reads the account value AFTER that tick was applied: the boundary tick's
value is the period's final equity. So that value has to be inside the period's band too. The
simulation observed it before sealing; the AutoTrader loop sealed first and observed after, so
its closing value sat outside the band it closed, and a drop on the boundary tick appeared in
neither day's deepest dip. A difference by construction between the two runs a parity proof
compares (#537).

Pinned as parity: the same ticks across midnight UTC, a position held through it, a price gap
on the boundary tick, both pipelines and both account models — the periods agree field by
field, and the closing value lies inside its own band in both.
"""

import dataclasses
import queue
from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.risk_baseline_tracker import RiskBaselineTracker
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
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.parity_fixtures import make_flat_btcusd_ticks

SYMBOL = 'BTCUSD'
# kraken_spot is a crypto market: its trading day flips at midnight UTC
_MIDNIGHT = datetime(2026, 1, 25, 0, 0, 0, tzinfo=timezone.utc)
_TICK_COUNT = 900
_BOUNDARY_TICK = 300          # lands on midnight; one tick per second
_GAP = 2_000.0                # the boundary tick's drop — 20 USD on the position below
_LOTS = 0.01

_LOGIC_CONFIG = {
    'trade_sequence': [
        {
            'tick_number': 100,   # opens before midnight
            'direction': 'LONG',
            'hold_ticks': 500,    # closes after it
            'lot_size': _LOTS,
        }
    ],
    'lot_size': _LOTS,
    'modify_sequence': [],
    'modify_limit_sequence': [],
    'modify_stop_sequence': [],
    'cancel_limit_sequence': [],
    'cancel_stop_sequence': [],
}

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _ticks_across_midnight() -> List[TickData]:
    """
    Flat ticks, shifted so the boundary tick lands on midnight, with a price gap on that tick.

    Flat everywhere else, because the two pipelines fill a market order one tick apart — a
    constant price makes that difference invisible, so whatever differs is the booking order.

    Returns:
        The ticks, oldest first
    """
    ticks = make_flat_btcusd_ticks(count=_TICK_COUNT)
    shift = _MIDNIGHT - ticks[_BOUNDARY_TICK].timestamp
    shift_ms = int(shift.total_seconds() * 1000)
    shifted = [
        dataclasses.replace(
            tick, timestamp=tick.timestamp + shift, time_msc=tick.time_msc + shift_ms,
            collected_msc=tick.collected_msc + shift_ms)
        for tick in ticks]
    boundary = shifted[_BOUNDARY_TICK]
    shifted[_BOUNDARY_TICK] = dataclasses.replace(
        boundary, bid=boundary.bid - _GAP, ask=boundary.ask - _GAP)
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


def _probe(executor, logger: ScenarioLogger) -> DeterministicProbe:
    """The decision logic that opens before midnight and closes after it."""
    logic = DeterministicProbe(
        name='booking_parity_logic', logger=logger, config=_LOGIC_CONFIG,
        trading_context=None)
    logic.set_trading_api(DecisionTradingApi(
        executor=executor,
        required_order_types=logic.get_required_order_types(_LOGIC_CONFIG),
        order_guard_config=None))
    return logic


def _orchestrator(logic: DeterministicProbe) -> MagicMock:
    """Both loops ask the orchestrator for the decision; it delegates to the probe."""
    orchestrator = MagicMock()
    orchestrator.process_tick.side_effect = (
        lambda tick, current_bars, bar_history, bar_render_state=None:
        logic.compute_tick(tick, {}))
    return orchestrator


def _simulation_periods(ticks: List[TickData], spot_mode: bool) -> List[BookingPeriod]:
    """
    Run the simulation's tick loop over the ticks.

    Args:
        ticks: The input
        spot_mode: The account model

    Returns:
        Its booking periods
    """
    logger = _logger('booking_parity_sim')
    simulator = TradeSimulator(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger,
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0, inbound_latency_max_ms=0,
        spot_mode=spot_mode, initial_balances=_balances(spot_mode))
    logic = _probe(simulator, logger)
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
    return result.booking_periods


def _autotrader_periods(ticks: List[TickData], spot_mode: bool) -> List[BookingPeriod]:
    """
    Run the AutoTrader tick loop over the ticks, built the way a session builds it.

    A session always hands the loop its risk baseline tracker, and the booking period's
    valuation rides the same per-tick read, so the tracker is part of the construction here.

    Args:
        ticks: The input
        spot_mode: The account model

    Returns:
        Its booking periods
    """
    logger = _logger('booking_parity_at')
    tick_queue: queue.Queue = queue.Queue()
    for tick in ticks:
        tick_queue.put(tick)
    tick_queue.put(None)

    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger,
        spot_mode=spot_mode, initial_balances=_balances(spot_mode))
    logic = _probe(executor, logger)
    tick_source = MagicMock()
    tick_source.is_exhausted.return_value = True

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
    )
    loop.run()
    return loop.get_booking_periods()


@ACCOUNT_MODELS
def test_both_pipelines_book_the_day_the_same_way(spot_mode):
    """The trading day's period and the one after it agree field by field."""
    ticks = _ticks_across_midnight()
    sim_periods = _simulation_periods(ticks, spot_mode)
    at_periods = _autotrader_periods(ticks, spot_mode)

    assert [p.reason for p in sim_periods] == [
        PeriodCloseReason.ANCHOR, PeriodCloseReason.SESSION_END]
    assert [p.reason for p in at_periods] == [p.reason for p in sim_periods]
    for sim, live in zip(sim_periods, at_periods):
        assert live.period_no == sim.period_no
        assert live.opened_at == sim.opened_at
        assert live.trade_count == sim.trade_count
        assert live.figures.net_pnl == pytest.approx(sim.figures.net_pnl, abs=1e-6)
        assert live.figures.final_equity == pytest.approx(sim.figures.final_equity, abs=1e-6)
        assert live.period_opening_equity == pytest.approx(sim.period_opening_equity, abs=1e-6)
        assert live.period_max_equity == pytest.approx(sim.period_max_equity, abs=1e-6)
        assert live.period_min_equity == pytest.approx(sim.period_min_equity, abs=1e-6)
        assert live.period_max_drawdown == pytest.approx(sim.period_max_drawdown, abs=1e-6)
        # The curve's running figures the seal reads — sampled before the seal in both
        assert live.figures.max_equity == pytest.approx(sim.figures.max_equity, abs=1e-6)
        assert live.figures.account_max_drawdown == pytest.approx(
            sim.figures.account_max_drawdown, abs=1e-6)
    assert at_periods[0].closed_at == sim_periods[0].closed_at == _MIDNIGHT


@ACCOUNT_MODELS
def test_the_day_holds_its_closing_value_and_the_gap_on_its_boundary(spot_mode):
    """The day sealed on the gap carries that gap — in its band and in its deepest dip."""
    ticks = _ticks_across_midnight()
    for periods in (_simulation_periods(ticks, spot_mode), _autotrader_periods(ticks, spot_mode)):
        day = periods[0]
        closing = day.figures.final_equity
        assert day.period_min_equity <= closing <= day.period_max_equity
        # 0.01 BTC through a 2,000 USD gap: the day fell by at least the gap's 20 USD
        assert day.period_max_drawdown >= _GAP * _LOTS
