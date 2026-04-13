"""
FiniexTestingIDE - AutoTrader Live Console Display
Real-time rich.live dashboard for running AutoTrader sessions (#228).

Architecture:
- Daemon thread polls queue.Queue every 300ms (configurable)
- Tick source stats (reconnects, last_message_time) polled directly (GIL-safe)
- Responsive layout: 3-col (≥160), 2-col (≥120), 1-col (<120)
- Panel priority: session/portfolio/positions/orders always shown,
  algo state/connection/clipping/worker perf added with terminal width
"""

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
    TradeHistoryEntry,
)
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.decision_logic_types import AwarenessLevel, DecisionLogicAction
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.types.trading_env_types.pending_order_stats_types import ActiveOrderSnapshot


class AutoTraderLiveDisplay:
    """
    Real-time console dashboard for AutoTrader live sessions.

    Queue-based design: tick loop pushes AutoTraderDisplayStats snapshots,
    display thread drains queue and renders via rich.live.

    Tick source connection stats are polled directly (GIL-safe primitives).

    Args:
        display_queue: Thread-safe queue receiving display stats
        tick_source: Tick source instance (for connection stats polling)
        config: AutoTrader configuration
    """

    # Layout breakpoints
    _WIDTH_THREE_COL = 160
    _WIDTH_TWO_COL = 120
    _WIDTH_TRADE_HISTORY = 100

    # Trade history display limit
    _MAX_RECENT_TRADES = 8

    def __init__(
        self,
        display_queue: queue.Queue,
        tick_source: AbstractTickSource,
        config: AutoTraderConfig,
        dry_run: bool = True,
        display_label_cache: Optional[DisplayLabelCache] = None,
    ):
        self._display_queue = display_queue
        self._tick_source = tick_source
        self._config = config
        self._dry_run = dry_run
        self._display_label_cache = display_label_cache or DisplayLabelCache()
        self._update_interval = config.display.update_interval_ms / 1000.0

        # Stats cache (latest snapshot from queue)
        self._stats: Optional[AutoTraderDisplayStats] = None

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Rich console
        self._console = Console()
        self._live: Optional[Live] = None

    def start(self) -> None:
        """Start the display thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._update_loop,
            name='AutoTrader-Display',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the display thread. Final drain + render happens inside the
        display thread to avoid cross-thread ``live.update()`` calls."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _update_loop(self) -> None:
        """Main update loop running in display thread."""
        with Live(
            self._render(),
            console=self._console,
            refresh_per_second=2,
        ) as live:
            self._live = live

            while self._running:
                try:
                    # Drain queue — use latest snapshot only
                    updates_processed = 0
                    for _ in range(100):
                        try:
                            stats = self._display_queue.get_nowait()
                            with self._lock:
                                self._stats = stats
                            updates_processed += 1
                        except queue.Empty:
                            break

                    live.update(self._render())
                    time.sleep(self._update_interval)

                except Exception:
                    self._running = False
                    raise

            # Final drain after _running = False — ensures the very last
            # snapshot pushed by the tick loop is rendered, regardless of
            # refresh timing. This runs inside the display thread, so
            # live.update() is safe (same thread that owns the Live context).
            while True:
                try:
                    stats = self._display_queue.get_nowait()
                    with self._lock:
                        self._stats = stats
                except queue.Empty:
                    break
            live.update(self._render())

    # =========================================================================
    # RENDER — responsive layout
    # =========================================================================

    def _render(self) -> Panel:
        """
        Build the full display panel with responsive layout.

        Returns:
            Rich Panel containing the dashboard
        """
        width = self._console.size.width

        with self._lock:
            stats = self._stats

        if stats is None:
            return Panel(
                '[dim]Waiting for first tick...[/dim]',
                title=self._build_header_title(),
                border_style='cyan',
                box=box.ROUNDED,
            )

        if width >= self._WIDTH_THREE_COL:
            body = self._render_three_col(stats, width)
        elif width >= self._WIDTH_TWO_COL:
            body = self._render_two_col(stats, width)
        else:
            body = self._render_single_col(stats)

        return Panel(
            body,
            title=self._build_header_title(stats),
            border_style='cyan',
            box=box.ROUNDED,
        )

    def _build_header_title(self, stats: Optional[AutoTraderDisplayStats] = None) -> str:
        """Build the header title with symbol and mode."""
        symbol = stats.symbol if stats else self._config.symbol
        broker = stats.broker_type if stats else self._config.broker_type
        dry_run = stats.dry_run if stats else self._dry_run
        mode_label = '[yellow]DRY RUN[/yellow]' if dry_run else '[green bold]LIVE TRADING[/green bold]'
        return f'[bold cyan]FiniexAutoTrader[/bold cyan] — {symbol} ({broker}) — {mode_label}'

    # =========================================================================
    # LAYOUT VARIANTS
    # =========================================================================

    def _render_three_col(self, stats: AutoTraderDisplayStats, width: int) -> Layout:
        """Three-column layout for wide terminals (≥160 cols)."""
        layout = Layout()
        layout.split_row(
            Layout(name='left', ratio=1),
            Layout(name='center', ratio=1),
            Layout(name='right', ratio=1),
        )

        # Left: Session + Connection + Worker Performance
        layout['left'].split_column(
            Layout(self._build_session_panel(stats), name='session'),
            Layout(self._build_connection_panel(stats), name='connection'),
            Layout(self._build_worker_perf_panel(stats), name='worker_perf'),
        )

        # Center: Portfolio + Tick Processing + Clipping
        layout['center'].split_column(
            Layout(self._build_portfolio_panel(stats), name='portfolio'),
            Layout(self._build_tick_processing_panel(stats), name='tick'),
        )

        # Right: Positions + Orders + Trade History + Algo State
        right_panels = [
            Layout(self._build_positions_panel(stats), name='positions'),
            Layout(self._build_orders_panel(stats), name='orders'),
        ]
        if stats.recent_trades:
            right_panels.append(
                Layout(self._build_trade_history_panel(stats), name='trades'))
        right_panels.append(
            Layout(self._build_algo_state_panel(stats), name='algo'))
        layout['right'].split_column(*right_panels)

        return layout

    def _render_two_col(self, stats: AutoTraderDisplayStats, width: int) -> Layout:
        """Two-column layout for medium terminals (≥120 cols)."""
        layout = Layout()
        layout.split_row(
            Layout(name='left', ratio=1),
            Layout(name='right', ratio=1),
        )

        # Left: Session + Portfolio + Algo State
        layout['left'].split_column(
            Layout(self._build_session_panel(stats), name='session'),
            Layout(self._build_portfolio_panel(stats), name='portfolio'),
            Layout(self._build_algo_state_panel(stats), name='algo'),
            Layout(self._build_connection_panel(stats), name='connection'),
        )

        # Right: Positions + Orders + Trade History
        right_panels = [
            Layout(self._build_positions_panel(stats), name='positions'),
            Layout(self._build_orders_panel(stats), name='orders'),
        ]
        if width >= self._WIDTH_TRADE_HISTORY and stats.recent_trades:
            right_panels.append(
                Layout(self._build_trade_history_panel(stats), name='trades'))
        layout['right'].split_column(*right_panels)

        return layout

    def _render_single_col(self, stats: AutoTraderDisplayStats) -> Layout:
        """Single-column layout for narrow terminals (<120 cols)."""
        layout = Layout()
        layout.split_column(
            Layout(self._build_session_panel(stats), name='session'),
            Layout(self._build_portfolio_panel(stats), name='portfolio'),
            Layout(self._build_positions_panel(stats), name='positions'),
            Layout(self._build_orders_panel(stats), name='orders'),
        )
        return layout

    # =========================================================================
    # PANEL BUILDERS
    # =========================================================================

    def _build_session_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Session overview: uptime, tick rate, trades, mode."""
        now = datetime.now(timezone.utc)
        uptime = now - stats.session_start
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f'{hours}h {minutes:02d}m {seconds:02d}s'

        mode = '[yellow]DRY RUN[/yellow]' if stats.dry_run else '[green]LIVE[/green]'
        win_rate = (stats.winning_trades / stats.total_trades *
                    100) if stats.total_trades > 0 else 0.0

        if stats.safety_blocked:
            safety_str = f'[red bold]⛔ BLOCKED[/red bold]  [dim]{stats.safety_reason}[/dim]'
        elif self._config.safety.enabled:
            safety_str = f'[green]● ACTIVE[/green]  [dim]({stats.trading_model})[/dim]'
        else:
            safety_str = '[dim]off[/dim]'

        lines = [
            f'Uptime:  {uptime_str}',
            f'Status:  [green]● RUNNING[/green]',
            f'Rate:    {stats.ticks_per_min:.1f}/min  ({stats.ticks_processed:,} total)',
            f'Trades:  {stats.total_trades}  (Win: {win_rate:.1f}%)',
            f'Mode:    {mode}',
            f'Safety:  {safety_str}',
        ]

        # Safety detail line — headroom at a glance
        if self._config.safety.enabled:
            safety = self._config.safety
            # Min threshold: mode-specific field name and value
            if stats.trading_model == 'spot':
                min_label = 'min_equity'
                min_threshold = safety.min_equity
            else:
                min_label = 'min_balance'
                min_threshold = safety.min_balance

            if min_threshold > 0:
                min_part = f'{min_label}: {min_threshold:.2f} (now: {stats.safety_current_value:.2f})'
            else:
                min_part = f'{min_label}: off'

            if safety.max_drawdown_pct > 0:
                dd_part = f'dd: {stats.safety_drawdown_pct:.1f}% / {safety.max_drawdown_pct:.1f}%'
            else:
                dd_part = 'dd: off'

            lines.append(f'         [dim]{min_part}  |  {dd_part}[/dim]')
        return Panel('\n'.join(lines), title='[bold]SESSION[/bold]', box=box.ROUNDED)

    def _build_portfolio_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Portfolio state: delegate to spot/margin branch."""
        if stats.trading_model == 'spot' and stats.spot_balances is not None:
            return self._build_spot_portfolio_panel(stats)
        return self._build_margin_portfolio_panel(stats)

    def _build_margin_portfolio_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Portfolio state (margin): account context, balance, P&L."""
        # Include unrealized P&L from open positions
        unrealized = sum(p.unrealized_pnl for p in stats.open_positions)
        net_pnl = stats.balance - stats.initial_balance + unrealized
        pnl_pct = (net_pnl / stats.initial_balance *
                   100) if stats.initial_balance > 0 else 0.0
        pnl_color = 'green' if net_pnl >= 0 else 'red'
        pnl_sign = '+' if net_pnl >= 0 else ''

        # Use explicit currencies from SymbolSpec (populated by tick loop)
        quote_currency = stats.quote_currency or stats.symbol[-3:]
        base_currency = stats.base_currency or stats.symbol[:-3]

        market_label = self._market_label(stats.broker_type)

        # Dual-currency balance display
        # account_currency tells us which side we hold — the other is estimated from price
        if stats.account_currency == quote_currency:
            # e.g. USD account trading SOLUSD: show USD, estimate SOL equivalent
            balance_line = f'Balance:  {stats.balance:,.6f} {quote_currency}'
            if stats.last_price > 0:
                other_val = stats.balance / stats.last_price
                secondary_line = f'          [dim]≈ {other_val:,.6f} {base_currency} (est.)[/dim]'
            else:
                secondary_line = ''
        else:
            # e.g. SOL account trading SOLUSD: show SOL, estimate USD equivalent
            balance_line = f'Balance:  {stats.balance:,.6f} {base_currency}'
            if stats.last_price > 0:
                other_val = stats.balance * stats.last_price
                secondary_line = f'          [dim]≈ {other_val:,.6f} {quote_currency} (est.)[/dim]'
            else:
                secondary_line = ''

        lines = [
            f'Account:  [bold]{stats.account_currency}[/bold]  [dim]{market_label} | {stats.trading_model} ({stats.broker_type})[/dim]',
            balance_line,
        ]
        if secondary_line:
            lines.append(secondary_line)
        lines += [
            f'Net P&L:  [{pnl_color}]{pnl_sign}{net_pnl:,.6f} ({pnl_sign}{pnl_pct:.2f}%)[/{pnl_color}]',
            f'Trades:   {stats.winning_trades}W / {stats.losing_trades}L',
        ]
        return Panel('\n'.join(lines), title='[bold]PORTFOLIO[/bold]', box=box.ROUNDED)

    def _build_spot_portfolio_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Portfolio state (spot): equity line + dual-balance breakdown."""
        quote_currency = stats.quote_currency or stats.symbol[-3:]
        base_currency = stats.base_currency or stats.symbol[:-3]

        # P&L based on equity change from initial balance (real portfolio value)
        net_pnl = stats.equity - stats.initial_balance
        pnl_pct = (net_pnl / stats.initial_balance *
                   100) if stats.initial_balance > 0 else 0.0
        pnl_color = 'green' if net_pnl >= 0 else 'red'
        pnl_sign = '+' if net_pnl >= 0 else ''

        market_label = self._market_label(stats.broker_type)

        # Account role label: which side does account_currency sit on?
        if stats.account_currency == quote_currency:
            role = 'quote'
        elif stats.account_currency == base_currency:
            role = 'base'
        else:
            role = '?'

        lines = [
            f'Account:  [bold]{stats.account_currency}[/bold] ({role})  [dim]{market_label} | {stats.trading_model} ({stats.broker_type})[/dim]',
            f'Equity:   [bold]{stats.equity:,.6f} {stats.account_currency}[/bold]',
        ]

        # Dual-balance breakdown with cross-conversion
        quote_amount = stats.spot_balances.get(quote_currency, 0.0)
        base_amount = stats.spot_balances.get(base_currency, 0.0)

        if stats.last_price > 0:
            quote_as_base = quote_amount / stats.last_price
            base_as_quote = base_amount * stats.last_price
            lines.append(
                f'  {quote_currency}:    {quote_amount:>14,.6f}  [dim](≈ {quote_as_base:,.6f} {base_currency})[/dim]'
            )
            lines.append(
                f'  {base_currency}:    {base_amount:>14,.6f}  [dim](≈ {base_as_quote:,.6f} {quote_currency})[/dim]'
            )
        else:
            lines.append(f'  {quote_currency}:    {quote_amount:>14,.6f}')
            lines.append(f'  {base_currency}:    {base_amount:>14,.6f}')

        lines += [
            f'Net P&L:  [{pnl_color}]{pnl_sign}{net_pnl:,.6f} ({pnl_sign}{pnl_pct:.2f}%)[/{pnl_color}]',
            f'Trades:   {stats.winning_trades}W / {stats.losing_trades}L',
        ]
        return Panel('\n'.join(lines), title='[bold]PORTFOLIO[/bold]', box=box.ROUNDED)

    @staticmethod
    def _market_label(broker_type: str) -> str:
        """Derive market label (crypto/forex/...) from broker_type."""
        if broker_type.startswith('kraken'):
            return 'crypto'
        if broker_type == 'mt5':
            return 'forex'
        return broker_type

    def _build_positions_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Open positions table."""
        if not stats.open_positions:
            return Panel('[dim]No open positions[/dim]', title='[bold]OPEN POSITIONS[/bold]', box=box.ROUNDED)

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('#', width=3)
        table.add_column('ID', width=16)
        table.add_column('Dir', width=6)
        table.add_column('Lots', width=8, justify='right')
        table.add_column('Entry', width=10, justify='right')
        table.add_column('P&L', width=12, justify='right')

        for idx, pos in enumerate(stats.open_positions, 1):
            pnl_color = 'green' if pos.unrealized_pnl >= 0 else 'red'
            pnl_sign = '+' if pos.unrealized_pnl >= 0 else ''
            dir_color = 'green' if pos.direction == OrderDirection.LONG else 'red'
            table.add_row(
                str(idx),
                pos.position_id[:16],
                f'[{dir_color}]{pos.direction.value}[/{dir_color}]',
                f'{pos.lots:.4f}',
                f'{pos.entry_price:.2f}',
                f'[{pnl_color}]{pnl_sign}{pos.unrealized_pnl:.4f}[/{pnl_color}]',
            )

        return Panel(table, title='[bold]OPEN POSITIONS[/bold]', box=box.ROUNDED)

    def _build_orders_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Active/pending orders table."""
        if not stats.active_orders and stats.pipeline_count == 0:
            if stats.last_rejection:
                return Panel(
                    f'[yellow]⚠  Last rejection: {stats.last_rejection}[/yellow]',
                    title='[bold]ORDERS[/bold]', box=box.ROUNDED,
                )
            return Panel('[dim]No active orders[/dim]', title='[bold]ORDERS[/bold]', box=box.ROUNDED)

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('#', width=3)
        table.add_column('ID', width=16)
        table.add_column('Type', width=8)
        table.add_column('Dir', width=6)
        table.add_column('Price', width=10, justify='right')
        table.add_column('Status', width=10)

        # Active limit/stop orders (broker-accepted, watching)
        for idx, order in enumerate(stats.active_orders, 1):
            table.add_row(
                str(idx),
                order.order_id[:16],
                order.order_type.value,
                order.direction.value,
                f'{order.entry_price:.2f}',
                'WATCHING',
            )

        # Pipeline count (pending, in transit)
        if stats.pipeline_count > 0:
            table.add_row(
                '',
                '',
                '',
                '',
                '',
                f'[yellow on default]■ {stats.pipeline_count} PENDING[/yellow on default]',
            )

        return Panel(table, title='[bold]ORDERS[/bold]', box=box.ROUNDED)

    def _build_trade_history_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Recent completed trades."""
        if not stats.recent_trades:
            return Panel('[dim]No trades yet[/dim]', title='[bold]TRADE HISTORY[/bold]', box=box.ROUNDED)

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('Dir', width=6)
        table.add_column('Lots', width=8, justify='right')
        table.add_column('Entry', width=10, justify='right')
        table.add_column('Exit', width=10, justify='right')
        table.add_column('P&L', width=12, justify='right')
        table.add_column('Reason', width=8)

        for trade in stats.recent_trades[:self._MAX_RECENT_TRADES]:
            pnl_color = 'green' if trade.net_pnl >= 0 else 'red'
            pnl_sign = '+' if trade.net_pnl >= 0 else ''
            dir_color = 'green' if trade.direction == OrderDirection.LONG else 'red'
            table.add_row(
                f'[{dir_color}]{trade.direction.value}[/{dir_color}]',
                f'{trade.lots:.4f}',
                f'{trade.entry_price:.2f}',
                f'{trade.exit_price:.2f}',
                f'[{pnl_color}]{pnl_sign}{trade.net_pnl:.4f}[/{pnl_color}]',
                trade.close_reason.value[:8],
            )

        return Panel(table, title='[bold]TRADE HISTORY[/bold]', box=box.ROUNDED)

    def _build_connection_panel(self, stats: Optional[AutoTraderDisplayStats] = None) -> Panel:
        """Connection stats from tick source (polled directly)."""
        reconnects = self._tick_source.get_reconnect_count()
        emitted = self._tick_source.get_ticks_emitted()
        last_msg_time = self._tick_source.get_last_message_time()
        last_tick_time = self._tick_source.get_last_tick_time()

        # Stream health — based on last WS message (includes heartbeats)
        if last_msg_time:
            msg_age_s = (datetime.now(timezone.utc) -
                         last_msg_time).total_seconds()
            if msg_age_s > 90:
                stream_str = '[red]● dead[/red]'
            elif msg_age_s > 30:
                stream_str = '[yellow]● stale[/yellow]'
            else:
                stream_str = '[green]● connected[/green]'
        else:
            stream_str = '[dim]● waiting[/dim]'

        # Last actual trade tick time
        if last_tick_time:
            tick_age_s = (datetime.now(timezone.utc) -
                          last_tick_time).total_seconds()
            if tick_age_s > 120:
                tick_age_str = f'[yellow]{tick_age_s:.0f}s ago[/yellow]'
            else:
                tick_age_str = f'{tick_age_s:.1f}s ago'
        else:
            tick_age_str = '[dim]—[/dim]'

        # Emitted tick rate (session average)
        if stats and emitted > 0:
            uptime_min = max(0.001, (datetime.now(
                timezone.utc) - stats.session_start).total_seconds() / 60.0)
            emit_rate_str = f'{emitted / uptime_min:.1f}/min'
        else:
            emit_rate_str = '[dim]—[/dim]'

        reconnect_color = 'yellow' if reconnects > 0 else ''
        reconnect_str = f'[{reconnect_color}]{reconnects}[/{reconnect_color}]' if reconnect_color else str(
            reconnects)

        lines = [
            f'Stream:         {stream_str}',
            f'Last Tick:      {tick_age_str}',
            f'Reconnects:     {reconnect_str}',
            f'Emitted Ticks:  {emit_rate_str}',
        ]
        return Panel('\n'.join(lines), title='[bold]CONNECTION[/bold]', box=box.ROUNDED)

    def _build_tick_processing_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Tick processing and clipping stats."""
        clipping_pct = stats.clipping_ratio * 100
        if clipping_pct > 20:
            clip_color = 'red'
        elif clipping_pct > 5:
            clip_color = 'yellow'
        else:
            clip_color = 'green'

        # Clipping bar visualization
        bar_width = 20
        filled = min(bar_width, int(clipping_pct / 100 * bar_width))
        clip_bar = '█' * filled + '░' * (bar_width - filled)

        # Percentiles from processing_times_ms
        p50 = p95 = p99 = 0.0
        if stats.processing_times_ms:
            sorted_times = sorted(stats.processing_times_ms)
            n = len(sorted_times)
            p50 = sorted_times[int(n * 0.50)] if n > 0 else 0.0
            p95 = sorted_times[min(int(n * 0.95), n - 1)] if n > 0 else 0.0
            p99 = sorted_times[min(int(n * 0.99), n - 1)] if n > 0 else 0.0

        lines = [
            f'Avg:      {stats.avg_processing_ms:.2f}ms',
            f'Max:      {stats.max_processing_ms:.2f}ms',
            f'p50={p50:.2f}  p95={p95:.2f}  p99={p99:.2f}',
            f'Clipped:  [{clip_color}]{stats.total_ticks_clipped} ({clipping_pct:.2f}%)[/{clip_color}]',
            f'          [{clip_color}]{clip_bar}[/{clip_color}]',
            f'Queue:    {stats.queue_depth}',
        ]
        return Panel('\n'.join(lines), title='[bold]TICK PROCESSING[/bold]', box=box.ROUNDED)

    def _build_worker_perf_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Per-worker performance bars."""
        if not stats.worker_times_ms:
            return Panel('[dim]No worker data[/dim]', title='[bold]WORKER PERFORMANCE[/bold]', box=box.ROUNDED)

        lines = []
        # Scale: max bar = 50ms (typical tick budget for 20 ticks/sec)
        max_scale_ms = 50.0
        bar_width = 16

        # Decision first (result before details)
        if stats.decision_time_ms > 0:
            d_filled = min(bar_width, int(
                (stats.decision_time_ms / max_scale_ms) * bar_width))
            d_bar = '█' * max(1, d_filled) + '░' * \
                (bar_width - max(1, d_filled))
            d_max = stats.decision_max_time_ms
            lines.append(
                f'{"decision":<16s} {d_bar} {stats.decision_time_ms:.2f}ms  [dim]max {d_max:.2f}ms[/dim]')
            lines.append('─' * 55)

        for name, avg_ms in stats.worker_times_ms.items():
            filled = min(bar_width, int((avg_ms / max_scale_ms) * bar_width))
            bar = '█' * max(1, filled) + '░' * (bar_width - max(1, filled))
            max_ms = stats.worker_max_times_ms.get(name, 0.0)
            lines.append(f'{name:<16s} {bar} {avg_ms:.2f}ms  [dim]max {max_ms:.2f}ms[/dim]')

        return Panel('\n'.join(lines), title='[bold]WORKER PERFORMANCE[/bold]', box=box.ROUNDED)

    def _build_algo_state_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Worker display=True outputs, last decision, and static config params."""
        lines = []
        cache = self._display_label_cache

        # Decision first (result before details)
        action = stats.last_decision_action
        action_color = 'green' if action == DecisionLogicAction.BUY else (
            'red' if action == DecisionLogicAction.SELL else 'dim')
        decision_parts = [f'[{action_color}]{action.value}[/{action_color}]']
        decision_output_labels = cache.decision_output_labels
        for key, value in stats.decision_outputs.items():
            label = decision_output_labels.get(key, key)
            if isinstance(value, float):
                decision_parts.append(f'{label}={value:.2f}')
            else:
                decision_parts.append(f'{label}={value}')
        lines.append(f'Decision:  {" ".join(decision_parts)}')

        # Params line — static decision logic config thresholds (#271)
        if cache.config_param_specs and stats.config_params:
            param_parts = []
            for raw_key, display_key in cache.config_param_specs:
                if raw_key not in stats.config_params:
                    continue
                value = stats.config_params[raw_key]
                if isinstance(value, float):
                    param_parts.append(f'{display_key}={value:.2f}')
                else:
                    param_parts.append(f'{display_key}={value}')
            if param_parts:
                lines.append(f'Params:    {" ".join(param_parts)}')

        # AwarenessChannel — ephemeral narration from decision logic
        if stats.last_awareness is not None:
            awareness = stats.last_awareness
            if awareness.level == AwarenessLevel.ALERT:
                a_color = 'bold red'
                a_icon = '!!'
            elif awareness.level == AwarenessLevel.NOTICE:
                a_color = 'yellow'
                a_icon = '!'
            else:
                a_color = 'dim'
                a_icon = 'i'
            lines.append(f'[{a_color}]  {a_icon} {awareness.message}[/{a_color}]')

        # Event tape — last N strategy moments
        if stats.event_history:
            lines.append('')
            lines.append('Events:')
            for event in stats.event_history:
                if event.level == AwarenessLevel.ALERT:
                    e_color = 'bold red'
                elif event.level == AwarenessLevel.NOTICE:
                    e_color = 'yellow'
                else:
                    e_color = 'dim'
                t_str = event.tick_time.strftime('%H:%M:%S') if event.tick_time else '??:??:??'
                ago = self._format_time_ago(event.tick_time, stats.last_tick_time) if event.tick_time else ''
                ago_suffix = f' ({ago})' if ago else ''
                lines.append(f'[{e_color}]  · {t_str}{ago_suffix} {event.message}[/{e_color}]')
            visible = len(stats.event_history)
            total = stats.total_events_emitted
            if total > visible:
                lines.append(f'[dim]  … (+{total - visible} older events)[/dim]')

        # Worker outputs (details below decision)
        worker_lines = []
        worker_output_labels = cache.worker_output_labels
        for worker_name, outputs in stats.worker_outputs.items():
            labels = worker_output_labels.get(worker_name, {})
            parts = []
            for key, value in outputs.items():
                label = labels.get(key, key)
                if isinstance(value, float):
                    parts.append(f'{label}={value:.4f}')
                else:
                    parts.append(f'{label}={value}')
            if parts:
                worker_lines.append(f'{worker_name:<16s} {", ".join(parts)}')
        if worker_lines:
            lines.append('─' * 40)
            lines.extend(worker_lines)

        if not lines:
            return Panel('[dim]No algo data[/dim]', title='[bold]ALGO STATE[/bold]', box=box.ROUNDED)

        return Panel('\n'.join(lines), title='[bold]ALGO STATE[/bold]', box=box.ROUNDED)

    @staticmethod
    def _format_time_ago(tick_time: datetime, reference_time: Optional[datetime] = None) -> str:
        """
        Human-readable relative time for event tape display.

        Uses reference_time (last tick time) instead of wall-clock so
        the display is correct in both live trading and mock replay.

        Args:
            tick_time: Event tick timestamp (timezone-aware)
            reference_time: Current tick time to measure against (falls back to now())

        Returns:
            Compact string like 'just now', '2m ago', '1h 15m ago'
        """
        now = reference_time or datetime.now(timezone.utc)
        delta = now - tick_time
        seconds = int(delta.total_seconds())
        if seconds < 5:
            return 'just now'
        if seconds < 60:
            return f'{seconds}s ago'
        minutes = seconds // 60
        if minutes < 60:
            return f'{minutes}m ago'
        hours = minutes // 60
        remaining_min = minutes % 60
        if remaining_min == 0:
            return f'{hours}h ago'
        return f'{hours}h {remaining_min}m ago'
