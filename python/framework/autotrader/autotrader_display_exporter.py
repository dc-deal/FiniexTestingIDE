"""
FiniexTestingIDE - AutoTrader Display Exporter
Builds AutoTraderDisplayStats snapshots from live pipeline state (#400).

Extracted from AutotraderTickLoop so the loop stays orchestration-only.
Holds the stable collaborators; the volatile per-frame state (safety,
rejections, spot-equity baseline) is passed per call.
"""

import queue
from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.drift_auditor import DriftAuditor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
    RejectionEntry,
    SafetyState,
    TradeHistoryEntry,
)
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_logic_types import Decision
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.parameter_types import OutputValue
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class AutotraderDisplayExporter:
    """
    Builds AutoTraderDisplayStats snapshots from live pipeline state.

    The stable collaborators (executor, orchestrator, monitors, config) live on
    the exporter; the volatile per-frame state (safety, rejections, spot-equity
    baseline) is passed into build(). Called after the algo pipeline — never on
    the critical path.

    Args:
        config: AutoTrader configuration
        executor: LiveTradeExecutor (portfolio, positions, orders, trade history)
        worker_orchestrator: WorkerOrchestrator (worker performance + outputs)
        decision_logic: DecisionLogic (decision outputs, params, awareness, events)
        clipping_monitor: LiveClippingMonitor (display stats)
        tick_queue: Thread-safe tick queue (for queue depth)
        trading_model: SPOT or MARGIN
        base_currency: Symbol base currency
        quote_currency: Symbol quote currency
        session_start: Session start time (UTC)
        dry_run: Whether this is a dry-run session
        display_label_cache: Cached display=True key lists
        drift_auditor: DriftAuditor (footer counters, optional)
        reconciler: Reconciler (status line, optional)
        api_monitor: ApiPerfMonitor (API performance panel, optional)
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        worker_orchestrator: WorkerOrchestrator,
        decision_logic: AbstractDecisionLogic,
        clipping_monitor: LiveClippingMonitor,
        tick_queue: queue.Queue,
        trading_model: TradingModel,
        base_currency: str,
        quote_currency: str,
        session_start: datetime,
        dry_run: bool,
        display_label_cache: DisplayLabelCache,
        drift_auditor: Optional[DriftAuditor] = None,
        reconciler: Optional[Reconciler] = None,
        api_monitor: Optional[ApiPerfMonitor] = None,
    ):
        self._config = config
        self._executor = executor
        self._worker_orchestrator = worker_orchestrator
        self._decision_logic = decision_logic
        self._clipping_monitor = clipping_monitor
        self._tick_queue = tick_queue
        self._trading_model = trading_model
        self._base_currency = base_currency
        self._quote_currency = quote_currency
        self._session_start = session_start
        self._dry_run = dry_run
        self._display_label_cache = display_label_cache
        self._drift_auditor = drift_auditor
        self._reconciler = reconciler
        self._api_monitor = api_monitor

    def build_startup_pulse(self, seconds_since_start: float) -> AutoTraderDisplayStats:
        """
        Build a slim pre-first-tick pulse frame (#320).

        Carries only what's known before any market data has arrived: session
        identity, configured account, and the wall-clock delta since session
        start. The renderer flags the `ticks_processed == 0 + is_pulse` combo
        and shows `💓 Ns since startup` instead of the default wait label.

        Args:
            seconds_since_start: Wall-clock seconds since session_start

        Returns:
            Minimal AutoTraderDisplayStats with is_pulse=True
        """
        portfolio = self._executor.portfolio
        return AutoTraderDisplayStats(
            core=LiveCoreSnapshot(
                symbol=self._config.symbol,
                ticks_processed=0,
                balance=portfolio.balance,
                initial_balance=portfolio.initial_balance,
            ),
            session_start=self._session_start,
            dry_run=self._dry_run,
            broker_type=self._config.broker_type,
            config_hash=self._executor.broker.config_hash,
            equity=portfolio.balance,
            spot_balances=portfolio.get_balances() if self._trading_model == TradingModel.SPOT else None,
            account_currency=self._executor.account_currency,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            trading_model=self._trading_model.value,
            is_pulse=True,
            seconds_since_last_tick=seconds_since_start,
            **self._drift_display_counters(),
            **self._reconcile_display_counters(),
            api_perf=self._api_perf_snapshot(),
        )

    def build(
        self,
        decision: Decision,
        ticks_processed: int,
        tick: TickData,
        safety: SafetyState,
        recent_rejections: List[RejectionEntry],
        total_rejections: int,
        initial_spot_equity: float,
    ) -> AutoTraderDisplayStats:
        """
        Build display stats snapshot from current pipeline state.

        Reads portfolio dirty-state (attribute reads, zero cost),
        open positions (0-3 in live), active orders, worker outputs.

        Args:
            decision: Current tick's decision
            ticks_processed: Tick counter
            tick: Current tick (for last_price)
            safety: Circuit-breaker state from the tick loop
            recent_rejections: Rolling rejection buffer (newest last)
            total_rejections: Running rejection count
            initial_spot_equity: Spot equity baseline captured on first tick

        Returns:
            AutoTraderDisplayStats snapshot for display queue
        """
        portfolio = self._executor.portfolio

        # Ensure P&L is current before snapshotting (display only, not every tick)
        portfolio.ensure_positions_updated()

        # Mid price (needed early for spot equity)
        last_price = (tick.bid + tick.ask) / 2.0

        # Equity + spot balances — spot mode reads balances directly (O(1), no cache trigger)
        if self._trading_model == TradingModel.SPOT:
            equity = portfolio.get_spot_equity(last_price)
            spot_balances = portfolio.get_balances()
        else:
            # Margin mode: balance + unrealized P&L (positions already updated above)
            unrealized = sum(pos.unrealized_pnl for pos in portfolio.open_positions.values())
            equity = portfolio.balance + unrealized
            spot_balances = None

        # Open positions → PositionSnapshot
        open_positions = []
        for pos in self._executor.get_open_positions():
            open_positions.append(PositionSnapshot(
                position_id=pos.position_id,
                symbol=pos.symbol,
                direction=pos.direction,
                lots=pos.lots,
                entry_price=pos.entry_price,
                unrealized_pnl=pos.unrealized_pnl,
                entry_trades=list(pos.entry_trades),
            ))

        # Active orders (limit + stop) from pending stats
        pending_stats = self._executor.get_pending_stats()
        active_orders = list(pending_stats.active_limit_orders) + \
            list(pending_stats.active_stop_orders)
        pipeline_count = pending_stats.latency_queue_count

        # Trade history — last 10, newest first
        recent_trades: List[TradeHistoryEntry] = []
        trade_history = self._executor.get_trade_history()
        for trade in trade_history[-10:][::-1]:
            recent_trades.append(TradeHistoryEntry(
                trade_id=trade.position_id,
                symbol=trade.symbol,
                direction=trade.direction,
                lots=trade.lots,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                net_pnl=trade.net_pnl,
                close_reason=trade.close_reason,
                close_type=trade.close_type,
                entry_trades=list(trade.entry_trades),
                exit_trades=list(trade.exit_trades),
                exit_side=trade.exit_side,
                exit_time=trade.exit_time,
            ))

        # Clipping — lightweight snapshot (avoids full session summary construction)
        clipping_stats = self._clipping_monitor.get_display_stats()

        # Worker performance + outputs (display=True only — keys from cache)
        worker_times: Dict[str, float] = {}
        worker_max_times: Dict[str, float] = {}
        worker_rolling_avgs: Dict[str, float] = {}
        worker_outputs: Dict[str, Dict[str, OutputValue]] = {}
        worker_display_keys = self._display_label_cache.worker_display_output_keys
        for name, worker in self._worker_orchestrator.workers.items():
            # Performance
            if worker.performance_logger:
                stats = worker.performance_logger.get_stats()
                worker_times[name] = stats.worker_avg_time_ms
                worker_max_times[name] = stats.worker_max_time_ms
                worker_rolling_avgs[name] = worker.performance_logger.get_rolling_avg_ms()

            # Outputs (cached display=True key list, no per-tick schema read)
            display_keys = worker_display_keys.get(name)
            if not display_keys:
                continue
            result = self._worker_orchestrator.get_worker_result(name)
            if result:
                display_outputs = {
                    key: result.outputs[key]
                    for key in display_keys
                    if key in result.outputs
                }
                if display_outputs:
                    worker_outputs[name] = display_outputs

        # Feed-status envelope (#434): any stale worker result flags the feed line
        feed_stale = any(
            result is not None and result.is_stale
            for result in (
                self._worker_orchestrator.get_worker_result(name)
                for name in self._worker_orchestrator.workers
            )
        )

        # Market-data staleness contract (#436): session-level tick-stream state
        market_stale = self._executor.get_market_data_status().is_stale

        # Decision outputs (display=True)
        decision_outputs: Dict[str, OutputValue] = {}
        decision_schema = self._decision_logic.__class__.get_output_schema()
        for key, param_def in decision_schema.items():
            if param_def.display and key in decision.outputs:
                decision_outputs[key] = decision.outputs[key]

        # Decision logic config params (static, but read fresh to support future live reload)
        config_params: Dict[str, OutputValue] = {}
        for raw_key, _display_key in self._display_label_cache.config_param_specs:
            if self._decision_logic.params.has(raw_key):
                config_params[raw_key] = self._decision_logic.params.get(raw_key)

        # Tick rate (session average)
        uptime_min = max(0.001, (datetime.now(timezone.utc) -
                         self._session_start).total_seconds() / 60.0)
        ticks_per_min = ticks_processed / uptime_min

        # Spot mode: P&L baseline = full equity at session start (USD + crypto × first_price)
        # Margin mode: P&L baseline = account balance at session start
        if self._trading_model == TradingModel.SPOT and initial_spot_equity > 0:
            initial_balance_for_display = initial_spot_equity
        else:
            initial_balance_for_display = portfolio.initial_balance

        return AutoTraderDisplayStats(
            core=LiveCoreSnapshot(
                symbol=self._config.symbol,
                ticks_processed=ticks_processed,
                balance=portfolio.balance,
                initial_balance=initial_balance_for_display,
                total_trades=portfolio.get_total_trades(),
                winning_trades=portfolio.get_winning_trades(),
                losing_trades=portfolio.get_losing_trades(),
                last_awareness=self._decision_logic.get_last_awareness(),
            ),
            session_start=self._session_start,
            dry_run=self._dry_run,
            broker_type=self._config.broker_type,
            config_hash=self._executor.broker.config_hash,
            equity=equity,
            spot_balances=spot_balances,
            open_positions=open_positions,
            active_orders=active_orders,
            pipeline_count=pipeline_count,
            recent_trades=recent_trades,
            clipping_ratio=clipping_stats.clipping_ratio,
            avg_processing_ms=clipping_stats.avg_processing_ms,
            max_processing_ms=clipping_stats.max_processing_ms,
            queue_depth=self._tick_queue.qsize(),
            total_ticks_clipped=clipping_stats.ticks_clipped,
            processing_times_ms=clipping_stats.processing_times_ms,
            ticks_per_min=ticks_per_min,
            last_price=last_price,
            worker_times_ms=worker_times,
            worker_max_times_ms=worker_max_times,
            worker_rolling_avg_times_ms=worker_rolling_avgs,
            worker_outputs=worker_outputs,
            feed_stale=feed_stale,
            market_stale=market_stale,
            last_decision_action=decision.action,
            decision_outputs=decision_outputs,
            config_params=config_params,
            decision_time_ms=self._decision_logic.performance_logger.get_stats().decision_avg_time_ms if self._decision_logic.performance_logger else 0.0,
            decision_max_time_ms=self._decision_logic.performance_logger.get_stats().decision_max_time_ms if self._decision_logic.performance_logger else 0.0,
            decision_rolling_avg_ms=self._decision_logic.performance_logger.get_rolling_avg_ms() if self._decision_logic.performance_logger else 0.0,
            account_currency=self._executor.account_currency,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            trading_model=self._trading_model.value,
            safety_blocked=safety.blocked,
            safety_reason=safety.reason,
            safety_current_value=safety.current_value,
            safety_drawdown_pct=safety.drawdown_pct,
            recent_rejections=list(recent_rejections),
            total_rejections=total_rejections,
            event_history=self._decision_logic.get_event_history(),
            total_events_emitted=self._decision_logic.get_total_events_emitted(),
            last_tick_time=self._executor.get_current_time(),
            **self._drift_display_counters(),
            **self._reconcile_display_counters(),
            api_perf=self._api_perf_snapshot(),
        )

    def _drift_display_counters(self) -> Dict[str, object]:
        """
        Build drift_* kwargs for AutoTraderDisplayStats. Returns empty dict
        (uses dataclass defaults) when no DriftAuditor is wired. #327.
        """
        if self._drift_auditor is None:
            return {}
        counters = self._drift_auditor.get_display_counters()
        return {
            'drift_enabled': bool(counters.get('drift_enabled', False)),
            'drift_audited': int(counters.get('drift_audited', 0)),
            'drift_fee_events': int(counters.get('drift_fee_events', 0)),
            'drift_volume_events': int(counters.get('drift_volume_events', 0)),
            'drift_price_events': int(counters.get('drift_price_events', 0)),
            'drift_slippage_events': int(counters.get('drift_slippage_events', 0)),
            'drift_max_fee_pct': float(counters.get('drift_max_fee_pct', 0.0)),
            'drift_max_slippage_pct': float(counters.get('drift_max_slippage_pct', 0.0)),
        }

    def _reconcile_display_counters(self) -> Dict[str, object]:
        """
        Build reconcile_* kwargs for AutoTraderDisplayStats. Returns empty dict
        (uses dataclass defaults) when no Reconciler is wired. #151.
        """
        if self._reconciler is None:
            return {}
        counters = self._reconciler.get_display_counters()
        return {
            'reconcile_enabled': bool(counters.get('reconcile_enabled', False)),
            'reconcile_divergences': int(counters.get('reconcile_divergences', 0)),
            'reconcile_clean': bool(counters.get('reconcile_clean', True)),
            'reconcile_count': int(counters.get('reconcile_count', 0)),
            'reconcile_state_age_s': float(counters.get('reconcile_state_age_s', 0.0)),
            'reconcile_next_in_s': float(counters.get('reconcile_next_in_s', 0.0)),
        }

    def _api_perf_snapshot(self) -> Optional[ApiPerfSnapshot]:
        """Return the API monitor snapshot for the display, or None if not wired (#351)."""
        if self._api_monitor is None:
            return None
        return self._api_monitor.get_snapshot()
