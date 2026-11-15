"""
Sim Ghost-Pass / Heartbeat Parity (#360 Stage 2)

The sim pipeline drives decision ghost-passes in the simulated-time gap between two
replayed data ticks, so an opt-in algo (wants_heartbeat) reacts between ticks at the
same relative point as live. Three deterministic layers, no broker, no network:

1. LatencySimulator.process_up_to_msc — resolve fills by an explicit simulated msc
   (the mechanism that lets a fill landing in a gap be resolved at its own instant).
2. The timeline intake's timers (#460) — interval cadence within a gap, the #208
   weekend-gap correctness gate, and none without a heartbeat. Until #460 a gap walk
   (`_run_sim_heartbeats`) made these passes; the intake makes them as timer events, on the
   same arrival axis and at the same instants.
3. execute_tick_loop — end-to-end: ghost-passes fire through the real loop for an
   opt-in decision (hard-gated; a non-opt-in decision fires none), and a session end asked
   for in one ends the run after it.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.global_logger import GlobalLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.sim_event_intake import SimEventIntake
from python.framework.process.process_tick_loop import execute_tick_loop
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.order_latency_simulator import OrderLatencySimulator
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.live_types.live_stats_config_types import LiveStatsExportConfig
from python.framework.types.loop_types.loop_event_types import DataSliceEvent, TimeEvent
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.utils.run_id_utils import mint_run_id

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
            placed_msc=_BASE_MSC,
            placed_at=datetime.fromtimestamp(_BASE_MSC / 1000, tz=timezone.utc),
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

class _NothingDue:
    """A simulator with nothing in flight, as the intake asks it."""

    def peek_next_due_msc(self):
        return None


def _instants(ticks, interval_ms: int = 1000, threshold_s: float = 300.0):
    """Every instant the intake hands over for the ticks, as tuples of event classes."""
    intake = SimEventIntake(ticks, _NothingDue(), EventSequencer(), interval_ms,
                            threshold_s * 1000.0, MagicMock())
    return [tuple(type(event) for event in instant) for instant in intake]


class TestTheIntakeTimers:
    """
    The intake makes a heartbeat logic's timers at the interval within a sub-threshold gap only.

    The trading-day boundary each of them checks is pinned in
    `tests/simulation/core/test_ghost_pass_booking_boundary.py`; these cases are about the cadence.
    """

    def test_a_timer_every_interval_within_a_gap(self):
        # 10 s gap, 1 s interval → timers at 1..9 s, between the two slices
        instants = _instants([_tick(_BASE_MSC), _tick(_BASE_MSC + 10_000)])
        assert instants == [(DataSliceEvent,)] + [(TimeEvent,)] * 9 + [(DataSliceEvent,)]

    def test_none_across_a_weekend_gap(self):
        # 400 s gap > 300 s threshold → the correctness gate makes none (#208)
        instants = _instants([_tick(_BASE_MSC), _tick(_BASE_MSC + 400_000)])
        assert instants == [(DataSliceEvent,), (DataSliceEvent,)]

    def test_none_without_a_heartbeat(self):
        instants = _instants([_tick(_BASE_MSC), _tick(_BASE_MSC + 10_000)], interval_ms=0)
        assert instants == [(DataSliceEvent,), (DataSliceEvent,)]


# =============================================================================
# 3. End-to-end through the real loop (hard gate)
# =============================================================================

def _run_loop(ticks, wants_heartbeat: bool, end_in_first_ghost: bool = False,
              end_in_first_tick: bool = False) -> MagicMock:
    """
    Run execute_tick_loop with a real TradeSimulator + mock orchestrator/decision.

    Args:
        ticks: The ticks
        wants_heartbeat: Whether the decision opts in to heartbeats
        end_in_first_ghost: Whether the first ghost pass asks for the session end
        end_in_first_tick: Whether the first tick pass asks for the session end
    """
    logger = ScenarioLogger(
        scenario_set_name='parity', scenario_name='heartbeat_ghost',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)),
    )
    controller = BarRenderingController(logger=logger)
    controller._required_timeframes = {'M1'}
    sim = TradeSimulator(
        broker_config=_broker_config(), initial_balance=10000.0,
        account_currency='USD', logger=logger, seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=1, inbound_latency_max_ms=1,
        spot_mode=True, initial_balances={'USD': 10000.0, 'BTC': 0.0},
    )
    worker_coordinator = MagicMock()
    worker_coordinator.process_tick.return_value = MagicMock()
    worker_coordinator.process_heartbeat.return_value = MagicMock()
    if end_in_first_ghost:
        def _ask_for_the_end():
            sim.request_session_end('the logic is done')
            return MagicMock()
        worker_coordinator.process_heartbeat.side_effect = _ask_for_the_end
    if end_in_first_tick:
        def _ask_for_the_end_on_a_tick(*args, **kwargs):
            sim.request_session_end('the logic is done')
            return MagicMock()
        worker_coordinator.process_tick.side_effect = _ask_for_the_end_on_a_tick
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

    def test_a_session_end_in_a_ghost_pass_ends_the_run_after_it(self):
        wc = _run_loop([_tick(_BASE_MSC), _tick(_BASE_MSC + 10_000)], wants_heartbeat=True,
                       end_in_first_ghost=True)
        assert wc.process_heartbeat.call_count == 1
        # the second tick never reached the algo
        assert wc.process_tick.call_count == 1

    def test_a_session_end_in_a_tick_pass_ends_the_run_after_it(self):
        """
        With no decision event channel too: the loop checks for a session end after every
        instant (#460) — the tick pass's check used to sit behind the event delivery.
        """
        wc = _run_loop([_tick(_BASE_MSC), _tick(_BASE_MSC + 1_000), _tick(_BASE_MSC + 2_000)],
                       wants_heartbeat=False, end_in_first_tick=True)
        assert wc.process_tick.call_count == 1
