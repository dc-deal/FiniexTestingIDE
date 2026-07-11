"""
FiniexTestingIDE - Backtesting Event Probe Decision Logic (#348)

Exercises the Decision Event Channel end-to-end in BOTH pipelines (simulation
and AutoTrader-mock). It subscribes to every decision event, drives a small
deterministic plan that generates events, and records the ordered sequence it
receives via the on_* hooks.

This decision logic is designed for TESTING, not production trading. It proves
that the event channel delivers the same events, in the same order, regardless
of pipeline:
- ORDER_FILLED   — from the MARKET open
- PARTIAL_CLOSE  — from the partial close of the open position
- SESSION_END    — from request_session_end at the end of the plan

The recorded sequence is exposed two ways:
- get_received_event_log() — in-process access (AutoTrader-mock test)
- get_statistics().backtesting_metadata.received_events — cross-process channel
  (simulation test, where the decision logic runs in a subprocess)

Configuration:
{
    "open_tick": 100,
    "lot_size": 0.02,
    "partial_close_tick": 2000,
    "partial_close_lots": 0.01,
    "session_end_tick": 5000
}
"""

from typing import Any, Dict, List, Optional, Set

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.decision_event_types import (
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    SessionEndEvent,
    SessionEndSeverity,
)
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats
from python.framework.types.trading_env_types.order_types import (
    OrderResult,
    OrderSide,
    OrderType,
)
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.worker_types import WorkerRequirement, WorkerResult


class BacktestingEventProbe(AbstractDecisionLogic):
    """
    Event-channel probe decision logic for dual-world validation testing.

    Opens one MARKET position, partially closes it, then requests session end —
    and records every decision event delivered through the on_* hooks. The
    recorded sequence must be identical in the simulation and AutoTrader-mock
    pipelines.
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        super().__init__(name, logger, config, trading_context=trading_context)

        # Plan configuration
        self._open_tick = self.params.get('open_tick')
        self._lot_size = self.params.get('lot_size')
        self._partial_close_tick = self.params.get('partial_close_tick')
        self._partial_close_lots = self.params.get('partial_close_lots')
        self._session_end_tick = self.params.get('session_end_tick')

        # Plan state
        self.tick_count = 0
        self._position_id: Optional[str] = None
        self._opened = False
        self._partial_close_submitted = False
        self._session_end_requested = False

        # Ordered log of received event types (the channel under test)
        self._received_events: List[str] = []

        self.logger.info(
            f"BacktestingEventProbe initialized: open@{self._open_tick}, "
            f"partial_close@{self._partial_close_tick} ({self._partial_close_lots} lots), "
            f"session_end@{self._session_end_tick}"
        )

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {
            'open_tick': InputParamDef(
                param_type=int, default=100, min_val=1,
                description="Tick at which to open the MARKET position"
            ),
            'lot_size': InputParamDef(
                param_type=float, default=0.02, min_val=0.0, max_val=100.0,
                description="Lot size for the opened position"
            ),
            'partial_close_tick': InputParamDef(
                param_type=int, default=2000, min_val=1,
                description="Tick at which to partially close the position"
            ),
            'partial_close_lots': InputParamDef(
                param_type=float, default=0.01, min_val=0.0, max_val=100.0,
                description="Lots to close at the partial close (< lot_size)"
            ),
            'session_end_tick': InputParamDef(
                param_type=int, default=5000, min_val=1,
                description="Tick at which to request session end"
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        return {
            'lot_size': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Position lot size',
                category='SIGNAL',
            ),
            'reason': OutputParamDef(
                param_type=str,
                description='Human-readable decision explanation',
                category='INFO',
            ),
            'price': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Price at decision time',
                category='INFO',
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    @classmethod
    def get_subscribed_events(cls) -> Set[DecisionEventType]:
        """Subscribe to every event so the probe records the full channel."""
        return {
            DecisionEventType.ORDER_FILLED,
            DecisionEventType.ORDER_REJECTED,
            DecisionEventType.ORDER_CANCELLED,
            DecisionEventType.PARTIAL_CLOSE,
            DecisionEventType.SESSION_END,
        }

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {
            "backtesting_worker": WorkerRequirement.all('CORE/backtesting/backtesting_sample_worker')
        }

    # ============================================
    # Decision Event Hooks (#348) — record the channel
    # ============================================

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        self._received_events.append(DecisionEventType.ORDER_FILLED.value)
        self.logger.info(f"[EVENT] order_filled {event.order_id} @ {event.fill_price}")

    def on_order_rejected(self, event: OrderRejectedEvent) -> None:
        self._received_events.append(DecisionEventType.ORDER_REJECTED.value)
        self.logger.info(f"[EVENT] order_rejected {event.order_id} ({event.message})")

    def on_order_cancelled(self, event: OrderCancelledEvent) -> None:
        self._received_events.append(DecisionEventType.ORDER_CANCELLED.value)
        self.logger.info(f"[EVENT] order_cancelled {event.order_id}")

    def on_partial_close(self, event: PartialCloseEvent) -> None:
        self._received_events.append(DecisionEventType.PARTIAL_CLOSE.value)
        self.logger.info(
            f"[EVENT] partial_close {event.position_id} "
            f"closed={event.closed_lots} remaining={event.remaining_lots}"
        )

    def on_session_end(self, event: SessionEndEvent) -> None:
        self._received_events.append(DecisionEventType.SESSION_END.value)
        self.logger.info(f"[EVENT] session_end ({event.reason})")

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """
        Deliberate pass (#436): replay gaps are data — sim dispatches this
        only under a planned stale_data_stress window, which this probe's
        scenarios do not configure.

        Args:
            status: Session-level market-data health snapshot
        """
        pass

    # ============================================
    # Core Logic: compute() + execute()
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        self.tick_count += 1

        if self.tick_count == self._open_tick and not self._opened:
            return Decision(
                action=DecisionLogicAction.BUY,
                outputs={
                    'lot_size': self._lot_size,
                    'reason': f"Event-probe open at tick {self.tick_count}",
                    'price': tick.mid,
                },
            )

        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={'reason': 'No signal', 'price': tick.mid},
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        if not self.trading_api:
            self.logger.warning("No trading_api available - skipping execution")
            return None

        # Partial close at the configured tick
        self._maybe_partial_close()

        # Session end at the configured tick
        self._maybe_request_session_end()

        # Open the position on signal
        if decision.action == DecisionLogicAction.BUY and not self._opened:
            self._opened = True
            order_result = self.trading_api.send_order(
                symbol=tick.symbol,
                order_type=OrderType.MARKET,
                side=OrderSide.BUY,
                lots=self._lot_size,
                comment='EventProbe open',
            )
            if order_result and not order_result.is_rejected:
                self._position_id = order_result.order_id
            return order_result

        return None

    def _maybe_partial_close(self) -> None:
        """Submit the partial close once, at partial_close_tick."""
        if self._partial_close_submitted:
            return
        if self.tick_count != self._partial_close_tick:
            return
        if self._position_id is None:
            return

        for pos in self.trading_api.get_open_positions():
            if pos.position_id == self._position_id:
                if self.trading_api.is_pending_close(pos.position_id):
                    return
                self._partial_close_submitted = True
                self.trading_api.close_position(
                    pos.position_id, lots=self._partial_close_lots)
                self.logger.info(
                    f"📊 Event-probe partial close at tick {self.tick_count}: "
                    f"{self._position_id} closing {self._partial_close_lots} lots"
                )
                return

    def _maybe_request_session_end(self) -> None:
        """Request session end once, at session_end_tick."""
        if self._session_end_requested:
            return
        if self.tick_count != self._session_end_tick:
            return
        self._session_end_requested = True
        self.trading_api.request_session_end(
            'event probe complete', SessionEndSeverity.NORMAL)
        self.logger.info(
            f"🛑 Event-probe requested session end at tick {self.tick_count}")

    # ============================================
    # Public Access + Statistics
    # ============================================

    def get_received_event_log(self) -> List[str]:
        """In-process access to the ordered received-event log (#348)."""
        return list(self._received_events)

    def get_statistics(self) -> DecisionLogicStats:
        base_stats = super().get_statistics()
        base_stats.backtesting_metadata = BacktestingMetadata(
            tick_count=self.tick_count,
            received_events=list(self._received_events),
        )
        return base_stats
