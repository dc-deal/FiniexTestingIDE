"""
FiniexTestingIDE - Live Telemetry Fixtures
A fully populated AutoTrader dashboard frame, and the display that renders it (#586).

Built from the real framework types, never a MagicMock: a mock invents every attribute it is
asked for, which is exactly how a panel came to read a field its row never had.
"""

import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
    QuoteFeedState,
    QuoteFeedStats,
    RejectionEntry,
    SignalTransportEvent,
    SignalTransportStats,
    TickSourceSnapshot,
    TradeHistoryEntry,
)
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.autotrader_defaults_config_types import SafetyConfig
from python.framework.types.decision_logic_types import (
    AwarenessLevel,
    DecisionAwareness,
    DecisionLogicAction,
    StrategyEvent,
)
from python.framework.types.live_types.api_perf_types import ApiEndpointStats, ApiPerfSnapshot
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.signal_data_types import SignalHealthStatus, SignalTransportState
from python.framework.types.trading_env_types.active_orders_snapshot_types import (
    ActiveOrderSnapshot,
)
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderDirection,
    OrderSide,
    OrderType,
)
from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay

# The frame's moment — every age on the dashboard is measured against it
_FRAME_TIME = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


class QuietTickSource(AbstractTickSource):
    """A tick source that emits nothing; a test hands the dashboard its readings directly."""

    def start(self) -> None:
        """Nothing to start."""

    def stop(self) -> None:
        """Nothing to stop."""

    def is_exhausted(self) -> bool:
        """
        Always exhausted — it holds no ticks.

        Returns:
            True
        """
        return True

    def get_symbol(self) -> str:
        """
        The symbol the dashboard frame is for.

        Returns:
            'BTCUSD'
        """
        return 'BTCUSD'


def execution(volume: float, price: float, minutes_before: int) -> BrokerTrade:
    """
    One venue execution, for the multi-fill rows.

    Args:
        volume: Executed lots
        price: Execution price
        minutes_before: How long before the frame it happened

    Returns:
        The execution
    """
    return BrokerTrade(
        trade_id=f'T-{volume}-{price}', parent_broker_ref='OREF-1', order_id='pos_btcusd_3',
        volume=volume, price=price, fee=0.01, fee_currency='USD',
        timestamp=_FRAME_TIME - timedelta(minutes=minutes_before), side=OrderSide.BUY,
        is_maker=False)


def trade_row(close_reason: CloseReason = CloseReason.MANUAL,
              close_type: CloseType = CloseType.FULL,
              lots: float = 0.05,
              entry_lots: float = 0.10) -> TradeHistoryEntry:
    """
    One closed trade as the dashboard's TRADE HISTORY shows it.

    Args:
        close_reason: How the position was closed
        close_type: Whether the close took the whole position
        lots: The size this close took
        entry_lots: The position's size when it opened, or None

    Returns:
        The row
    """
    return TradeHistoryEntry(
        trade_id='pos_btcusd_3', symbol='BTCUSD', direction=OrderDirection.LONG, lots=lots,
        entry_price=50000.0, exit_price=50500.0, net_pnl=24.5, close_reason=close_reason,
        close_type=close_type, entry_lots=entry_lots,
        entry_trades=[execution(0.06, 50000.0, 30), execution(0.04, 50010.0, 29)],
        exit_trades=[execution(0.03, 50490.0, 4), execution(0.02, 50510.0, 3)],
        exit_side=OrderSide.SELL, exit_time=_FRAME_TIME - timedelta(minutes=3))


@pytest.fixture
def make_trade_row():
    """
    The TRADE HISTORY row builder, for tests that vary how a trade closed.

    Returns:
        trade_row
    """
    return trade_row


@pytest.fixture
def frame_time() -> datetime:
    """
    The wall clock the dashboard renders at.

    Returns:
        A fixed, timezone-aware moment
    """
    return _FRAME_TIME


@pytest.fixture
def dashboard_frame() -> AutoTraderDisplayStats:
    """
    A dashboard frame with every field set to something a panel renders.

    Free text carries brackets on purpose: a venue's or a strategy's words are text, never
    markup, and a closing tag without an opener only fails when it is PRINTED.

    Returns:
        The frame
    """
    return AutoTraderDisplayStats(
        core=LiveCoreSnapshot(
            symbol='BTCUSD', ticks_processed=48213, balance=9980.0, initial_balance=10000.0,
            total_trades=12, winning_trades=7, losing_trades=5,
            last_awareness=DecisionAwareness(
                message='trend filter [ATR] armed, waiting [/x]', level=AwarenessLevel.NOTICE)),
        session_start=_FRAME_TIME - timedelta(hours=54, minutes=12),
        dry_run=False,
        broker_type='kraken_spot',
        config_hash='a1b2c3d4',
        deployment_id='deploy_20261012_074850',
        equity=10012.5,
        spot_balances={'USD': 9980.0, 'BTC': 0.05},
        open_positions=[PositionSnapshot(
            position_id='pos_btcusd_9', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.05, entry_price=50100.0, unrealized_pnl=-3.25,
            entry_trades=[execution(0.03, 50100.0, 9), execution(0.02, 50110.0, 8)])],
        active_orders=[ActiveOrderSnapshot(
            order_id='pos_btcusd_11', order_type=OrderType.LIMIT, symbol='BTCUSD',
            direction=OrderDirection.LONG, lots=0.1, entry_price=49800.0,
            cumulative_filled_lots=0.04, requested_lots=0.1,
            submitted_at=_FRAME_TIME - timedelta(minutes=12))],
        pipeline_count=1,
        recent_trades=[trade_row()],
        clipping_ratio=0.07,
        avg_processing_ms=0.42,
        max_processing_ms=3.1,
        queue_depth=2,
        total_ticks_clipped=3375,
        processing_times_ms=[0.3, 0.4, 0.5, 1.2, 3.1],
        ticks_per_min=14.8,
        last_price=50433.0,
        account_currency='EUR',
        base_currency='BTC',
        quote_currency='USD',
        trading_model='spot',
        safety_blocked=True,
        safety_reason='drawdown 5.2% [limit 5.0%]',
        safety_current_value=9980.0,
        safety_drawdown_pct=5.2,
        recent_rejections=[RejectionEntry(
            seq=3, reason='insufficient_funds', message='EOrder:[/x] Insufficient funds',
            side='buy', tick_time=_FRAME_TIME - timedelta(minutes=1))],
        total_rejections=4,
        worker_times_ms={'rsi_fast': 0.21},
        worker_max_times_ms={'rsi_fast': 0.9},
        worker_rolling_avg_times_ms={'rsi_fast': 0.25},
        worker_outputs={'rsi_fast': {'rsi': 41.2, 'regime': 'range [low]'}},
        feed_stale=True,
        signal_transport=SignalTransportStats(
            configured=True, state=SignalTransportState.LIVE, source='producer', last_seq=812,
            stream_epoch=4,
            last_envelope_at=_FRAME_TIME - timedelta(seconds=40), envelopes_received=812,
            transport_errors=2, contract_errors=1,
            tape=[SignalTransportEvent(message='epoch changed [/x] — reconnecting',
                                       at=_FRAME_TIME - timedelta(minutes=2),
                                       level=AwarenessLevel.NOTICE)],
            total_events=3,
            health=SignalHealthStatus(
                journal_id='j-7f3a', journal_name='main [prod]', probed_at=_FRAME_TIME,
                journal_changed=True, budget_suspended=True,
                budget_reason='daily budget [/x] spent')),
        market_stale=True,
        last_decision_action=DecisionLogicAction.BUY,
        decision_outputs={'confidence': 0.73, 'why': 'cross [up]'},
        decision_time_ms=0.08,
        decision_max_time_ms=0.4,
        decision_rolling_avg_ms=0.09,
        config_params={'rsi_buy': 30.0, 'mode': 'strict [x]'},
        event_history=[StrategyEvent(message='entry skipped [/x] — spread too wide',
                                     level=AwarenessLevel.ALERT,
                                     tick_time=_FRAME_TIME - timedelta(minutes=5))],
        total_events_emitted=9,
        last_tick_time=_FRAME_TIME - timedelta(seconds=2),
        is_pulse=True,
        seconds_since_last_tick=2.4,
        drift_enabled=True,
        drift_audited=13,
        drift_fee_events=1,
        drift_volume_events=1,
        drift_price_events=1,
        drift_slippage_events=1,
        drift_max_fee_pct=0.62,
        drift_max_slippage_pct=0.03,
        reconcile_enabled=True,
        reconcile_divergences=1,
        reconcile_clean=False,
        reconcile_count=10,
        reconcile_state_age_s=95.0,
        reconcile_next_in_s=30.0,
        reconcile_unaccounted=1,
        api_perf=ApiPerfSnapshot(
            endpoints=[ApiEndpointStats(
                endpoint='/0/private/OpenOrders', count=235, last_fired_at=_FRAME_TIME,
                max_ms=3200.0, total_ms=23500.0, error_count=1)],
            slow_count=1, total_errors=1),
    )


@pytest.fixture
def connection(frame_time: datetime) -> TickSourceSnapshot:
    """
    The tick source's readings for the frame.

    Args:
        frame_time: The frame's moment

    Returns:
        The readings
    """
    return TickSourceSnapshot(
        reconnect_count=1, ticks_emitted=48213,
        last_message_time=frame_time - timedelta(seconds=1),
        last_tick_time=frame_time - timedelta(seconds=2),
        message_age_s=1.0, connection_dead_s=10.0,
        quote=QuoteFeedStats(state=QuoteFeedState.LIVE, bid=50430.0, ask=50436.0, quote_age_ms=420,
                             quotes_received=900))


@pytest.fixture
def render_reports() -> List[str]:
    """
    Where the dashboard reports what it could not render.

    Returns:
        An empty list the display appends to
    """
    return []


@pytest.fixture
def dashboard(render_reports: List[str]) -> AutoTraderLiveDisplay:
    """
    The dashboard as a session builds it — no thread is started, nothing reaches a terminal.

    Args:
        render_reports: Receives the display's failure reports

    Returns:
        The display
    """
    config = AutoTraderConfig(
        profile_name='dashboard_render_test', symbol='BTCUSD', broker_type='kraken_spot',
        adapter_type='live', strategy_config={'decision_logic_type': 'CORE/aggressive_trend'},
        safety=SafetyConfig(enabled=True, min_equity=9990.0, max_drawdown_pct=5.0),
        config_path=Path('configs/autotrader_profiles/production/dashboard_render_test.json'))
    labels = DisplayLabelCache(
        config_param_specs=(('rsi_buy', 'rsi_buy'), ('mode', 'mode')),
        worker_output_labels={'rsi_fast': {'rsi': 'rsi', 'regime': 'regime'}},
        decision_output_labels={'confidence': 'conf', 'why': 'why'},
        data_sentiment_type='news_sentiment')
    return AutoTraderLiveDisplay(
        display_queue=queue.Queue(), tick_source=QuietTickSource(), config=config,
        dry_run=False, deployment_id='deploy_20261012_074850', display_label_cache=labels,
        on_render_error=render_reports.append)
