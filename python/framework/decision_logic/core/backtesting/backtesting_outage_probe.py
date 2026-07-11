"""
FiniexTestingIDE - Backtesting Outage Probe Decision Logic (#436)

Exercises BOTH staleness contracts deterministically in one scenario run:
- on_signal_stale (#434): fired by the orchestrator when the SIGNAL feed dies
  (driven by a stale_data_stress `signal_worker` window — data-plane cut).
- on_market_data_stale (#436): fired by the stale-data stress driver on the
  sim time axis (`market_data` window — status-plane) or, in the AutoTrader,
  by the live loop's heartbeat evaluation.

While the market is stale the probe attempts EXACTLY ONE entry per episode to
prove the OrderGuard floor (STALE_MARKET_DATA rejection), otherwise it stays
FLAT — it never trades for real.

This decision logic is designed for TESTING, not production trading.

Records (cross-process channel, same pattern as the event probe #348):
    get_statistics().backtesting_metadata.received_events
    - 'signal_stale:<worker>:<source>'
    - 'market_data_stale'
    - 'stale_entry_rejected'                (guard floor proven)
    - 'stale_entry_UNEXPECTED:<status>'     (guard floor FAILED)
"""

from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OrderResult,
    OrderSide,
    OrderType,
    RejectionReason,
)
from python.framework.types.worker_types import WorkerRequirement, WorkerResult


class BacktestingOutageProbe(AbstractDecisionLogic):
    """
    Staleness-contract probe decision logic (#434 + #436).

    Consumes one SIGNAL worker (so the #434 contract applies) and records
    every staleness hook firing plus the guard verdict of one deliberate
    entry attempt per market-stale episode.
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        super().__init__(name, logger, config, trading_context=trading_context)

        self._lot_size = self.params.get('lot_size')

        # Probe state
        self.tick_count = 0
        self._entry_probe_pending = False   # armed by the market-stale hook
        self._probed_this_episode = False   # one attempt per episode

        # Ordered log of staleness events (the contracts under test)
        self._received_events: List[str] = []

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """CORE test probe metadata (#436)."""
        return ComponentMetadata(
            version='1.0.0',
            doc_link='docs/user_guides/live_outage_handling_guide.md',
        )

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {
            'lot_size': InputParamDef(
                param_type=float, default=0.01, min_val=0.0, max_val=100.0,
                description="Lot size of the deliberate stale-entry probe"
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        return {
            'reason': OutputParamDef(
                param_type=str,
                description='Human-readable decision explanation',
                category='INFO',
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        """One SIGNAL worker — the #434 contract must apply to this probe."""
        return {
            'sentiment': WorkerRequirement.of(
                'CORE/llm_sentiment', 'sentiment_score', 'confidence'),
        }

    # ============================================
    # Staleness Contracts (#434 + #436) — record the hooks
    # ============================================

    def on_signal_stale(self, worker_name: str, source: str) -> None:
        """
        Record the SIGNAL-feed staleness edge (#434).

        Args:
            worker_name: The SIGNAL worker instance that turned stale
            source: Its signal source key
        """
        self._received_events.append(f'signal_stale:{worker_name}:{source}')
        self.logger.warning(
            f"[PROBE] on_signal_stale fired: {worker_name} ({source})")

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """
        Record the market-data staleness edge (#436) and arm the guard probe.

        Args:
            status: Session-level market-data health snapshot
        """
        self._received_events.append('market_data_stale')
        self._entry_probe_pending = True
        self.logger.warning(
            f"[PROBE] on_market_data_stale fired "
            f"({status.seconds_since_last_tick:.0f}s since last tick)")

    # ============================================
    # Core Logic: compute() + execute()
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        self.tick_count += 1

        market_stale = self.trading_api.get_market_data_status().is_stale \
            if self.trading_api else False
        if not market_stale:
            # Episode over → re-arm for the next one
            self._probed_this_episode = False

        if self._entry_probe_pending and market_stale and not self._probed_this_episode:
            return Decision(
                action=DecisionLogicAction.BUY,
                outputs={'reason': 'Deliberate stale-entry probe (#436)'},
            )

        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={'reason': 'Probe idle'},
        )

    def wants_heartbeat(self) -> bool:
        # Live/AT: during a freeze there ARE no ticks — the stale-entry probe
        # must run on the ghost pass. Sim probes via compute_tick (the stress
        # driver keeps ticks flowing there); the heartbeat path stays inert.
        return True

    def compute_heartbeat(
        self,
        worker_results: Dict[str, WorkerResult],
    ) -> Optional[Decision]:
        market_stale = self.trading_api.get_market_data_status().is_stale \
            if self.trading_api else False
        if self._entry_probe_pending and market_stale and not self._probed_this_episode:
            return Decision(
                action=DecisionLogicAction.BUY,
                outputs={'reason': 'Deliberate stale-entry probe (#436, ghost pass)'},
            )
        return None

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData]
    ) -> Optional[OrderResult]:
        if decision.action != DecisionLogicAction.BUY or not self.trading_api:
            return None

        # One deliberate entry per stale episode — the guard MUST reject it.
        # tick is None on a ghost pass (AT freeze) → symbol from the context.
        symbol = tick.symbol if tick is not None else self._trading_context.symbol
        self._probed_this_episode = True
        self._entry_probe_pending = False
        result = self.trading_api.send_order(
            symbol=symbol,
            order_type=OrderType.MARKET,
            side=OrderSide.BUY,
            lots=self._lot_size,
            comment='OutageProbe stale entry',
        )
        if result.is_rejected and result.rejection_reason == RejectionReason.STALE_MARKET_DATA:
            self._received_events.append('stale_entry_rejected')
            self.logger.warning(
                '[PROBE] stale entry rejected by OrderGuard (STALE_MARKET_DATA)')
        else:
            self._received_events.append(
                f'stale_entry_UNEXPECTED:{result.status.value}')
            self.logger.error(
                f"[PROBE] stale entry NOT guard-blocked: {result.status.value}")
        return result

    # ============================================
    # Statistics (cross-process channel)
    # ============================================

    def get_received_event_log(self) -> List[str]:
        """In-process access to the ordered staleness-event log."""
        return list(self._received_events)

    def get_statistics(self) -> DecisionLogicStats:
        base_stats = super().get_statistics()
        base_stats.backtesting_metadata = BacktestingMetadata(
            tick_count=self.tick_count,
            received_events=list(self._received_events),
        )
        return base_stats
