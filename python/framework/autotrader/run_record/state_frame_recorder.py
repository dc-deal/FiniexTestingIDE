"""
FiniexTestingIDE - State Frame Recorder (#579 part A)

Writes what an AutoTrader session holds and decides at one moment into its `io/state_frames.jsonl`,
whether or not a display runs: on a cadence of the canonical clock, when a bar of a timeframe the
workers read closes, when the decision sends an order, and once at the session's end — at most one
frame per pass of the loop. A backtest over the same period can be run again at any time; the live
session cannot, so its side of the comparison (#512) has to be recorded while it happens.

The cadence is a timeframe, and its slots are that timeframe's periods on the canonical clock — the
grid the bars open their periods on — so a live session and a backtest of the same period fill the
same slots. A frame written for an event fills a slot that is due in the same pass. A clock that
steps back (a replayed session's heartbeat reads the wall clock) re-anchors the grid instead of
writing.

The process's memory (#611) is on every frame of a live session, whose frames come once per cadence
interval of the wall clock and at its events. A replay compresses hours into seconds, and reading
the private pages of a large process costs milliseconds (measured 2026-10-21: 0.84 ms for a 640 MB
process; 93 % of a replay's recording cost before the throttle), so a replay reads it at most once
per cadence interval of the monotonic clock, and at its first frame and its last — a frame without
a reading carries none. A live session is not paced that way: two of its cadence frames lie a
little more or a little less than one interval apart, and the pacing dropped the reading from a
third of them (measured 2026-10-21 in a dry run on the live feed: 21 of 32 frames).

A frame that cannot be built is said once and skipped, and the recording goes on; a stream that can
no longer be written stops, by the appender's rule. Neither ever ends the session.
"""

import math
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.builders.pending_orders_report_builder import active_order_rows
from python.framework.reporting.builders.portfolio_report_builder import open_position_rows
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.api.state_frame_types import (
    ProcessMemoryRow,
    StateFrame,
    StateFrameCause,
)
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import SafetyState
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_logic_types import Decision
from python.framework.types.process_table_types import OwnProcessMemory
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.utils.bar_period import open_time_of, period_start
from python.framework.utils.process_memory_reader import read_own_memory
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.utils.trading_math.price_trigger import mid_price
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _utc_now() -> datetime:
    """
    The wall clock as an aware UTC instant — when a frame was written, never what it describes.

    Returns:
        The current instant in UTC
    """
    return datetime.now(timezone.utc)


def _plain(value: Any) -> Any:
    """
    One output as a JSON value. A worker declares scalars but may output a structure (a probe's
    warmup status is a dict), so a mapping or a list is walked; a numpy value becomes its Python
    value, a value not defined yet (NaN, an indicator still warming up) None — JSON has no NaN —
    an enum its value, an instant its ISO text, and anything else its text.

    Args:
        value: A worker's or the decision's output

    Returns:
        The value the line holds
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, 'tolist') and not isinstance(value, (str, bytes)):
        # A numpy scalar answers a Python scalar, an array a (nested) list
        return _plain(value.tolist())
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class StateFrameRecorder:
    """
    Decides when a frame is due, builds it from the session's own objects, and appends it.

    Args:
        appender: The stream the frames go to
        cadence_timeframe: The cadence — one frame per period of this timeframe
        config: The resolved profile — its symbol
        executor: The session's executor — the clock, the account, the positions, the orders
        worker_orchestrator: The workers' latest results and the signal workers
        bar_controller: The bar history a closed bar's opening is read from
        trading_model: SPOT or MARGIN — whether the frame lists every balance
        reconciler: The reconciler, or None when none is wired — then nothing was checked
        replayed: Whether the ticks are a replay; its heartbeats read the wall clock, so the cadence
            follows the tick passes alone, and the memory is read at most once per interval
        logger: The session's channel, where a frame that cannot be built is said
        wall_clock: Reads the instant a frame is written
        memory_reader: Reads what the session's own process holds
        monotonic_clock: Paces a replay's memory reading
    """

    def __init__(
        self,
        appender: RunStreamAppender,
        cadence_timeframe: str,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        worker_orchestrator: WorkerOrchestrator,
        bar_controller: BarRenderingController,
        trading_model: TradingModel,
        reconciler: Optional[Reconciler],
        replayed: bool,
        logger: AbstractLogger,
        wall_clock: Callable[[], datetime] = _utc_now,
        memory_reader: Callable[[], OwnProcessMemory] = read_own_memory,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ):
        self._appender = appender
        self._cadence_timeframe = cadence_timeframe
        # One period of the cadence, in seconds — what paces a replay's memory reading
        self._cadence_s = TimeframeConfig.get_minutes(cadence_timeframe) * 60.0
        self._symbol = config.symbol
        self._executor = executor
        self._worker_orchestrator = worker_orchestrator
        self._bar_controller = bar_controller
        self._spot = trading_model == TradingModel.SPOT
        self._reconciler = reconciler
        self._replayed = replayed
        self._logger = logger
        self._wall_clock = wall_clock
        self._memory_reader = memory_reader
        self._monotonic_clock = monotonic_clock
        # When the process's memory was last read, on the monotonic clock
        self._memory_read_at: Optional[float] = None
        # The last slot a frame filled, and the canonical time of the last frame — the anchor a
        # clock that steps back is measured against
        self._last_slot: Optional[datetime] = None
        self._last_frame_time: Optional[datetime] = None
        self._frames_written = 0
        self._build_failures = 0

    def on_pass(
        self,
        ticks_processed: int,
        decision: Optional[Decision],
        safety: SafetyState,
        on_tick: bool,
        closed_timeframes: FrozenSet[str] = frozenset(),
        order: Optional[OrderResult] = None,
    ) -> None:
        """
        Write a frame when this pass is due one — at the end of every pass, tick or heartbeat.

        Args:
            ticks_processed: The session's tick counter
            decision: The decision of this pass, or the last one on a heartbeat; None before any
            safety: The circuit breaker's state after this pass
            on_tick: True on a tick pass, False on a heartbeat
            closed_timeframes: The timeframes whose bar closed in this pass
            order: What the decision sent in this pass, if it sent anything
        """
        frame_time = self._executor.get_current_time_if_set()
        if frame_time is None:
            return
        if self._last_frame_time is not None and frame_time < self._last_frame_time:
            # A replayed session's heartbeat read the wall clock, the next tick steps back to
            # the replay: begin the grid again from here rather than fill a slot twice
            self._last_slot = self._slot_of(frame_time)
            self._last_frame_time = frame_time
            return
        slot = self._slot_of(frame_time)
        slot_due = (on_tick or not self._replayed) and (
            self._last_slot is None or slot > self._last_slot)
        if order is not None:
            cause = StateFrameCause.ORDER
        elif closed_timeframes:
            cause = StateFrameCause.BAR_CLOSE
        elif slot_due:
            cause = StateFrameCause.CADENCE
        else:
            return
        self._write(
            cause, frame_time, slot if slot_due else None, ticks_processed, decision, safety,
            closed_timeframes, order)
        if slot_due:
            # Taken by this attempt whether or not it reached the stream: a frame that cannot be
            # built is tried again at the next slot, never on every pass of this one
            self._last_slot = slot
        self._last_frame_time = frame_time

    def write_shutdown(
        self,
        ticks_processed: int,
        decision: Optional[Decision],
        safety: SafetyState,
    ) -> None:
        """
        Write the session's last frame — at the state its report describes — and close the stream.

        Called after the session's orders were finished and the venue asked once more, before the
        results are collected: the same moment and the same price the report's valuation reads.

        Args:
            ticks_processed: The session's tick counter
            decision: The last decision, or None when no pass ran one
            safety: The circuit breaker's last state
        """
        frame_time = self._executor.get_current_time_if_set()
        if frame_time is not None:
            # The last reading is always taken: it closes the series a leak is a slope across
            self._memory_read_at = None
            self._write(StateFrameCause.SHUTDOWN, frame_time, None, ticks_processed, decision,
                        safety, frozenset(), None)
        if self._build_failures:
            self._logger.warning(
                f'📼 {self._build_failures} state frame(s) could not be built this session and are '
                f'missing from {self._appender.get_path().name}; the first failure was reported '
                f'when it happened.')
        self._appender.close()

    def close(self) -> None:
        """Close the stream without a last frame — a session that never reached its loop."""
        self._appender.close()

    def get_frames_written(self) -> int:
        """
        How many frames reached the stream.

        Returns:
            The count
        """
        return self._frames_written

    # === Internals ===

    def _slot_of(self, frame_time: datetime) -> datetime:
        """
        The cadence slot an instant falls in — the opening of its period, on the bars' grid.

        Args:
            frame_time: A canonical-clock instant

        Returns:
            The slot's opening instant
        """
        return period_start(frame_time, self._cadence_timeframe)

    def _write(
        self,
        cause: StateFrameCause,
        frame_time: datetime,
        slot: Optional[datetime],
        ticks_processed: int,
        decision: Optional[Decision],
        safety: SafetyState,
        closed_timeframes: FrozenSet[str],
        order: Optional[OrderResult],
    ) -> bool:
        """
        Build one frame and append it; a frame that cannot be built is said once and skipped.

        Args:
            cause: Why it is written
            frame_time: The canonical time of the pass
            slot: The cadence slot it fills, or None
            ticks_processed: The session's tick counter
            decision: The decision it records, or None
            safety: The circuit breaker's state
            closed_timeframes: The timeframes whose bar closed in this pass
            order: The order the decision sent, if any

        Returns:
            True when the frame reached the stream
        """
        try:
            frame = self._build(cause, frame_time, slot, ticks_processed, decision, safety,
                                closed_timeframes, order)
        except Exception as e:  # noqa: BLE001 — a recording defect never ends the session
            self._build_failures += 1
            if self._build_failures == 1:
                self._logger.error(
                    f'❌ A state frame ({cause.value}) could not be built: '
                    f'{type(e).__name__}: {e}. The frame is skipped and the recording goes on; '
                    f'the count follows at the session\'s end.')
            return False
        if not self._appender.write(frame.model_dump(mode='json')):
            return False
        self._frames_written += 1
        return True

    def _build(
        self,
        cause: StateFrameCause,
        frame_time: datetime,
        slot: Optional[datetime],
        ticks_processed: int,
        decision: Optional[Decision],
        safety: SafetyState,
        closed_timeframes: FrozenSet[str],
        order: Optional[OrderResult],
    ) -> StateFrame:
        """
        The frame of this moment, from the session's own objects.

        Args:
            cause: Why it is written
            frame_time: The canonical time of the pass
            slot: The cadence slot it fills, or None
            ticks_processed: The session's tick counter
            decision: The decision it records, or None
            safety: The circuit breaker's state
            closed_timeframes: The timeframes whose bar closed in this pass
            order: The order the decision sent, if any

        Returns:
            The frame
        """
        portfolio = self._executor.portfolio
        # Marks the open positions, as the report's own collection does before it reads them
        portfolio.ensure_positions_updated()
        last_price: Optional[float] = None
        try:
            last_price = mid_price(*self._executor.get_current_price(self._symbol))
        except ValueError:
            # No tick yet: the account is reported unvalued, never at an invented price
            pass
        positions = self._executor.get_open_positions()
        orders = self._executor.get_active_orders_snapshot()
        return StateFrame(
            frame_time=frame_time,
            written_at=self._wall_clock(),
            written_for=cause,
            cadence_slot=slot,
            ticks_processed=ticks_processed,
            symbol=self._symbol,
            account_currency=self._executor.account_currency,
            last_price=last_price,
            balance=portfolio.balance,
            balances=dict(portfolio.get_balances()) if self._spot else None,
            equity=portfolio.get_account_value(),
            unrealized_pnl=sum(position.unrealized_pnl for position in positions),
            open_positions=open_position_rows(positions, last_price or 0.0),
            active_orders=active_order_rows(
                list(orders.active_limit_orders) + list(orders.active_stop_orders)),
            safety_blocked=safety.blocked,
            safety_drawdown_pct=safety.drawdown_pct,
            market_stale=self._executor.get_market_data_status().is_stale,
            feed_stale=self._feed_stale(),
            reconcile_clean=self._reconcile_clean(),
            worker_outputs=self._worker_outputs(),
            decision_action=decision.action if decision is not None else None,
            decision_outputs=({key: _plain(value) for key, value in decision.outputs.items()}
                              if decision is not None else {}),
            closed_bars=self._closed_bars(closed_timeframes),
            order_id=order.order_id if order is not None else None,
            process_memory=self._process_memory(),
        )

    def _process_memory(self) -> Optional[ProcessMemoryRow]:
        """
        The process's memory — on every frame of a live session; in a replay when a cadence
        interval of the monotonic clock has passed since the last reading, and at the first frame
        and the last.

        Returns:
            The reading, or None when this frame takes none
        """
        now = self._monotonic_clock()
        if (self._replayed and self._memory_read_at is not None
                and now - self._memory_read_at < self._cadence_s):
            return None
        self._memory_read_at = now
        memory = self._memory_reader()
        return ProcessMemoryRow(rss_bytes=memory.rss_bytes, private_bytes=memory.private_bytes)

    def _worker_outputs(self) -> Dict[str, Dict[str, Any]]:
        """
        Every output of every worker's latest result — what the decision could read.

        Returns:
            Outputs by worker instance name; a worker without a result yet is left out
        """
        outputs: Dict[str, Dict[str, Any]] = {}
        for name in self._worker_orchestrator.workers:
            result = self._worker_orchestrator.get_worker_result(name)
            if result is not None:
                outputs[name] = {key: _plain(value) for key, value in result.outputs.items()}
        return outputs

    def _feed_stale(self) -> Optional[bool]:
        """
        Whether a signal input is stale — None when the session reads no signal at all.

        Returns:
            The staleness, or None for not checked
        """
        if not self._worker_orchestrator.get_signal_workers():
            return None
        return bool(self._worker_orchestrator.get_stale_signal_workers())

    def _reconcile_clean(self) -> Optional[bool]:
        """
        Whether the latest reconciliation found the venue in agreement with the local book.

        Returns:
            The verdict, or None for not checked: no reconciler, no cycle yet, or a latest cycle
            that could not reach the venue
        """
        if self._reconciler is None:
            return None
        return self._reconciler.get_last_verdict()

    def _closed_bars(self, closed_timeframes: FrozenSet[str]) -> Dict[str, datetime]:
        """
        The opening of the bar each timeframe just closed — the key a backtest's frame shares.

        Args:
            closed_timeframes: The timeframes whose bar closed in this pass

        Returns:
            The bar's opening instant by timeframe
        """
        closed: Dict[str, datetime] = {}
        for timeframe in sorted(closed_timeframes):
            history = self._bar_controller.get_bar_history(self._symbol, timeframe)
            if history:
                closed[timeframe] = open_time_of(history[-1])
        return closed


def open_state_frame_stream(
    config: AutoTraderConfig,
    run_writer: RunEntryWriter,
    run_id: str,
    executor: AbstractTradeExecutor,
    worker_orchestrator: WorkerOrchestrator,
    bar_controller: BarRenderingController,
    trading_model: TradingModel,
    reconciler: Optional[Reconciler],
    logger: AbstractLogger,
) -> Optional[StateFrameRecorder]:
    """
    Open the session's state-frame stream, when the profile records one.

    The run index learns of the stream in the same step, so the run list names it while the session
    runs — the check-in reads it from there (#611), long before any report exists.

    Args:
        config: The resolved profile — its `state_frames` block, its unit and its tick source
        run_writer: The session's run writer, whose stream sealer the stream joins (#476)
        run_id: The run the stream belongs to
        executor: The session's executor
        worker_orchestrator: The session's workers
        bar_controller: The session's bars
        trading_model: SPOT or MARGIN
        reconciler: The reconciler, or None when none is wired
        logger: The session's channel

    Returns:
        The recorder, or None when the recording is switched off
    """
    settings = config.state_frames
    if not settings.enabled:
        logger.info('📼 State frames: not recorded — switched off in the profile')
        return None
    appender = run_writer.open_stream(
        RunEntryMemberId.STATE_FRAMES, run_id, logger,
        stops='The session\'s state is no longer recorded',
        header_fields={'unit_name': config.get_unit_name(),
                       'cadence_timeframe': settings.cadence_timeframe,
                       'cadence_s': TimeframeConfig.get_minutes(settings.cadence_timeframe) * 60.0})
    logger.info(
        f'📼 State frames: one per {settings.cadence_timeframe} period of the canonical clock, at '
        f'a closed bar and at an order → {appender.get_path()}')
    RunIndex(AppConfigManager().get_file_logging_config_object().run_index) \
        .record_streams(run_id, run_writer.get_run_dir())
    return StateFrameRecorder(
        appender, settings.cadence_timeframe, config, executor, worker_orchestrator,
        bar_controller, trading_model, reconciler,
        replayed=config.tick_source.type == 'mock', logger=logger)
