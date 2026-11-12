"""
FiniexTestingIDE - Observer Probe Decision Logic
Records everything a decision can see, on every pass, so two runs can be compared field by field
(#653).

The portfolio marks its positions lazily, and that is meant to make the tick loop faster without
changing anything a strategy sees. This probe is the instrument that checks it: it trades a fixed
plan and, on every tick pass, every heartbeat pass and in every hook, records every READS method of
the decision trading API — the positions it kept from earlier passes first, before any call could
mark them. Run once with the portfolio cache on and once with it off, the two records must be
equal.

This decision logic is designed for TESTING, not production trading.

Plan steps (decision_logic_config.plan), for example:
    {"trigger": "tick", "at": 100, "action": "open", "direction": "LONG", "lots": 0.01}
    {"trigger": "heartbeat", "at": 200, "action": "open", "direction": "LONG", "lots": 0.01}
    {"trigger": "tick", "at": 1500, "action": "partial_close", "position_index": 0, "lots": 0.004}
    {"trigger": "tick", "at": 2200, "action": "close", "position_index": 1}
    {"trigger": "tick", "at": 600, "action": "rest_limit", "direction": "LONG", "lots": 0.01,
     "offset": 0.2}
    {"trigger": "tick", "at": 1200, "action": "cancel_resting", "resting_index": 0}
A tick step fires on that tick pass; a heartbeat step on the first heartbeat pass after that many
tick passes. position_index counts the positions the plan opened, in the order they filled.
rest_limit places a limit that far from the tick's mid — 0.2 is 20 % — so it rests rather than
fills; resting_index counts the orders it placed. A resting order is read through its mask on
every pass and kept, like a position, so its record shows it stays current (#654).
"""

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.trading_env.algo_masks.algo_order import AlgoOrder
from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.trading_env.algo_masks.algo_position import AlgoPosition
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
)
from python.framework.types.decision_event_types import (
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    PositionClosedEvent,
)
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats
from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderFeature,
    OrderSide,
    OrderType,
)
from python.framework.types.worker_types import WorkerRequirement, WorkerResult

# The READS methods of the decision trading API this probe calls on every pass. A test holds this
# set equal to the API's own declaration, so a new reading method is observed here or the suite fails
OBSERVED_DECISION_API_READS = frozenset({
    'get_open_positions', 'get_position', 'is_pending_close', 'get_symbol_spec', 'is_spot_mode',
    'get_asset_balance', 'get_free_entry_capital', 'get_cost_breakdown', 'has_pending_orders',
    'has_pipeline_orders', 'has_in_flight_operation', 'get_in_flight_operation',
    'get_order_capabilities', 'get_current_time', 'get_market_data_status', 'get_broker_name',
    'get_leverage', 'get_time_to_next_rollover', 'is_next_rollover_triple',
    'get_time_to_market_close', 'is_weekend_ahead', 'get_active_order_counts', 'get_active_orders',
    'get_order_history', 'get_account_info',
})

# How far ahead the probe asks whether a weekend comes
_WEEKEND_HORIZON = timedelta(hours=6)


class ObserverProbe(AbstractDecisionLogic):
    """
    Trades a fixed plan and records everything the decision trading API hands out, on every pass.

    Configuration:
        plan: The steps to trade, as in the module docstring
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize the observer probe.

        Args:
            name: Logic identifier
            logger: ScenarioLogger instance
            config: Configuration dict with the plan
            trading_context: TradingContext — names the symbol the probe trades
        """
        super().__init__(name, logger, config, trading_context=trading_context)
        self._plan: List[Dict[str, Any]] = list(self.params.get('plan'))
        self._symbol = trading_context.symbol if trading_context else ''
        self._tick_passes = 0
        self._pass_number = 0
        self._fired_steps: Set[int] = set()
        # The orders the plan sent, and the positions it opened, in the order they filled
        self._sent_order_ids: List[str] = []
        self._opening_order_ids: Set[str] = set()
        self._opened_position_ids: List[str] = []
        # A reference to each opened position, kept from the pass that first saw it — a strategy
        # that holds a position reads it without asking the API again
        self._kept_positions: Dict[str, AlgoPosition] = {}
        # The resting orders the plan placed, and a mask of each kept from the pass that first read
        # it — a strategy that holds an order reads it without asking the API again (#654)
        self._resting_order_ids: List[str] = []
        self._kept_orders: Dict[str, AlgoOrder] = {}
        self._observations: List[Dict[str, Any]] = []

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """Observer probe parameters."""
        return {
            'plan': InputParamDef(
                param_type=list,
                default=[],
                description='The steps to trade: trigger, at, action, direction, lots, position_index'
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Observer probe decision outputs."""
        return {
            'step': OutputParamDef(
                param_type=int,
                description='Index of the plan step this pass executes, -1 for none',
                category='INFO',
            ),
        }

    @classmethod
    def get_required_order_features(cls, decision_logic_config: Dict[str, Any]) -> List[OrderFeature]:
        """
        None: the probe sets no level on an order or a position.

        Args:
            decision_logic_config: Decision logic configuration dict

        Returns:
            An empty list
        """
        return []

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        The probe trades market orders only.

        Args:
            decision_logic_config: Decision logic configuration dict

        Returns:
            MARKET, and LIMIT where the plan rests an order
        """
        plan = decision_logic_config.get('plan') or []
        if any(step.get('action') == 'rest_limit' for step in plan):
            return [OrderType.MARKET, OrderType.LIMIT]
        return [OrderType.MARKET]

    @classmethod
    def get_subscribed_events(cls) -> Set[DecisionEventType]:
        """
        The events whose hooks the probe observes in.

        Returns:
            The order and position events
        """
        return {
            DecisionEventType.ORDER_FILLED,
            DecisionEventType.ORDER_REJECTED,
            DecisionEventType.ORDER_CANCELLED,
            DecisionEventType.PARTIAL_CLOSE,
            DecisionEventType.POSITION_CLOSED,
        }

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        """
        The sample probe worker — the probe reads none of its outputs, but a run needs a worker.

        Returns:
            Dict with the worker instance mapping
        """
        return {
            'probe_worker': WorkerRequirement.all('CORE/test_probes/sample_probe_worker')
        }

    def wants_heartbeat(self) -> bool:
        """
        Heartbeat passes are passes a decision can read in, and a fill can happen in one.

        Returns:
            True
        """
        return True

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """
        Deliberate pass: the probe's scenarios configure no stale-data window.

        Args:
            status: Session-level market-data health snapshot
        """
        pass

    # ============================================
    # Passes
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Observe, then name the plan step due on this tick pass.

        Args:
            tick: Current tick data
            worker_results: Worker outputs (not read)

        Returns:
            Decision carrying the step index
        """
        self._tick_passes += 1
        self._observe('tick', quote=(tick.bid, tick.ask))
        return self._decision_for(self._due_step('tick'))

    def compute_heartbeat(
        self,
        worker_results: Dict[str, WorkerResult],
    ) -> Optional[Decision]:
        """
        Observe, then name the plan step due on this heartbeat pass.

        Args:
            worker_results: Cached worker outputs (not read)

        Returns:
            Decision carrying the step index, or None when no step is due
        """
        self._observe('heartbeat')
        step_index = self._due_step('heartbeat')
        if step_index is None:
            return None
        return self._decision_for(step_index)

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData]
    ) -> Optional[AlgoOrderResult]:
        """
        Execute the plan step the decision names.

        Args:
            decision: Decision from a pass
            tick: Current tick data, None on a heartbeat pass

        Returns:
            AlgoOrderResult of the step's order, None when no step was due or nothing could be sent
        """
        step_index = decision.outputs.get('step', -1)
        if step_index < 0:
            return None
        step = self._plan[step_index]
        if step['action'] == 'rest_limit':
            return self._rest_limit(step, step_index, tick)
        if step['action'] == 'cancel_resting':
            if step['resting_index'] < len(self._resting_order_ids):
                self.trading_api.cancel_limit_order(self._resting_order_ids[step['resting_index']])
            return None
        if step['action'] == 'open':
            side = OrderSide.BUY if step['direction'] == 'LONG' else OrderSide.SELL
            result = self.trading_api.send_order(
                symbol=self._symbol,
                order_type=OrderType.MARKET,
                side=side,
                lots=step['lots'],
                comment=f'observer step {step_index}',
            )
            if result is not None:
                self._sent_order_ids.append(result.order_id)
                if not result.is_refused:
                    self._opening_order_ids.add(result.order_id)
            return result
        position_index = step['position_index']
        if position_index >= len(self._opened_position_ids):
            return None
        position_id = self._opened_position_ids[position_index]
        if self.trading_api.get_position(position_id) is None:
            return None
        result = self.trading_api.close_position(position_id, lots=step.get('lots'))
        self._sent_order_ids.append(result.order_id)
        return result

    def _rest_limit(self, step: Dict[str, Any], step_index: int,
                    tick: Optional[TickData]) -> Optional[AlgoOrderResult]:
        """
        Place a limit far enough from the market that it rests.

        Args:
            step: The plan step — its direction, lots and offset from the mid
            step_index: The step's index
            tick: The tick of the pass; None on a heartbeat pass, which places nothing

        Returns:
            The order's result, None on a heartbeat pass
        """
        if tick is None:
            return None
        long = step['direction'] == 'LONG'
        digits = self.trading_api.get_symbol_spec(self._symbol).digits
        price = round(tick.mid * (1 - step['offset'] if long else 1 + step['offset']), digits)
        result = self.trading_api.send_order(
            symbol=self._symbol,
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY if long else OrderSide.SELL,
            lots=step['lots'],
            price=price,
            comment=f'observer step {step_index}',
        )
        self._sent_order_ids.append(result.order_id)
        if not result.is_refused:
            self._resting_order_ids.append(result.order_id)
        return result

    # ============================================
    # Hooks — a hook is a pass a decision can read in
    # ============================================

    def on_cold_start(self, situation: ColdStartSituation) -> ColdStartVerdict:
        """
        The boot's question, asked of a logic that can leave an order resting: this probe runs in
        backtests, where no venue holds anything across a restart.

        Args:
            situation: What the boot found at the venue

        Returns:
            A declining verdict with the reason
        """
        return ColdStartVerdict(False, 'observer probe — no live venue to account for')

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        """
        Keep a reference to each position the plan opened, then observe.

        Args:
            event: The fill
        """
        if event.order_id in self._opening_order_ids and event.position_id:
            self._opened_position_ids.append(event.position_id)
            position = self.trading_api.get_position(event.position_id)
            if position is not None:
                self._kept_positions[event.position_id] = position
        self._observe('order_filled', event)

    def on_order_rejected(self, event: OrderRejectedEvent) -> None:
        """
        Observe.

        Args:
            event: The rejection
        """
        self._observe('order_rejected', event)

    def on_order_cancelled(self, event: OrderCancelledEvent) -> None:
        """
        Observe.

        Args:
            event: The cancel
        """
        self._observe('order_cancelled', event)

    def on_partial_close(self, event: PartialCloseEvent) -> None:
        """
        Observe.

        Args:
            event: The partial close
        """
        self._observe('partial_close', event)

    def on_position_closed(self, event: PositionClosedEvent) -> None:
        """
        Observe.

        Args:
            event: The close
        """
        self._observe('position_closed', event)

    # ============================================
    # Observation
    # ============================================

    def _observe(self, kind: str, event: Any = None,
                 quote: Optional[Tuple[float, float]] = None) -> None:
        """
        Record everything the decision can see now — the kept positions first, before any API call.

        Args:
            kind: The pass or hook observed in
            event: The hook's event, if any
            quote: The tick's bid and ask on a tick pass — what a current position is marked at
        """
        self._pass_number += 1
        record: Dict[str, Any] = {'pass': self._pass_number, 'kind': kind}
        if quote is not None:
            record['quote'] = {'bid': quote[0], 'ask': quote[1]}
        if event is not None:
            record['event'] = {item.name: _plain(getattr(event, item.name)) for item in fields(event)}
        record['kept'] = {position_id: _position_view(position)
                          for position_id, position in self._kept_positions.items()}
        record['kept_orders'] = {order_id: _plain(order)
                                 for order_id, order in self._kept_orders.items()}
        record['reads'] = self._read_everything()
        self._observations.append(record)

    def _read_everything(self) -> Dict[str, Any]:
        """
        Call every READS method of the decision trading API and record what it hands out.

        get_account_info comes last: it marks the positions as it reads, and every read before it
        must see them as the run left them.

        Returns:
            Method name → what it handed out, as plain values
        """
        api = self.trading_api
        spec = api.get_symbol_spec(self._symbol)
        reads: Dict[str, Any] = {}
        reads['get_open_positions'] = [_position_view(position) for position in api.get_open_positions()]
        reads['get_position'] = {position_id: _position_view(api.get_position(position_id))
                                 for position_id in self._opened_position_ids}
        reads['is_pending_close'] = {position_id: api.is_pending_close(position_id)
                                     for position_id in self._opened_position_ids}
        reads['get_symbol_spec'] = [spec.symbol, spec.base_currency, spec.quote_currency]
        reads['is_spot_mode'] = api.is_spot_mode()
        reads['get_asset_balance'] = {currency: api.get_asset_balance(currency)
                                      for currency in (spec.base_currency, spec.quote_currency)}
        reads['get_free_entry_capital'] = {
            direction.value: api.get_free_entry_capital(self._symbol, direction)
            for direction in (OrderDirection.LONG, OrderDirection.SHORT)}
        reads['get_cost_breakdown'] = _plain(api.get_cost_breakdown())
        reads['has_pending_orders'] = api.has_pending_orders()
        reads['has_pipeline_orders'] = api.has_pipeline_orders()
        reads['has_in_flight_operation'] = {order_id: api.has_in_flight_operation(order_id)
                                            for order_id in self._sent_order_ids}
        reads['get_in_flight_operation'] = {order_id: _plain(api.get_in_flight_operation(order_id))
                                            for order_id in self._sent_order_ids}
        reads['get_order_capabilities'] = _plain(api.get_order_capabilities())
        reads['get_current_time'] = _plain(api.get_current_time())
        reads['get_market_data_status'] = _plain(api.get_market_data_status())
        reads['get_broker_name'] = api.get_broker_name()
        reads['get_leverage'] = api.get_leverage()
        reads['get_time_to_next_rollover'] = _plain(api.get_time_to_next_rollover())
        reads['is_next_rollover_triple'] = api.is_next_rollover_triple(self._symbol)
        reads['get_time_to_market_close'] = _plain(api.get_time_to_market_close())
        reads['is_weekend_ahead'] = api.is_weekend_ahead(_WEEKEND_HORIZON)
        reads['get_active_order_counts'] = dict(api.get_active_order_counts())
        active_orders = api.get_active_orders()
        for order in active_orders:
            self._kept_orders.setdefault(order.pending_order_id, order)
        reads['get_active_orders'] = [_plain(order) for order in active_orders]
        try:
            reads['get_order_history'] = [_plain(order) for order in api.get_order_history()]
        except NotImplementedError:
            # Declared and not built yet, as the method says itself; once it hands out a
            # history, the history is recorded here like every other read
            reads['get_order_history'] = 'not implemented'
        reads['get_account_info'] = _plain(api.get_account_info(OrderDirection.LONG))
        return reads

    def _due_step(self, trigger: str) -> Optional[int]:
        """
        The first plan step due on this pass, marked as fired.

        Args:
            trigger: 'tick' or 'heartbeat'

        Returns:
            The step's index, or None
        """
        for index, step in enumerate(self._plan):
            if index in self._fired_steps or step['trigger'] != trigger:
                continue
            due = (self._tick_passes == step['at'] if trigger == 'tick'
                   else self._tick_passes >= step['at'])
            if due:
                self._fired_steps.add(index)
                return index
        return None

    def _decision_for(self, step_index: Optional[int]) -> Decision:
        """
        The decision that carries a plan step to execution.

        Args:
            step_index: The step's index, or None

        Returns:
            BUY or SELL for an open, FLAT otherwise; the step index in the outputs
        """
        if step_index is None:
            return Decision(action=DecisionLogicAction.FLAT, outputs={'step': -1})
        step = self._plan[step_index]
        action = DecisionLogicAction.FLAT
        if step['action'] == 'open':
            action = (DecisionLogicAction.BUY if step['direction'] == 'LONG'
                      else DecisionLogicAction.SELL)
        return Decision(action=action, outputs={'step': step_index})

    def get_statistics(self) -> DecisionLogicStats:
        """
        Statistics with the observation record.

        Returns:
            DecisionLogicStats with probe_metadata carrying the observations
        """
        base_stats = super().get_statistics()
        base_stats.probe_metadata = ProbeMetadata(
            tick_count=self._tick_passes,
            observations=self._observations,
        )
        return base_stats


def _position_view(position: Optional[AlgoPosition]) -> Optional[Dict[str, Any]]:
    """
    Every field the algo mask of a position shows, as plain values.

    Read from the mask's own field list, so a field the mask gains is observed without a change here.

    Args:
        position: The position's mask, or None

    Returns:
        Its fields, or None
    """
    if position is None:
        return None
    return {name: _plain(getattr(position, name)) for name in AlgoPosition.get_mask_fields()}

def _plain(value: Any) -> Any:
    """
    A value as plain data — what a comparison of two records can hold equal.

    Args:
        value: Any value the API hands out

    Returns:
        Its plain form: dataclasses and mappings recursively, times as ISO text, enums as values
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, AbstractAlgoMask):
        # A mask is read through its own field list, so a field it gains is recorded unasked
        return {name: _plain(getattr(value, name)) for name in type(value).get_mask_fields()}
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, 'model_dump'):
        return _plain(value.model_dump())
    return repr(value)
