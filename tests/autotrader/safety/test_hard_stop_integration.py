"""
Hard Stop Integration — the hard stop's wiring through the real tick loop (#356).

The unit suites drive the hard stop's methods against a stub. This drives the whole path a
session takes: the loop values the account on a tick, the safety layer sees the drawdown pass
the hard threshold, sends the closes, the closes fill through the executor's ordinary drain, and
the session ends as an EMERGENCY with the outcome on the record. Three variants, because the
three end differently — a margin position is closed, a spot holding is sold only when
`spot_liquidate_to_quote` says so, and otherwise kept.

One position opens before a price drop on a fixed tick; the drop on that position passes the
hard threshold, and nothing else in the run could.
"""

import dataclasses
import queue
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.safety_session_types import (
    FlattenOutcome,
    SafetySessionRecord,
)
from python.framework.types.config_types.autotrader_defaults_config_types import SafetyConfig
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.persistence_types import BaselineKind
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.parity_fixtures import make_flat_btcusd_ticks

SYMBOL = 'BTCUSD'
_TICK_COUNT = 600
_OPEN_TICK = 100
_DROP_TICK = 300
_DROP = 2_000.0          # on 0.01 BTC: a 20 USD drawdown
_HARD_LIMIT_ABS = 10.0   # passed by the drop, by nothing else
_LOTS = 0.01

_LOGIC_CONFIG = {
    'trade_sequence': [
        {'tick_number': _OPEN_TICK, 'direction': 'LONG', 'hold_ticks': 10_000, 'lot_size': _LOTS},
    ],
    'lot_size': _LOTS,
    'modify_sequence': [],
    'modify_limit_sequence': [],
    'modify_stop_sequence': [],
    'cancel_limit_sequence': [],
    'cancel_stop_sequence': [],
}


def _ticks_with_a_drop() -> List[TickData]:
    """
    Flat ticks, and from one tick on the price stays a fixed step lower.

    Returns:
        The ticks, oldest first
    """
    ticks = make_flat_btcusd_ticks(count=_TICK_COUNT)
    return [
        dataclasses.replace(tick, bid=tick.bid - _DROP, ask=tick.ask - _DROP)
        if index >= _DROP_TICK else tick
        for index, tick in enumerate(ticks)]


def _run(spot_mode: bool, liquidate: bool = False) -> Tuple[
        AutotraderTickLoop, LiveTradeExecutor, int]:
    """
    Run the AutoTrader tick loop over the drop with the hard stop armed.

    Args:
        spot_mode: The account model
        liquidate: `spot_liquidate_to_quote` — whether the hard stop may sell a spot holding

    Returns:
        (the loop, its executor, the ticks it processed)
    """
    logger = ScenarioLogger(
        scenario_set_name='hard_stop', scenario_name='spot' if spot_mode else 'margin',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)))
    tick_queue: queue.Queue = queue.Queue()
    for tick in _ticks_with_a_drop():
        tick_queue.put(tick)
    tick_queue.put(None)

    balances: Optional[Dict[str, float]] = {'USD': 10000.0, 'BTC': 0.0} if spot_mode else None
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger,
        spot_mode=spot_mode, initial_balances=balances)
    logic = DeterministicProbe(
        name='hard_stop_logic', logger=logger, config=_LOGIC_CONFIG, trading_context=None)
    logic.set_trading_api(DecisionTradingApi(
        executor=executor,
        required_order_types=logic.get_required_order_types(_LOGIC_CONFIG),
        order_guard_config=None))
    orchestrator = MagicMock()
    orchestrator.process_tick.side_effect = (
        lambda tick, current_bars, bar_history, bar_render_state=None:
        logic.compute_tick(tick, {}))
    tick_source = MagicMock()
    tick_source.is_exhausted.return_value = True

    loop = AutotraderTickLoop(
        config=AutoTraderConfig(
            profile_name='hard_stop', symbol=SYMBOL, broker_type='kraken_spot',
            adapter_type='mock',
            safety=SafetyConfig(
                enabled=True, emergency_flatten_enabled=True,
                max_drawdown_abs_hard=_HARD_LIMIT_ABS, spot_liquidate_to_quote=liquidate)),
        tick_queue=tick_queue,
        tick_source=tick_source,
        executor=executor,
        bar_controller=BarRenderingController(logger=logger),
        worker_orchestrator=orchestrator,
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
    ticks_processed, _ = loop.run()
    return loop, executor, ticks_processed


def _assert_the_stop_fired(record: SafetySessionRecord, executor: LiveTradeExecutor,
                           ticks_processed: int) -> None:
    """What every variant shares: the stop fired on the drop and ended the session early."""
    assert record.flatten_fired
    assert 'hard drawdown' in record.flatten_reason
    assert executor.is_session_end_requested()
    assert executor.get_session_end_severity() is SessionEndSeverity.EMERGENCY
    assert ticks_processed < _TICK_COUNT, 'the session ended on the stop, not on the input'


@pytest.mark.parametrize('spot_mode, liquidate', [(False, False), (True, True)],
                         ids=['margin', 'spot_liquidated'])
def test_the_hard_stop_closes_everything_and_ends_the_session(spot_mode, liquidate):
    loop, executor, ticks_processed = _run(spot_mode, liquidate)
    record = loop.get_safety_session()

    _assert_the_stop_fired(record, executor, ticks_processed)
    assert record.flatten_outcome is FlattenOutcome.COMPLETE
    assert executor.portfolio.get_open_positions() == []
    assert executor.get_session_end_reason().startswith('emergency flatten complete')


def test_a_spot_holding_is_kept_when_the_stop_may_not_sell_it():
    loop, executor, ticks_processed = _run(spot_mode=True, liquidate=False)
    record = loop.get_safety_session()

    _assert_the_stop_fired(record, executor, ticks_processed)
    assert record.flatten_outcome is FlattenOutcome.HOLDINGS_KEPT
    assert len(record.flatten_kept) == 1
    assert len(executor.portfolio.get_open_positions()) == 1, 'the holding stays, unsold'
    assert executor.get_session_end_reason().startswith('emergency stop, spot holdings kept')
