"""
FiniexTestingIDE - Trend Channel Reference Decision Logic (didactic)

TEACHING EXAMPLE — NOT A PROFITABLE STRATEGY. This CORE decision logic exists to
demonstrate and validate the framework's full order surface end-to-end: resting
LIMIT and STOP entries, SL/TP set at submission, an always-on trailing stop, a
partial-close ladder, and multi-position stacking. It is a deliberately mechanical
multi-timeframe channel reference (an H1 trend gate + an M15 Bollinger channel) —
the standard textbook pattern, NOT the framework's alpha, and it makes no
profitability claim. The didactic defaults may be tuned freely for exploration;
the unified run report and the robustness validation tooling are the point, not
the P&L.

Strategy in one paragraph:
- H1 `CORE/ma_trend` is the directional gate (completed-bar-only, bar-close
  recompute): new longs only while the H1 trend is up, shorts only while down.
- M15 `CORE/bollinger` is the entry channel. Two configurable entry modes:
    * limit_pullback — buy a pullback to the lower band with a resting LIMIT
                       (maker); symmetric short at the upper band.
    * stop_breakout  — buy a breakout above the upper band with a resting STOP;
                       symmetric short below the lower band.
- Risk geometry is sized off the M15 band half-width (a local volatility unit, no
  ATR worker needed): SL/TP at submission, an always-on trailing stop that only
  ratchets in the profit direction, and a one-rung partial close at a configured
  R-multiple. Up to `max_positions` concurrent positions stack on the symbol.

Multi-position note: the backtest engine runs ONE symbol per scenario today
(portfolio multi-symbol is #369 / V1.5), so multi-position here means several
concurrent positions stacked on the same symbol — each with its own SL / TP /
trailing stop / partial close.
"""

import traceback
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.decision_logic_types import AwarenessLevel, Decision, DecisionLogicAction
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.worker_types import WorkerRequirement, WorkerResult
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OrderStatus,
    OrderType,
    OrderDirection,
    OrderSide,
    OrderResult,
)


class TrendChannelReference(AbstractDecisionLogic):
    """
    Didactic multi-timeframe channel reference — drives the full order surface.

    An H1 trend gate (CORE/ma_trend) plus an M15 Bollinger channel (CORE/bollinger).
    Two entry modes (limit_pullback / stop_breakout) place resting LIMIT or STOP
    entries with SL/TP; positions are managed with an always-on trailing stop, a
    one-rung partial close, and capped multi-position stacking. Reference / teaching
    example only — no profitability claim.

    Configuration options (see get_parameter_schema for ranges/defaults):
    - entry_mode: 'limit_pullback' (resting LIMIT) or 'stop_breakout' (resting STOP)
    - entry_band_pos: %B threshold that arms a pullback entry (limit_pullback)
    - breakout_offset_mult: STOP trigger distance beyond the band, in band halves
    - sl_mult / tp_mult: SL / TP distance from entry, in band halves
    - trail_mult: trailing-stop distance behind price, in band halves
    - partial_rr / partial_fraction: R-multiple rung and fraction of the original
      lots closed at that rung
    - max_positions: max concurrent positions stacked on the symbol
    - lot_size / min_free_margin: fixed entry size and the margin floor
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize the trend-channel reference logic.

        Args:
            name: Logic identifier
            logger: ScenarioLogger instance
            config: Configuration dict / ValidatedParameters with the schema params
            trading_context: TradingContext (optional)
        """
        super().__init__(name, logger, config, trading_context=trading_context)

        # All values guaranteed present by schema defaults + factory validation
        self.entry_mode = self.params.get('entry_mode')
        self.entry_band_pos = self.params.get('entry_band_pos')
        self.breakout_offset_mult = self.params.get('breakout_offset_mult')
        self.sl_mult = self.params.get('sl_mult')
        self.tp_mult = self.params.get('tp_mult')
        self.trail_mult = self.params.get('trail_mult')
        self.partial_rr = self.params.get('partial_rr')
        self.partial_fraction = self.params.get('partial_fraction')
        self.max_positions = self.params.get('max_positions')
        self.lot_size = self.params.get('lot_size')
        self.min_free_margin = self.params.get('min_free_margin')

        # ============================================
        # Per-tick worker read (stashed for the execution pass)
        # ============================================
        self._gate: str = 'neutral'
        self._upper: float = 0.0
        self._lower: float = 0.0
        self._band_half: float = 0.0

        # ============================================
        # Order / position bookkeeping
        # ============================================
        # resting entry order_id → {symbol, direction, order_type, price, sl, tp}
        self._resting_entries: Dict[str, Dict[str, Any]] = {}
        # position_id → initial risk distance (|entry − initial SL|) for R-multiple
        self._initial_risk: Dict[str, float] = {}
        # position_ids that already had their partial-close rung executed
        self._partial_done: set = set()

        self.logger.debug(
            f"TrendChannelReference initialized: mode={self.entry_mode}, "
            f"sl={self.sl_mult} tp={self.tp_mult} trail={self.trail_mult}, "
            f"partial={self.partial_fraction}@R{self.partial_rr}, "
            f"max_pos={self.max_positions}, lots={self.lot_size}"
        )

    # ============================================
    # STATIC: classmethods for factory / UI
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """TrendChannelReference parameters with validation ranges."""
        return {
            'entry_mode': InputParamDef(
                param_type=str, default='limit_pullback',
                choices=('limit_pullback', 'stop_breakout'),
                description="Resting entry style: LIMIT pullback or STOP breakout",
                display=True, display_label='mode',
            ),
            'entry_band_pos': InputParamDef(
                param_type=float, default=0.15, min_val=0.0, max_val=0.5,
                description="%B threshold that arms a pullback entry (limit_pullback)",
                display=True, display_label='band_x',
            ),
            'breakout_offset_mult': InputParamDef(
                param_type=float, default=0.25, min_val=0.0, max_val=2.0,
                description="STOP trigger distance beyond the band, in band halves",
            ),
            'sl_mult': InputParamDef(
                param_type=float, default=1.0, min_val=0.1, max_val=5.0,
                description="Stop-loss distance from entry, in band halves",
                display=True, display_label='sl_x',
            ),
            'tp_mult': InputParamDef(
                param_type=float, default=2.0, min_val=0.1, max_val=10.0,
                description="Take-profit distance from entry, in band halves",
                display=True, display_label='tp_x',
            ),
            'trail_mult': InputParamDef(
                param_type=float, default=1.0, min_val=0.1, max_val=5.0,
                description="Trailing-stop distance behind price, in band halves",
                display=True, display_label='trail_x',
            ),
            'partial_rr': InputParamDef(
                param_type=float, default=1.0, min_val=0.1, max_val=10.0,
                description="R-multiple rung at which the partial close fires",
                display=True, display_label='p_rr',
            ),
            'partial_fraction': InputParamDef(
                param_type=float, default=0.5, min_val=0.0, max_val=1.0,
                description="Fraction of the original lots closed at the partial rung",
                display=True, display_label='p_frac',
            ),
            'max_positions': InputParamDef(
                param_type=int, default=2, min_val=1, max_val=10,
                description="Max concurrent positions stacked on the symbol",
                display=True, display_label='max_pos',
            ),
            'lot_size': InputParamDef(
                param_type=float, default=0.1, min_val=0.0, max_val=100.0,
                description="Fixed lot size for entries",
            ),
            'min_free_margin': InputParamDef(
                param_type=float, default=1000, min_val=0,
                description="Minimum free margin required before opening an entry",
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """TrendChannelReference decision output parameters."""
        return {
            'gate': OutputParamDef(
                param_type=str, choices=('up', 'down', 'neutral'),
                description='H1 trend gate direction',
                category='SIGNAL', display=True, display_label='gate',
            ),
            'mode': OutputParamDef(
                param_type=str,
                description='Active entry mode',
                category='INFO',
            ),
            'entry_price': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Resting entry price (limit/stop) when armed, else 0',
                category='SIGNAL',
            ),
            'stop_loss': OutputParamDef(
                param_type=float, min_val=0.0,
                description='SL price for the armed entry, else 0',
                category='SIGNAL',
            ),
            'take_profit': OutputParamDef(
                param_type=float, min_val=0.0,
                description='TP price for the armed entry, else 0',
                category='SIGNAL',
            ),
            'band_width': OutputParamDef(
                param_type=float, min_val=0.0,
                description='M15 band width (upper − lower) at decision time',
                category='INFO',
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
            'timestamp': OutputParamDef(
                param_type=str,
                description='ISO format UTC timestamp at decision time',
                category='INFO',
            ),
        }

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """Didactic CORE reference logic — teaching example, no profitability claim."""
        return ComponentMetadata(
            version='1.1.0',
            doc_link='docs/user_guides/trend_channel_reference_guide.md',
            recommended_markets=('forex',),
        )

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        """
        Programmed market-outage reaction (#436): hold and wait.

        Resting entry orders stay broker-side by design; the OrderGuard blocks
        new entries while stale. The blind moment is surfaced and the logic
        waits for ticks to resume.

        Args:
            status: Session-level market-data health snapshot
        """
        self.logger.warning(
            f"🔌 Market data stale ({status.seconds_since_last_tick:.0f}s "
            f"since last tick) — holding until ticks resume."
        )
        self.emit_event(
            '🔌 market data stale — holding until ticks resume',
            AwarenessLevel.NOTICE, 'market_data_stale')

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        Declare required order types based on the configured entry mode.

        MARKET covers closes; the resting entry type is LIMIT (limit_pullback) or
        STOP (stop_breakout). The capability check rejects a broker that cannot
        rest the configured order type before any subprocess starts.

        Args:
            decision_logic_config: Decision logic configuration dict

        Returns:
            List of OrderType this logic will submit
        """
        mode = decision_logic_config.get('entry_mode', 'limit_pullback')
        resting = OrderType.STOP if mode == 'stop_breakout' else OrderType.LIMIT
        return [OrderType.MARKET, resting]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        """
        Declare required worker instances + consumed signals (#425).

        H1 ma_trend is the directional gate, M15 Bollinger the entry channel.
        Reads all outputs (SUBSCRIBE_ALL).

        Returns:
            Dict[instance_name, WorkerRequirement] — H1 trend gate + M15 channel
        """
        return {
            'h1_trend': WorkerRequirement.all('CORE/ma_trend'),
            'm15_channel': WorkerRequirement.all('CORE/bollinger'),
        }

    # ============================================
    # DYNAMIC: compute (signal) + execute (orders)
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Read the gate + channel and produce the entry intent for this tick.

        Stashes the gate direction and the M15 band geometry for the execution
        pass (trailing / re-pricing read them). Narrates every terminal path.

        Args:
            tick: Current tick data
            worker_results: Results from the h1_trend and m15_channel workers

        Returns:
            Decision with BUY/SELL intent (+ entry geometry) or FLAT
        """
        h1 = worker_results.get('h1_trend')
        m15 = worker_results.get('m15_channel')

        if not h1 or not m15:
            self.notify_awareness('Missing worker results', AwarenessLevel.NOTICE, 'no_workers')
            return self._flat(tick, 'Missing worker results')

        gate = h1.get_signal('direction')
        upper = m15.get_signal('upper')
        lower = m15.get_signal('lower')
        pos_raw = m15.get_signal('position_raw')

        band_half = (upper - lower) / 2.0

        # Stash for the execution pass (trailing / re-pricing)
        self._gate = gate
        self._upper = upper
        self._lower = lower
        self._band_half = band_half

        # Degenerate band (flat market) — no risk unit, no entry
        if band_half <= 0.0:
            self.notify_awareness('Flat band — no entry', AwarenessLevel.INFO, 'flat_band')
            return self._flat(tick, 'Flat band (zero width)')

        if gate == 'neutral':
            self.notify_awareness('H1 gate neutral — waiting', AwarenessLevel.INFO, 'gate_neutral')
            return self._flat(tick, 'H1 trend neutral')

        # Direction allowed by the gate
        side = OrderSide.BUY if gate == 'up' else OrderSide.SELL
        entry_price, stop_loss, take_profit = self._entry_geometry(side)

        if not self._is_armed(side, tick.mid, entry_price, pos_raw):
            self.notify_awareness(
                f"Gate {gate} — no {self.entry_mode} setup (%B {pos_raw:.2f})",
                AwarenessLevel.INFO, 'no_setup',
            )
            return self._flat(tick, f"No {self.entry_mode} setup")

        action = DecisionLogicAction.BUY if side == OrderSide.BUY else DecisionLogicAction.SELL
        self.notify_awareness(
            f"{self.entry_mode} {side.value} armed @ {entry_price:.5f} "
            f"(gate {gate}, %B {pos_raw:.2f})",
            AwarenessLevel.INFO, 'armed',
        )
        return Decision(
            action=action,
            outputs={
                'gate': gate,
                'mode': self.entry_mode,
                'entry_price': float(entry_price),
                'stop_loss': float(stop_loss),
                'take_profit': float(take_profit),
                'band_width': float(upper - lower),
                'reason': f"{self.entry_mode} {side.value} armed",
                'price': tick.mid,
                'timestamp': tick.timestamp.isoformat(),
            },
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Manage open positions + resting entries, then place a new entry if armed.

        Called every tick. Order of operations:
        1. Open positions → partial close at the R rung, then trail the stop.
        2. Resting entries → fill detection, cancel on gate-flip, re-price.
        3. New entry → place a resting LIMIT/STOP if armed and capacity allows.

        Args:
            decision: Decision from compute_tick
            tick: Current tick data

        Returns:
            OrderResult if a new entry order was submitted, None otherwise
        """
        if not self.trading_api:
            self.logger.warning('Trading API not available - cannot execute')
            return None

        self._manage_open_positions(tick)
        self._manage_resting_entries(tick)

        if decision.action in (DecisionLogicAction.BUY, DecisionLogicAction.SELL):
            return self._try_open_entry(decision, tick)

        return None

    # ============================================
    # Entry geometry + placement
    # ============================================

    def _entry_geometry(self, side: OrderSide) -> tuple:
        """
        Compute (entry_price, stop_loss, take_profit) from the M15 band edges.

        Geometry is sized off the band half-width (the local volatility unit). Pure —
        no arming decision and no price-space division (the band position is read from the
        Normalizer-derived worker output, see _is_armed).

        Args:
            side: BUY (up-gate) or SELL (down-gate)

        Returns:
            (entry_price, stop_loss, take_profit) for the configured entry mode
        """
        bh = self._band_half
        sl_d = self.sl_mult * bh
        tp_d = self.tp_mult * bh

        if self.entry_mode == 'limit_pullback':
            if side == OrderSide.BUY:
                entry = self._lower
                return entry, entry - sl_d, entry + tp_d
            entry = self._upper
            return entry, entry + sl_d, entry - tp_d

        # stop_breakout — resting STOP beyond the band (momentum continuation)
        offset = self.breakout_offset_mult * bh
        if side == OrderSide.BUY:
            entry = self._upper + offset
            return entry, entry - sl_d, entry + tp_d
        entry = self._lower - offset
        return entry, entry + sl_d, entry - tp_d

    def _is_armed(self, side: OrderSide, price: float, entry: float, pos_raw: float) -> bool:
        """
        Decide whether the current channel read arms an entry.

        Uses the worker's %B (position_raw, Normalizer-derived) for the pullback, and the
        STOP-validity check (trigger beyond price) for the breakout.

        Args:
            side: BUY or SELL
            price: Current mid price
            entry: Planned entry price (from _entry_geometry)
            pos_raw: M15 band position (%B) from the channel worker

        Returns:
            True if an entry should be placed
        """
        if self.entry_mode == 'limit_pullback':
            if side == OrderSide.BUY:
                return pos_raw <= self.entry_band_pos
            return pos_raw >= (1.0 - self.entry_band_pos)
        # stop_breakout — the STOP trigger must sit beyond the current price
        if side == OrderSide.BUY:
            return price < entry
        return price > entry

    def _try_open_entry(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Place a resting LIMIT/STOP entry if capacity and margin allow.

        One pending entry at a time paces the stacking; `max_positions` caps the
        concurrent count on the symbol.

        Args:
            decision: Armed BUY/SELL decision carrying the entry geometry
            tick: Current tick data

        Returns:
            OrderResult if an entry was submitted, None otherwise
        """
        symbol = tick.symbol

        # Capacity — count filled positions + the resting entry (one at a time)
        if len(self._resting_entries) > 0:
            return None
        open_count = len(self.trading_api.get_open_positions(symbol))
        if open_count >= self.max_positions:
            return None
        if self.trading_api.has_pipeline_orders():
            return None

        side = OrderSide.BUY if decision.action == DecisionLogicAction.BUY else OrderSide.SELL
        direction = OrderDirection.LONG if side == OrderSide.BUY else OrderDirection.SHORT

        # Spot SELL: the algo owns the base-currency check (margin mode skips this).
        if side == OrderSide.SELL and self.trading_api.is_spot_mode():
            spec = self.trading_api.get_symbol_spec(symbol)
            required = self.lot_size * spec.contract_size
            if self.trading_api.get_asset_balance(spec.base_currency) < required:
                return None

        account = self.trading_api.get_account_info(direction)
        if account.free_margin < self.min_free_margin:
            return None

        entry_price = decision.outputs['entry_price']
        stop_loss = decision.outputs['stop_loss']
        take_profit = decision.outputs['take_profit']
        order_type = OrderType.STOP if self.entry_mode == 'stop_breakout' else OrderType.LIMIT
        limit_price = entry_price if order_type == OrderType.LIMIT else None
        stop_price = entry_price if order_type == OrderType.STOP else None

        try:
            result = self.trading_api.send_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                lots=self.lot_size,
                price=limit_price,
                stop_price=stop_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=f"TrendChannelRef {self.entry_mode} {side.value}",
            )
        except Exception:
            self.logger.error(f"❌ Entry submission failed:\n{traceback.format_exc()}")
            return None

        if result and not result.is_rejected:
            self._resting_entries[result.order_id] = {
                'symbol': symbol,
                'direction': direction,
                'order_type': order_type,
                'price': entry_price,
                'sl': stop_loss,
                'tp': take_profit,
            }
            self.emit_event(
                f"{order_type.value} {side.value} resting @ {entry_price:.5f} "
                f"SL {stop_loss:.5f} TP {take_profit:.5f}",
                AwarenessLevel.INFO, 'entry_submitted',
            )
            self._record_setup_diagnostic(side, decision, tick)
        elif result and result.is_rejected:
            self.logger.warning(
                f"✗ Entry rejected: "
                f"{result.rejection_reason.value if result.rejection_reason else 'Unknown'}"
            )

        return result

    # ============================================
    # Open-position management — partial close + trailing
    # ============================================

    def _manage_open_positions(self, tick: TickData) -> None:
        """
        Run the partial-close rung and the always-on trailing stop per position.

        Args:
            tick: Current tick data
        """
        for pos in self.trading_api.get_open_positions(tick.symbol):
            pid = pos.position_id

            # Seed the initial risk if the fill was not seen via the resting reconcile
            if pid not in self._initial_risk and pos.stop_loss is not None:
                self._initial_risk[pid] = abs(pos.entry_price - pos.stop_loss)

            if self.trading_api.is_pending_close(pid):
                continue

            self._maybe_partial_close(pos)
            self._maybe_trail(pos)

    def _maybe_partial_close(self, pos) -> None:
        """
        Close `partial_fraction` of the original lots once the R rung is reached.

        Args:
            pos: Open Position
        """
        pid = pos.position_id
        if pid in self._partial_done:
            return
        if self._current_r(pos) < self.partial_rr:
            return

        close_lots = round(pos.original_lots * self.partial_fraction, 2)
        if close_lots < 0.01 or (pos.lots - close_lots) < 0.01:
            return

        self.trading_api.close_position(pid, lots=close_lots)
        self._partial_done.add(pid)
        self.emit_event(
            f"Partial close {close_lots} lots @ {self.partial_rr:.1f}R {pid}",
            AwarenessLevel.NOTICE, 'partial_close',
        )

    def _maybe_trail(self, pos) -> None:
        """
        Ratchet the stop loss toward price in the profit direction (never backward).

        Args:
            pos: Open Position
        """
        pid = pos.position_id
        if self.trading_api.has_in_flight_operation(pid):
            return

        # Trail in R-units off the position's own initial risk (stable distance),
        # not the live band — a calming market must not collapse the trail.
        risk = self._initial_risk.get(pid) or self._band_half
        offset = self.trail_mult * risk
        if offset <= 0.0:
            return
        epsilon = risk * 0.1   # ignore sub-noise moves (no modify spam)
        price = pos.current_price

        if pos.direction == OrderDirection.LONG:
            new_sl = price - offset
            if pos.stop_loss is None or new_sl > pos.stop_loss + epsilon:
                self.trading_api.modify_position(pid, stop_loss=new_sl)
        else:
            new_sl = price + offset
            if pos.stop_loss is None or new_sl < pos.stop_loss - epsilon:
                self.trading_api.modify_position(pid, stop_loss=new_sl)

    def _current_r(self, pos) -> float:
        """
        Current R-multiple of an open position (favourable move / initial risk).

        Args:
            pos: Open Position

        Returns:
            R-multiple, or 0.0 when the initial risk is unknown
        """
        risk = self._initial_risk.get(pos.position_id)
        if not risk:
            return 0.0
        if pos.direction == OrderDirection.LONG:
            move = pos.current_price - pos.entry_price
        else:
            move = pos.entry_price - pos.current_price
        return move / risk

    # ============================================
    # Resting-entry management — fill / cancel / re-price
    # ============================================

    def _manage_resting_entries(self, tick: TickData) -> None:
        """
        Promote filled entries, cancel on gate-flip, re-price on band drift.

        Args:
            tick: Current tick data
        """
        active_ids = {o.pending_order_id for o in self.trading_api.get_active_orders()}

        for oid in list(self._resting_entries.keys()):
            info = self._resting_entries[oid]

            pos = self.trading_api.get_position(oid)
            if pos is not None:
                # Filled — the resting order id becomes the position id
                self._initial_risk[oid] = abs(pos.entry_price - info['sl'])
                del self._resting_entries[oid]
                self.emit_event(
                    f"Entry filled {oid} @ {pos.entry_price:.5f}",
                    AwarenessLevel.INFO, 'entry_filled',
                )
                continue

            # Gate flipped away from the entry direction → cancel the resting order
            if self._gate_flipped_against(info['direction']):
                self._cancel_resting(oid, info)
                continue

            # Re-price toward the current band edge while it rests (bar-close bounded)
            if oid in active_ids:
                self._maybe_reprice(oid, info, tick.mid)

    def _gate_flipped_against(self, direction: OrderDirection) -> bool:
        """
        Has the H1 gate turned away from a resting entry's direction?

        Args:
            direction: The resting entry's direction

        Returns:
            True if the gate no longer supports that direction
        """
        if direction == OrderDirection.LONG:
            return self._gate != 'up'
        return self._gate != 'down'

    def _cancel_resting(self, oid: str, info: Dict[str, Any]) -> None:
        """
        Cancel a resting entry order; keep tracking if the cancel did not apply.

        Args:
            oid: Resting order id
            info: Tracked order info
        """
        if self.trading_api.has_in_flight_operation(oid):
            return
        if info['order_type'] == OrderType.STOP:
            cancelled = self.trading_api.cancel_stop_order(oid)
        else:
            cancelled = self.trading_api.cancel_limit_order(oid)
        if cancelled:
            del self._resting_entries[oid]
            self.emit_event(
                f"Cancel resting entry {oid} (gate flip)",
                AwarenessLevel.NOTICE, 'entry_cancelled',
            )

    def _maybe_reprice(self, oid: str, info: Dict[str, Any], price: float) -> None:
        """
        Re-price a resting entry (and its SL/TP) toward the current band edge.

        Bounded by the M15 bar-close cadence (the band only moves on a close) and guarded
        against an in-flight modify; the re-priced order must stay valid relative to price
        (a LIMIT below / a STOP above for a buy, mirrored for a sell).

        Args:
            oid: Resting order id
            info: Tracked order info
            price: Current mid price
        """
        if self.trading_api.has_in_flight_operation(oid):
            return

        side = OrderSide.BUY if info['direction'] == OrderDirection.LONG else OrderSide.SELL
        new_price, new_sl, new_tp = self._entry_geometry(side)
        if abs(new_price - info['price']) <= self._band_half * 0.5:
            return

        # Keep the resting order on the correct side of price after the move
        if info['order_type'] == OrderType.STOP:
            if (side == OrderSide.BUY and new_price <= price) or \
               (side == OrderSide.SELL and new_price >= price):
                return
            self.trading_api.modify_stop_order(
                oid, stop_price=new_price, stop_loss=new_sl, take_profit=new_tp)
        else:
            if (side == OrderSide.BUY and new_price >= price) or \
               (side == OrderSide.SELL and new_price <= price):
                return
            self.trading_api.modify_limit_order(
                oid, price=new_price, stop_loss=new_sl, take_profit=new_tp)
        info['price'], info['sl'], info['tp'] = new_price, new_sl, new_tp
        self.emit_event(
            f"Re-price resting entry {oid} → {new_price:.5f}",
            AwarenessLevel.INFO, 'entry_repriced',
        )

    # ============================================
    # Strategy-owned diagnostics (#376)
    # ============================================

    def _record_setup_diagnostic(
        self,
        side: OrderSide,
        decision: Decision,
        tick: TickData,
    ) -> None:
        """
        Append one row to the setup-funnel diagnostics CSV per submitted entry.

        Args:
            side: Entry side
            decision: The armed decision (carries the geometry)
            tick: Current tick (for the timestamp)
        """
        self.diagnostics_csv(
            'trend_channel_setups',
            ['timestamp', 'symbol', 'mode', 'side', 'gate',
             'entry_price', 'stop_loss', 'take_profit', 'band_width'],
        ).append_row({
            'timestamp': tick.timestamp.isoformat(),
            'symbol': tick.symbol,
            'mode': self.entry_mode,
            'side': side.value,
            'gate': decision.outputs['gate'],
            'entry_price': round(decision.outputs['entry_price'], 5),
            'stop_loss': round(decision.outputs['stop_loss'], 5),
            'take_profit': round(decision.outputs['take_profit'], 5),
            'band_width': round(decision.outputs['band_width'], 5),
        })

    # ============================================
    # Helpers
    # ============================================

    def _flat(self, tick: TickData, reason: str) -> Decision:
        """
        Build a FLAT decision with the standard output payload.

        Args:
            tick: Current tick data
            reason: Human-readable explanation

        Returns:
            FLAT Decision
        """
        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={
                'gate': self._gate,
                'mode': self.entry_mode,
                'entry_price': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'band_width': float(self._upper - self._lower),
                'reason': reason,
                'price': tick.mid,
                'timestamp': tick.timestamp.isoformat(),
            },
        )
