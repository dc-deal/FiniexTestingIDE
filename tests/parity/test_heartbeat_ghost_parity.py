"""
Sim Ghost-Pass / Heartbeat Parity (#360 Stage 2)

The sim pipeline drives decision ghost-passes in the simulated-time gap between two
replayed data ticks, so an opt-in algo (wants_heartbeat) reacts between ticks at the
same relative point as live. Three deterministic layers, no broker, no network:

1. LatencySimulator.process_up_to_msc — resolve fills by an explicit simulated msc
   (the mechanism that lets a fill landing in a gap be resolved on a ghost-pass).
2. _run_sim_heartbeats — the driver: interval cadence within a gap, the #208
   weekend-gap correctness gate, and session-end short-circuit.
3. execute_tick_loop — end-to-end: ghost-passes fire through the real loop for an
   opt-in decision (hard-gated; a non-opt-in decision fires none).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.global_logger import GlobalLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_tick_loop import execute_tick_loop, _run_sim_heartbeats
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.order_latency_simulator import OrderLatencySimulator
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

SYMBOL = 'BTCUSD'
_BASE_MSC = 1_000_000  # arbitrary simulated wall-clock anchor (ms)


def _tick(msc: int) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol=SYMBOL, bid=50000.0, ask=50001.0, collected_msc=msc,
    )


def _broker_config() -> BrokerConfig:
    return BrokerConfig(BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))


# =============================================================================
# 1. Latency resolution by explicit msc
# =============================================================================

class TestLatencyResolutionByMsc:
    """process_up_to_msc resolves a queued order exactly when its fill msc is reached."""

    def test_resolves_only_at_or_after_fill_msc(self):
        sim = OrderLatencySimulator(
            seeds={'inbound_latency_seed': 42}, logger=GlobalLogger(name='LatTest'),
            inbound_latency_min_ms=5000, inbound_latency_max_ms=5000,
        )
        sim.submit_open_order(
            order_id='o1',
            request=OpenOrderRequest(
                symbol=SYMBOL, order_type=OrderType.MARKET,
                direction=OrderDirection.LONG, lots=0.01),
            tick=_tick(_BASE_MSC),
        )
        # broker_fill_msc = _BASE_MSC + 5000 — not yet reached
        assert sim.process_up_to_msc(_BASE_MSC + 4000) == []
        # reached → resolved + removed
        resolved = sim.process_up_to_msc(_BASE_MSC + 5000)
        assert len(resolved) == 1 and resolved[0].pending_order_id == 'o1'
        assert sim.process_up_to_msc(_BASE_MSC + 9000) == []  # already removed


# =============================================================================
# 2. The ghost-pass driver (cadence + gates)
# =============================================================================

def _driver_mocks():
    trade_simulator = MagicMock()
    trade_simulator.is_session_end_requested.return_value = False
    worker_coordinator = MagicMock()
    worker_coordinator.process_heartbeat.return_value = MagicMock()  # a Decision
    decision_logic = MagicMock()
    return trade_simulator, worker_coordinator, decision_logic


def _config(interval_ms: int = 1000, threshold_s: float = 300.0) -> ProcessScenarioConfig:
    return ProcessScenarioConfig(
        name='hb', symbol=SYMBOL, scenario_index=0,
        start_time=datetime.fromtimestamp(_BASE_MSC / 1000.0, tz=timezone.utc),
        heartbeat_interval_ms=interval_ms,
        inter_tick_gap_threshold_s=threshold_s,
        live_stats_config=LiveStatsExportConfig(enabled=False),
    )


class TestSimHeartbeatDriver:
    """_run_sim_heartbeats fires at the interval within a sub-threshold gap only."""

    def test_fires_interval_passes_within_gap(self):
        ts, wc, dl = _driver_mocks()
        # 10 s gap, 1 s interval → ghost-passes at 1..9 s (9 passes)
        ended = _run_sim_heartbeats(
            _BASE_MSC, _BASE_MSC + 10_000, _config(), ts, wc, dl, None)
        assert ended is False
        assert wc.process_heartbeat.call_count == 9
        # the decision was executed each pass with tick=None
        assert dl.execute_decision.call_count == 9
        for call in dl.execute_decision.call_args_list:
            assert call.kwargs.get('tick', call.args[1] if len(call.args) > 1 else 'x') is None

    def test_no_pass_across_weekend_gap(self):
        ts, wc, dl = _driver_mocks()
        # 400 s gap > 300 s threshold → correctness gate suppresses all passes (#208)
        ended = _run_sim_heartbeats(
            _BASE_MSC, _BASE_MSC + 400_000, _config(), ts, wc, dl, None)
        assert ended is False
        assert wc.process_heartbeat.call_count == 0

    def test_session_end_short_circuits(self):
        ts, wc, dl = _driver_mocks()
        ts.is_session_end_requested.return_value = True
        ended = _run_sim_heartbeats(
            _BASE_MSC, _BASE_MSC + 10_000, _config(), ts, wc, dl, None)
        assert ended is True
        assert wc.process_heartbeat.call_count == 1  # stopped after the first pass


# =============================================================================
# 3. End-to-end through the real loop (hard gate)
# =============================================================================

def _run_loop(ticks, wants_heartbeat: bool) -> MagicMock:
    """Run execute_tick_loop with a real TradeSimulator + mock orchestrator/decision."""
    logger = ScenarioLogger(
        scenario_set_name='parity', scenario_name='heartbeat_ghost',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {'M1'}
    sim = TradeSimulator(
        broker_config=_broker_config(), initial_balance=10000.0,
        account_currency='USD', logger=logger, seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0, inbound_latency_max_ms=0,
        spot_mode=True, initial_balances={'USD': 10000.0, 'BTC': 0.0},
    )
    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.return_value = MagicMock()
    worker_coordinator.process_heartbeat.return_value = MagicMock()
    decision_logic = MagicMock()
    decision_logic.wants_heartbeat.return_value = wants_heartbeat

    config = ProcessScenarioConfig(
        name='heartbeat_ghost', symbol=SYMBOL, scenario_index=0,
        start_time=ticks[0].timestamp,
        live_stats_config=LiveStatsExportConfig(enabled=False),
    )
    execute_tick_loop(
        config=config,
        worker_coordinator=worker_coordinator,
        trade_simulator=sim,
        bar_rendering_controller=controller,
        decision_logic=decision_logic,
        scenario_logger=logger,
        ticks=tuple(ticks),
        decision_event_dispatcher=None,
    )
    return worker_coordinator


class TestSimGhostPassLoop:
    """The real loop fires ghost-passes between ticks — only for an opt-in decision."""

    def test_opt_in_fires_ghost_passes_in_gap(self):
        # 10 s gap between two ticks, 1 s default interval → 9 ghost-passes
        wc = _run_loop([_tick(_BASE_MSC), _tick(_BASE_MSC + 10_000)], wants_heartbeat=True)
        assert wc.process_heartbeat.call_count == 9

    def test_no_opt_in_fires_none(self):
        wc = _run_loop([_tick(_BASE_MSC), _tick(_BASE_MSC + 10_000)], wants_heartbeat=False)
        assert wc.process_heartbeat.call_count == 0

    def test_weekend_gap_fires_none(self):
        # 400 s gap > 300 s threshold → no ghost-passes even when opted in
        wc = _run_loop([_tick(_BASE_MSC), _tick(_BASE_MSC + 400_000)], wants_heartbeat=True)
        assert wc.process_heartbeat.call_count == 0
