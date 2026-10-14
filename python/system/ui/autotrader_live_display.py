"""
FiniexTestingIDE - AutoTrader Live Console Display
Real-time rich.live dashboard for running AutoTrader sessions (#228).

Architecture:
- Daemon thread polls queue.Queue every 300ms (configurable)
- Tick source stats (reconnects, last_message_time) polled once per frame into a snapshot
- Responsive layout: 3-col (≥160), 2-col (≥120), 1-col (<120)
- In the 3-col and 2-col layouts the three tables — OPEN POSITIONS, ORDERS, TRADE HISTORY — span
  the full width below the columns: a third or a half of the terminal cut their sizes, prices and
  states (#586)
- Panel priority: session/portfolio/positions/orders always shown,
  algo state/connection/clipping/worker perf added with terminal width
- A panel that raises shows as failed in place and is reported to the session log (#586)
"""

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    QuoteFeedState,
    QuoteFeedStats,
    TickSourceSnapshot,
)
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.decision_logic_types import AwarenessLevel, DecisionLogicAction
from python.framework.types.signal_data_types import SignalTransportState
from python.framework.types.trading_env_types.order_types import CloseType, OrderDirection
from python.system.ui.render_failure_reporter import RenderFailureReporter
from python.system.ui.ui_render_utils import prove_printable, rendered_height


class AutoTraderLiveDisplay:
    """
    Real-time console dashboard for AutoTrader sessions.

    Queue-based design: tick loop pushes AutoTraderDisplayStats snapshots,
    display thread drains queue and renders via rich.live.

    Tick source connection stats are polled once per frame (GIL-safe primitives) and handed to
    `render` with the wall clock and the terminal width, so every panel renders from data alone.

    A failure never ends the session and is never silent (#586). A panel whose builder raises
    shows as failed in place; a frame that cannot be printed is replaced by a failure frame;
    either is reported through `on_render_error` — once per failure, and only a few times per
    panel (RenderFailureReporter). Should the display thread still end, trading goes on
    unaffected: the tick loop hands snapshots over without waiting and drops them when nobody
    takes them.

    Args:
        display_queue: Thread-safe queue receiving display stats
        tick_source: Tick source instance (for connection stats polling)
        config: AutoTrader configuration
        dry_run: Whether the session places no real orders
        deployment_id: The deployment this session belongs to, empty for a one-off
        display_label_cache: Labels for decision and worker outputs
        on_render_error: Receives the report of a render failure — the session log's error
            channel in a session; None reports nothing
    """

    # Layout breakpoints
    _WIDTH_THREE_COL = 160
    _WIDTH_TWO_COL = 120
    # The dashboard frame's border and padding: how much narrower its content is than the terminal
    _FRAME_MARGIN = 4

    # Trade history display limit
    _MAX_RECENT_TRADES = 8
    # Active orders display limit
    _MAX_ACTIVE_ORDERS = 8

    def __init__(
        self,
        display_queue: queue.Queue,
        tick_source: AbstractTickSource,
        config: AutoTraderConfig,
        dry_run: bool = True,
        deployment_id: str = '',
        display_label_cache: Optional[DisplayLabelCache] = None,
        on_render_error: Optional[Callable[[str], None]] = None,
    ):
        self._display_queue = display_queue
        self._tick_source = tick_source
        self._config = config
        self._dry_run = dry_run
        self._deployment_id = deployment_id
        self._display_label_cache = display_label_cache or DisplayLabelCache()
        self._update_interval = config.display.update_interval_ms / 1000.0
        self._failure_reporter = RenderFailureReporter(on_render_error)

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
        """
        Main update loop running in display thread.

        Printing happens here, inside the guards, and never in a refresh thread of rich's own:
        a frame whose markup only fails when it is printed is caught like a builder that raises
        (#586). Only a failure of the live display itself ends the loop, and it is reported.
        """
        try:
            # No first frame here: Live prints it on entry, before any guard could catch a
            # frame that fails to print — the loop's first _show() puts it on screen instead
            with Live(
                console=self._console,
                auto_refresh=False,
            ) as live:
                self._live = live

                while self._running:
                    # Drain queue — use latest snapshot only
                    self._drain_queue(limit=100)
                    self._show(live)
                    time.sleep(self._update_interval)

                # Final drain after _running = False — ensures the very last
                # snapshot pushed by the tick loop is rendered, regardless of
                # refresh timing. This runs inside the display thread, so
                # live.update() is safe (same thread that owns the Live context).
                self._drain_queue(limit=None)
                self._show(live)
        except Exception as error:
            self._running = False
            self._failure_reporter.report(
                'The dashboard', error, 'The dashboard has stopped; trading is unaffected.')

    def _drain_queue(self, limit: Optional[int]) -> None:
        """
        Take the newest snapshot off the queue.

        Args:
            limit: How many snapshots to take at most, None for all of them
        """
        taken = 0
        while limit is None or taken < limit:
            try:
                stats = self._display_queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._stats = stats
            taken += 1

    def _show(self, live: Live) -> None:
        """
        Render and print one frame; a frame that cannot be printed is replaced by a failure
        frame and tried again on the next cycle (#586).

        The frame is proven printable BEFORE the live display holds it: a held frame is
        re-rendered whenever any thread prints, so an unprintable one would make the tick
        loop's own logging raise.

        Args:
            live: The live display that owns the console
        """
        frame = self._frame()
        try:
            prove_printable(frame, self._console.size.width, self._console.size.height)
        except Exception as error:
            frame = self._failure_frame(error)
            self._failure_reporter.report(
                'The dashboard frame', error,
                'A failure frame replaces it until it renders again; trading is unaffected.')
        live.update(frame, refresh=True)

    def _frame(self) -> Panel:
        """
        The dashboard for this moment: the newest snapshot, the tick source's readings, the wall
        clock and the terminal width. A frame that cannot be built is a failure frame (#586).

        Returns:
            Rich Panel containing the dashboard
        """
        try:
            with self._lock:
                stats = self._stats
            return self.render(stats, self._poll_tick_source(),
                               datetime.now(timezone.utc), self._console.size.width)
        except Exception as error:
            self._failure_reporter.report(
                'The dashboard frame', error,
                'A failure frame replaces it until it renders again; trading is unaffected.')
            return self._failure_frame(error)

    def _poll_tick_source(self) -> TickSourceSnapshot:
        """
        Read the tick source's connection figures once for this frame.

        Returns:
            The readings the CONNECTION panel renders
        """
        return TickSourceSnapshot(
            reconnect_count=self._tick_source.get_reconnect_count(),
            ticks_emitted=self._tick_source.get_ticks_emitted(),
            last_message_time=self._tick_source.get_last_message_time(),
            last_tick_time=self._tick_source.get_last_tick_time(),
            quote=self._tick_source.get_quote_stats(),
        )

    def _failure_frame(self, error: Exception) -> Panel:
        """
        What stands on screen while a frame cannot be rendered.

        Args:
            error: The failure

        Returns:
            A plain panel naming it
        """
        with self._lock:
            stats = self._stats
        return Panel(
            Text(f'⚠ dashboard render failed: {type(error).__name__} — see the session log',
                 style='yellow'),
            title=self._build_header_title(stats), border_style='yellow', box=box.ROUNDED)

    def _guarded(
        self,
        title: str,
        build: Callable[..., Optional[Panel]],
        *args,
    ) -> Optional[Panel]:
        """
        Build one panel; a builder that raises yields a panel saying so, and the rest of the
        dashboard renders (#586).

        Args:
            title: The panel's title, as the report names it
            build: The panel builder
            args: The builder's arguments

        Returns:
            The panel, the failure panel, or None where the builder has nothing to show
        """
        try:
            return build(*args)
        except Exception as error:
            self._failure_reporter.report(
                f'Panel {title}', error, 'The panel shows as failed; trading is unaffected.')
            return Panel(
                f'[yellow]⚠ panel failed: {type(error).__name__} — see the session log[/yellow]',
                title=f'[bold]{title}[/bold]', box=box.ROUNDED)

    # =========================================================================
    # RENDER — responsive layout
    # =========================================================================

    def render(
        self,
        stats: Optional[AutoTraderDisplayStats],
        connection: TickSourceSnapshot,
        now: datetime,
        width: int,
    ) -> Panel:
        """
        Build the full display panel with responsive layout.

        Everything a frame shows arrives here — the session's snapshot, the tick source's
        readings, the wall clock, the terminal width — so nothing in the render path reads a
        live object, and a test renders every panel without a running session (#586).

        Args:
            stats: The newest snapshot, None before the first one
            connection: The tick source's readings for this frame
            now: The wall clock, for uptime and ages
            width: The terminal width the layout is chosen for

        Returns:
            Rich Panel containing the dashboard
        """
        if stats is None:
            # Race-window only: tick loop pushes a startup pulse before its
            # while-loop, so the display has a frame from t=0. This empty
            # panel is visible at most one refresh cycle, if at all.
            return Panel(
                '',
                title=self._build_header_title(),
                border_style='cyan',
                box=box.ROUNDED,
            )

        if width >= self._WIDTH_THREE_COL:
            body = self._render_three_col(stats, connection, now, width)
        elif width >= self._WIDTH_TWO_COL:
            body = self._render_two_col(stats, connection, now, width)
        else:
            body = self._render_single_col(stats, now, width)

        return Panel(
            body,
            title=self._build_header_title(stats),
            border_style='cyan',
            box=box.ROUNDED,
        )

    def _build_header_title(self, stats: Optional[AutoTraderDisplayStats] = None) -> Text:
        """Build the header title with symbol, mode and config seed hash."""
        symbol = stats.core.symbol if stats else self._config.symbol
        broker = stats.broker_type if stats else self._config.broker_type
        dry_run = stats.dry_run if stats else self._dry_run
        config_hash = stats.config_hash if stats else ''

        title = Text()
        title.append('FiniexAutoTrader', style='bold cyan')
        title.append(f' — {symbol} ({broker})')
        if config_hash:
            title.append(f' [{config_hash}]', style='bright_black')
        title.append(' — ')
        label, style = self._session_mode(dry_run)
        title.append(label, style=style)
        # WHICH KIND OF RUN this is, from the RESOLVED answer rather than the profile's
        # declaration (#497) — a profile that says continuous while `--one-off` is in force
        # must read ONE-OFF here, or this is the `dry_run` near-miss in a new costume. Always
        # shown, never only in the deployment case: an absent label reads as "not checked".
        deployment = stats.deployment_id if stats else self._deployment_id
        title.append(' · ')
        if deployment:
            title.append(f'DEPLOYMENT {deployment}', style='cyan bold')
        else:
            title.append('ONE-OFF', style='bright_black')
        return title

    def _session_mode(self, dry_run: bool) -> Tuple[str, str]:
        """
        What kind of session this is, as the header and the status line name it.

        A mock session is named for what it is. It is also dry — the flag reads true for every
        mock session — but "DRY RUN" is the word for a live-adapter session whose orders the
        venue validates and never places, and a mock session reaches no venue at all.

        Args:
            dry_run: Whether the session places no real orders

        Returns:
            (label, rich style)
        """
        if self._config.adapter_type == 'mock':
            return 'MOCK', 'yellow'
        if dry_run:
            return 'DRY RUN', 'yellow'
        return 'LIVE TRADING', 'green bold'

    # =========================================================================
    # LAYOUT VARIANTS
    # =========================================================================

    def _render_three_col(
        self,
        stats: AutoTraderDisplayStats,
        connection: TickSourceSnapshot,
        now: datetime,
        width: int,
    ) -> Layout:
        """Three-column layout for wide terminals (≥160 cols)."""
        layout = Layout()
        layout.split_row(
            Layout(name='left', ratio=1),
            Layout(name='center', ratio=1),
            Layout(name='right', ratio=1),
        )

        # Left: Session + Connection + Worker Performance (optional — hidden if tracking off)
        left_panels = [
            Layout(self._guarded('SESSION', self._build_session_panel, stats, now, width),
                   name='session'),
            Layout(self._guarded('CONNECTION', self._build_connection_panel,
                                 stats, connection, now), name='connection'),
        ]
        worker_perf = self._guarded('WORKER PERFORMANCE', self._build_worker_perf_panel, stats)
        if worker_perf is not None:
            left_panels.append(Layout(worker_perf, name='worker_perf'))
        api_perf = self._guarded('API PERFORMANCE', self._build_api_perf_panel, stats, now)
        if api_perf is not None:
            left_panels.append(Layout(api_perf, name='api_perf'))
        layout['left'].split_column(*left_panels)

        # Center: Portfolio + Tick Processing + Clipping
        layout['center'].split_column(
            Layout(self._guarded('PORTFOLIO', self._build_portfolio_panel, stats),
                   name='portfolio'),
            Layout(self._guarded('TICK PROCESSING', self._build_tick_processing_panel, stats),
                   name='tick'),
        )

        # Right: Rejections + Algo State
        right_panels = []
        if stats.recent_rejections:
            right_panels.append(Layout(
                self._guarded('REJECTIONS', self._build_rejections_panel, stats, now),
                name='rejections'))
        right_panels.append(Layout(
            self._guarded('ALGO STATE', self._build_algo_state_panel, stats, now), name='algo'))
        layout['right'].split_column(*right_panels)

        return self._with_tables_below(layout, stats, now, width)

    def _render_two_col(
        self,
        stats: AutoTraderDisplayStats,
        connection: TickSourceSnapshot,
        now: datetime,
        width: int,
    ) -> Layout:
        """Two-column layout for medium terminals (≥120 cols)."""
        layout = Layout()
        layout.split_row(
            Layout(name='left', ratio=1),
            Layout(name='right', ratio=1),
        )

        # Left: Session + Portfolio (+ API perf)
        left_panels = [
            Layout(self._guarded('SESSION', self._build_session_panel, stats, now, width),
                   name='session'),
            Layout(self._guarded('PORTFOLIO', self._build_portfolio_panel, stats),
                   name='portfolio'),
        ]
        api_perf = self._guarded('API PERFORMANCE', self._build_api_perf_panel, stats, now)
        if api_perf is not None:
            left_panels.append(Layout(api_perf, name='api_perf'))
        layout['left'].split_column(*left_panels)

        # Right: Rejections + Algo State + Connection — the tables that stood here went below
        right_panels = []
        if stats.recent_rejections:
            right_panels.append(Layout(
                self._guarded('REJECTIONS', self._build_rejections_panel, stats, now),
                name='rejections'))
        right_panels.append(Layout(
            self._guarded('ALGO STATE', self._build_algo_state_panel, stats, now), name='algo'))
        right_panels.append(Layout(
            self._guarded('CONNECTION', self._build_connection_panel, stats, connection, now),
            name='connection'))
        layout['right'].split_column(*right_panels)

        return self._with_tables_below(layout, stats, now, width)

    def _with_tables_below(
        self,
        columns: Layout,
        stats: AutoTraderDisplayStats,
        now: datetime,
        width: int,
    ) -> Layout:
        """
        Put the three tables below the columns — OPEN POSITIONS, ORDERS and, once there are
        trades, TRADE HISTORY — each across the full width and as tall as its rows.

        In a column they had a third or a half of the terminal, and their tables cut sizes,
        prices and states to `0.0…`, `49800…` and `WATCH…` (#586).

        Args:
            columns: The layout's columns
            stats: The snapshot
            now: The wall clock, where the snapshot carries no tick time to measure ages by
            width: The terminal width

        Returns:
            The columns above the tables
        """
        tables = [
            ('positions', self._guarded('OPEN POSITIONS', self._build_positions_panel, stats)),
            ('orders', self._guarded('ORDERS', self._build_orders_panel, stats, now)),
        ]
        if stats.recent_trades:
            tables.append(('trades', self._guarded(
                'TRADE HISTORY', self._build_trade_history_panel, stats, now)))
        table_width = width - self._FRAME_MARGIN
        layout = Layout()
        layout.split_column(columns, *[
            Layout(panel, name=name, size=rendered_height(panel, table_width))
            for name, panel in tables])
        return layout

    def _render_single_col(
        self,
        stats: AutoTraderDisplayStats,
        now: datetime,
        width: int,
    ) -> Layout:
        """Single-column layout for narrow terminals (<120 cols)."""
        layout = Layout()
        panels = [
            Layout(self._guarded('SESSION', self._build_session_panel, stats, now, width),
                   name='session'),
            Layout(self._guarded('PORTFOLIO', self._build_portfolio_panel, stats),
                   name='portfolio'),
            Layout(self._guarded('OPEN POSITIONS', self._build_positions_panel, stats),
                   name='positions'),
            Layout(self._guarded('ORDERS', self._build_orders_panel, stats, now), name='orders'),
        ]
        if stats.recent_rejections:
            panels.append(Layout(
                self._guarded('REJECTIONS', self._build_rejections_panel, stats, now),
                name='rejections'))
        api_perf = self._guarded('API PERFORMANCE', self._build_api_perf_panel, stats, now)
        if api_perf is not None:
            panels.append(Layout(api_perf, name='api_perf'))
        layout.split_column(*panels)
        return layout

    # =========================================================================
    # PANEL BUILDERS
    # =========================================================================

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        """Compact duration label: '4m12s' for >=60s, else '38s'."""
        s = int(seconds)
        return f'{s // 60}m{s % 60:02d}s' if s >= 60 else f'{s}s'

    def _build_session_panel(self, stats: AutoTraderDisplayStats, now: datetime,
                             width: int) -> Panel:
        """
        Session overview: uptime, tick rate, trades, mode.

        Args:
            stats: The snapshot
            now: The wall clock, for the uptime
            width: The terminal width — the Audit line is compact below 120

        Returns:
            The SESSION panel
        """
        uptime = now - stats.session_start
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f'{hours}h {minutes:02d}m {seconds:02d}s'

        label, style = self._session_mode(stats.dry_run)
        mode = f'[{style}]{label}[/{style}]'
        win_rate = (stats.core.winning_trades / stats.core.total_trades *
                    100) if stats.core.total_trades > 0 else 0.0

        if stats.safety_blocked:
            safety_str = f'[red bold]⛔ BLOCKED[/red bold]  [dim]{escape(stats.safety_reason)}[/dim]'
        elif self._config.safety.enabled:
            safety_str = f'[green]● ACTIVE[/green]  [dim]({stats.trading_model})[/dim]'
        else:
            safety_str = '[dim]off[/dim]'

        config_name = escape(self._config.profile_name)
        config_file = escape(self._config.config_path.name) if self._config.config_path else ''
        config_str = f'{config_name}  [dim]({config_file})[/dim]' if config_file else config_name

        # #320 — Status stays RUNNING during heartbeat pulses. The pulse adds
        # a "💓 Ns since last tick" suffix (or "since startup" before the
        # first real tick) so the operator sees the loop is alive without the
        # status switching to a misleading IDLE.
        if stats.is_pulse and stats.seconds_since_last_tick > 0:
            since_label = 'since startup' if stats.core.ticks_processed == 0 else 'since last tick'
            status_str = (
                f'[green]● RUNNING[/green]  '
                f'[dim]💓 {stats.seconds_since_last_tick:.1f}s {since_label}[/dim]'
            )
        else:
            status_str = '[green]● RUNNING[/green]'

        lines = [
            f'Uptime:  {uptime_str}',
            f'Status:  {status_str}',
            f'Processed: {stats.ticks_per_min:.1f}/min  ({stats.core.ticks_processed:,} total)',
            f'Trades:  {stats.core.total_trades}  (Win: {win_rate:.1f}%)',
            f'Mode:    {mode}',
            f'Config:  {config_str}',
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
                # The field a margin profile writes. Both floors denominate the ACCOUNT
                # VALUE since #356 — the name says which key, not which quantity.
                min_label = 'min_balance'
                min_threshold = safety.min_balance

            if min_threshold > 0:
                min_part = f'{min_label}: {min_threshold:.2f} (now: {stats.safety_current_value:.2f})'
            else:
                min_part = f'{min_label}: off'

            if safety.max_drawdown_pct > 0:
                # `safety_dd` rather than `dd` (#497): this is the SAFETY reading, measured
                # against the configured baseline, and the report shows a CURVE reading —
                # peak-to-trough — beside it. Two honest measures that may legitimately
                # differ, and two bare percentages under one word is how they get read as
                # one number that disagrees with itself.
                dd_part = (f'safety_dd: {stats.safety_drawdown_pct:.1f}% / '
                           f'{safety.max_drawdown_pct:.1f}%')
            else:
                dd_part = 'dd: off'

            lines.append(f'         [dim]{min_part}  |  {dd_part}[/dim]')

        # #327 — Drift Audit footer. Width-aware so the SESSION column doesn't
        # wrap in three-col layout. PRICE counter rendered dim (Kraken-intra-
        # reporting consistency check). SLIPPAGE counter (#340) rendered cyan —
        # structural market-reality measurement (real submission-to-fill cost).
        if stats.drift_enabled:
            if width < 120:
                audit_str = (
                    f'[green]✓{stats.drift_audited:d}[/green] │ '
                    f'[yellow]⚠{stats.drift_fee_events:d}F[/yellow] │ '
                    f'⚠{stats.drift_volume_events:d}V │ '
                    f'[dim]{stats.drift_price_events:d}P[/dim] │ '
                    f'[cyan]◇{stats.drift_slippage_events:d}S[/cyan]'
                )
            else:
                max_part = (
                    f' [yellow](max {stats.drift_max_fee_pct:.1f}%)[/yellow]'
                    if stats.drift_fee_events > 0 else ''
                )
                # Slippage is structural — show max whenever audits ran, not
                # only on threshold breaches. Operator sees that measurement
                # happened even when all samples were sub-threshold.
                slip_part = (
                    f' [cyan](max {stats.drift_max_slippage_pct:.2f}%)[/cyan]'
                    if stats.drift_audited > 0 else ''
                )
                audit_str = (
                    f'[green]✓{stats.drift_audited:d}[/green] │ '
                    f'[yellow]⚠{stats.drift_fee_events:d} fee[/yellow]{max_part} │ '
                    f'⚠{stats.drift_volume_events:d} vol │ '
                    f'[dim]{stats.drift_price_events:d} price[/dim] │ '
                    f'[cyan]◇{stats.drift_slippage_events:d} slip[/cyan]{slip_part}'
                )
            lines.append(f'Audit:   {audit_str}')

        # #151 — Reconciliation status line (ALERT_ONLY). Shows the CURRENT state +
        # how long it has held: "clean Xs" (stability streak) when ok, "for Xs"
        # (persistence) when divergent. "next ≤" is the time-based bound — a
        # reconcile may fire sooner on the interval_ticks threshold.
        if stats.reconcile_enabled:
            next_in = stats.reconcile_next_in_s
            if stats.reconcile_count == 0:
                lines.append(f'Reconcile: [dim]awaiting first check (≤{next_in:.0f}s)[/dim]')
            elif stats.reconcile_clean:
                age = self._fmt_duration(stats.reconcile_state_age_s)
                lines.append(
                    f'Reconcile: [green]● ok[/green]  [dim]clean {age} · next ≤{next_in:.0f}s[/dim]'
                )
            else:
                age = self._fmt_duration(stats.reconcile_state_age_s)
                # #355: an order that is OURS but unaccounted for is the actionable
                # subset, so it gets its own marker rather than being one of N.
                unaccounted = (
                    f'[red] · {stats.reconcile_unaccounted} ours, unaccounted[/red]'
                    if stats.reconcile_unaccounted else ''
                )
                lines.append(
                    f'Reconcile: [yellow]⚠️ {stats.reconcile_divergences} divergence(s)[/yellow]'
                    f'{unaccounted}  [dim]for {age} · next ≤{next_in:.0f}s[/dim]'
                )

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
        net_pnl = stats.core.balance - stats.core.initial_balance + unrealized
        pnl_pct = (net_pnl / stats.core.initial_balance *
                   100) if stats.core.initial_balance > 0 else 0.0
        pnl_color = 'green' if net_pnl >= 0 else 'red'
        pnl_sign = '+' if net_pnl >= 0 else ''

        quote_currency = stats.quote_currency
        base_currency = stats.base_currency

        market_label = self._market_label(stats.broker_type)

        # Dual-currency balance display
        # account_currency tells us which side we hold — the other is estimated from price
        if stats.account_currency == quote_currency:
            # e.g. USD account trading SOLUSD: show USD, estimate SOL equivalent
            balance_line = f'Balance:  {stats.core.balance:,.6f} {quote_currency}'
            if stats.last_price > 0:
                other_val = stats.core.balance / stats.last_price
                secondary_line = f'          [dim]≈ {other_val:,.6f} {base_currency} (est.)[/dim]'
            else:
                secondary_line = ''
        else:
            # e.g. SOL account trading SOLUSD: show SOL, estimate USD equivalent
            balance_line = f'Balance:  {stats.core.balance:,.6f} {base_currency}'
            if stats.last_price > 0:
                other_val = stats.core.balance * stats.last_price
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
            f'Trades:   {stats.core.winning_trades}W / {stats.core.losing_trades}L',
        ]
        return Panel('\n'.join(lines), title='[bold]PORTFOLIO[/bold]', box=box.ROUNDED)

    def _build_spot_portfolio_panel(self, stats: AutoTraderDisplayStats) -> Panel:
        """Portfolio state (spot): equity line + dual-balance breakdown."""
        quote_currency = stats.quote_currency
        base_currency = stats.base_currency

        # P&L based on equity change from initial balance (real portfolio value)
        net_pnl = stats.equity - stats.core.initial_balance
        pnl_pct = (net_pnl / stats.core.initial_balance *
                   100) if stats.core.initial_balance > 0 else 0.0
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
            f'Trades:   {stats.core.winning_trades}W / {stats.core.losing_trades}L',
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

        # Minimum widths and no wrapping, as in TRADE HISTORY: a fixed Entry column cut the price
        # range of an entry filled in parts at every terminal width (#586)
        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('#', min_width=3, no_wrap=True)
        table.add_column('ID', min_width=16, no_wrap=True)
        table.add_column('Dir', min_width=6, no_wrap=True)
        table.add_column('Lots', min_width=8, justify='right', no_wrap=True)
        table.add_column('Entry', min_width=10, justify='right', no_wrap=True)
        table.add_column('P&L', min_width=12, justify='right', no_wrap=True)

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

            # Multi-fill sub-row (#330) — only when the entry was matched
            # across multiple broker executions. V1.3 always single-fill;
            # surfaces once #342 (live partial fills) or #143 (order-book sim)
            # produce real multi-element entry_trades lists.
            if len(pos.entry_trades) > 1:
                first_price = pos.entry_trades[0].price
                last_price = pos.entry_trades[-1].price
                table.add_row(
                    '',
                    f'[dim]└─ {len(pos.entry_trades)} fills[/dim]',
                    '',
                    '',
                    f'[dim]{first_price:.2f}→{last_price:.2f}[/dim]',
                    '',
                )

        return Panel(table, title='[bold]OPEN POSITIONS[/bold]', box=box.ROUNDED)

    def _build_orders_panel(self, stats: AutoTraderDisplayStats, now: datetime) -> Panel:
        """
        Active/pending orders table.

        Args:
            stats: The snapshot
            now: The wall clock, where the snapshot carries no tick time to measure ages by

        Returns:
            The ORDERS panel
        """
        if not stats.active_orders and stats.pipeline_count == 0:
            return Panel('[dim]No active orders[/dim]', title='[bold]ORDERS[/bold]', box=box.ROUNDED)

        table = Table(show_header=True, box=None, padding=(0, 1))
        # Minimum widths and no wrapping, as in the other two tables (#586)
        table.add_column('#', min_width=3, no_wrap=True)
        table.add_column('ID', min_width=16, no_wrap=True)
        table.add_column('Type', min_width=8, no_wrap=True)
        table.add_column('Dir', min_width=6, no_wrap=True)
        table.add_column('Price', min_width=10, justify='right', no_wrap=True)
        table.add_column('Status', min_width=10, no_wrap=True)
        table.add_column('Age', min_width=9, no_wrap=True)

        # Active limit/stop orders (broker-accepted, watching) — newest first
        ordered_active = list(reversed(stats.active_orders))
        for idx, order in enumerate(ordered_active[:self._MAX_ACTIVE_ORDERS], 1):
            table.add_row(
                str(idx),
                order.order_id[:16],
                order.order_type.value,
                order.direction.value,
                f'{order.entry_price:.2f}',
                'WATCHING',
                (self._format_time_ago(order.submitted_at, stats.last_tick_time or now)
                 if order.submitted_at else ''),
            )

            # Partial-fill sub-row (#330) — shown for an order taken over at a cold start
            # partly filled; an ordinary resting order's partial execution is mirrored only
            # once #342 lands. A protective order reads requested_lots 0.0 and never shows it.
            # The figures stand in the ID column: the 10-wide Status column cut them off.
            if order.cumulative_filled_lots > 0 and order.cumulative_filled_lots < order.requested_lots:
                progress = (f'[dim]└─ {order.cumulative_filled_lots:.4f}/'
                            f'{order.requested_lots:.4f}[/dim]')
                table.add_row('', progress, '', '', '', '[dim]PARTIAL[/dim]')

        hidden_orders = len(ordered_active) - self._MAX_ACTIVE_ORDERS
        if hidden_orders > 0:
            table.add_row('', f'[dim]… +{hidden_orders} more[/dim]', '', '', '', '')

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

    def _build_rejections_panel(self, stats: AutoTraderDisplayStats, now: datetime) -> Panel:
        """
        Order rejections — its own panel, included only when at least one occurred.

        Newest first; each row carries a running #N (so a NEW rejection is
        distinguishable from an old one persisting on screen) and an age. Kept
        separate from the ORDERS panel so order display and rejection status no
        longer flicker against each other. The venue's words are escaped: a bracket in
        them is text, never markup (#586).

        Args:
            stats: The snapshot
            now: The wall clock, where the snapshot carries no tick time to measure ages by

        Returns:
            The REJECTIONS panel
        """
        lines = []
        for rej in reversed(stats.recent_rejections):
            ago = (self._format_time_ago(rej.tick_time, stats.last_tick_time or now)
                   if rej.tick_time else '')
            ago_suffix = f' ({ago})' if ago else ''
            msg = f' — {escape(rej.message)}' if rej.message else ''
            lines.append(
                f'[yellow]  ⚠ #{rej.seq}{ago_suffix}  {escape(str(rej.reason))}{msg} '
                f'({escape(str(rej.side))})[/yellow]'
            )
        hidden = stats.total_rejections - len(stats.recent_rejections)
        if hidden > 0:
            lines.append(f'[dim]  … +{hidden} earlier[/dim]')
        return Panel(
            '\n'.join(lines),
            title=f'[bold]REJECTIONS ({stats.total_rejections})[/bold]',
            box=box.ROUNDED,
        )

    def _build_trade_history_panel(self, stats: AutoTraderDisplayStats, now: datetime) -> Panel:
        """
        Recent completed trades.

        Args:
            stats: The snapshot
            now: The wall clock, where the snapshot carries no tick time to measure ages by

        Returns:
            The TRADE HISTORY panel
        """
        if not stats.recent_trades:
            return Panel('[dim]No trades yet[/dim]', title='[bold]TRADE HISTORY[/bold]', box=box.ROUNDED)

        # Minimum widths, never fixed ones, and no wrapping: a column grows to what it holds — the
        # price range of a trade filled in parts is wider than a price — and the full-width row
        # this panel stands in leaves room for that (#586)
        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('Side', min_width=6, no_wrap=True)
        table.add_column('Lots', min_width=8, justify='right', no_wrap=True)
        table.add_column('Entry', min_width=10, justify='right', no_wrap=True)
        table.add_column('Exit', min_width=10, justify='right', no_wrap=True)
        table.add_column('P&L', min_width=12, justify='right', no_wrap=True)
        table.add_column('Reason', min_width=8, no_wrap=True)
        table.add_column('Age', min_width=9, no_wrap=True)

        for trade in stats.recent_trades[:self._MAX_RECENT_TRADES]:
            pnl_color = 'green' if trade.net_pnl >= 0 else 'red'
            pnl_sign = '+' if trade.net_pnl >= 0 else ''
            # Color by underlying position direction (so a LONG-close and a
            # SHORT-close keep their identity in the panel); text shows the
            # trade-event side (BUY/SELL — what the close operation was).
            dir_color = 'green' if trade.direction == OrderDirection.LONG else 'red'
            side_text = trade.exit_side.value if trade.exit_side else trade.direction.value

            # Reason column doubles as close_type marker (#330): when the
            # real close_reason is MANUAL (empty string), surface the partial
            # nature so a sequence of N closes on one position is visually
            # connected. Non-MANUAL reasons (SL/TP/scenario_end) keep their
            # value — close_type is then deducible from the lots column.
            #   PARTIAL              → 'partial'
            #   FULL remainder of a  → 'remain'   (the position opened larger than
            #     partial chain                     this close)
            #   FULL standalone      → empty (today's behavior)
            # The size at entry is the record's own `entry_lots`. The first entry FILL said the
            # same only while a position opened in one fill; with several it was a slice.
            reason_text = trade.close_reason.value[:8]
            if not reason_text:
                if trade.close_type == CloseType.PARTIAL:
                    reason_text = 'partial'
                elif trade.entry_lots is not None and trade.entry_lots > trade.lots:
                    reason_text = 'remain'

            table.add_row(
                f'[{dir_color}]{side_text}[/{dir_color}]',
                f'{trade.lots:.4f}',
                f'{trade.entry_price:.2f}',
                f'{trade.exit_price:.2f}',
                f'[{pnl_color}]{pnl_sign}{trade.net_pnl:.4f}[/{pnl_color}]',
                reason_text,
                (self._format_time_ago(trade.exit_time, stats.last_tick_time or now)
                 if trade.exit_time else ''),
            )

            # Per-side multi-fill sub-rows (#330) — only when broker produced
            # more than one execution on the entry or exit side. V1.3 always
            # single-fill in both directions; activates with #342 / #143.
            if len(trade.entry_trades) > 1:
                first_p = trade.entry_trades[0].price
                last_p = trade.entry_trades[-1].price
                table.add_row(
                    '',
                    f'[dim]└─ in {len(trade.entry_trades)}[/dim]',
                    f'[dim]{first_p:.2f}→{last_p:.2f}[/dim]',
                    '', '', '',
                )
            if len(trade.exit_trades) > 1:
                first_p = trade.exit_trades[0].price
                last_p = trade.exit_trades[-1].price
                table.add_row(
                    '',
                    f'[dim]└─ out {len(trade.exit_trades)}[/dim]',
                    '',
                    f'[dim]{first_p:.2f}→{last_p:.2f}[/dim]',
                    '', '',
                )

        hidden_trades = stats.core.total_trades - self._MAX_RECENT_TRADES
        if hidden_trades > 0:
            table.add_row('', f'[dim]… +{hidden_trades} more[/dim]', '', '', '', '')

        return Panel(table, title='[bold]TRADE HISTORY[/bold]', box=box.ROUNDED)

    def _build_connection_panel(
        self,
        stats: Optional[AutoTraderDisplayStats],
        connection: TickSourceSnapshot,
        now: datetime,
    ) -> Panel:
        """
        Connection stats from the tick source, read once for this frame.

        Args:
            stats: The snapshot, None before the first one
            connection: The tick source's readings
            now: The wall clock, for stream and tick ages

        Returns:
            The CONNECTION panel
        """
        reconnects = connection.reconnect_count
        emitted = connection.ticks_emitted
        last_msg_time = connection.last_message_time
        last_tick_time = connection.last_tick_time

        # Stream health — based on last WS message (includes heartbeats)
        if last_msg_time:
            msg_age_s = (now - last_msg_time).total_seconds()
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
            tick_age_s = (now - last_tick_time).total_seconds()
            if tick_age_s > 120:
                tick_age_str = f'[yellow]{tick_age_s:.0f}s ago[/yellow]'
            else:
                tick_age_str = f'{tick_age_s:.1f}s ago'
        else:
            tick_age_str = '[dim]—[/dim]'

        # Market-data staleness contract (#436) — contract-driven tag next to
        # the transport colors above (threshold from the profile, not hardcoded)
        if stats and stats.market_stale:
            tick_age_str += ' [yellow][STALE][/yellow]'

        # Emitted tick rate (session average)
        if stats and emitted > 0:
            uptime_min = max(0.001, (now - stats.session_start).total_seconds() / 60.0)
            emit_rate_str = f'{emitted / uptime_min:.1f}/min  ({emitted:,} total)'
        else:
            emit_rate_str = '[dim]—[/dim]'

        reconnect_color = 'yellow' if reconnects > 0 else ''
        reconnect_str = f'[{reconnect_color}]{reconnects}[/{reconnect_color}]' if reconnect_color else str(
            reconnects)

        # Current market price (mid of last tick) — lets the operator see where
        # the market stands vs. a resting limit order's price.
        if stats and stats.last_price > 0:
            price_str = f'{stats.last_price:.2f}'
        else:
            price_str = '[dim]—[/dim]'

        lines = [
            f'Stream:         {stream_str}',
            f'Last Tick:      {tick_age_str}',
            f'Last Price:     {price_str}',
            f'Spread:         {self._format_quote(connection.quote)}',
            f'Reconnects:     {reconnect_str}',
            f'Emitted Ticks:  {emit_rate_str}',
        ]
        lines.extend(self._build_signal_transport_lines(stats, now))
        return Panel('\n'.join(lines), title='[bold]CONNECTION[/bold]', box=box.ROUNDED)

    def _format_quote(self, quote: Optional[QuoteFeedStats]) -> str:
        """
        Render the quote channel as spread plus the age of the quote it came from (#520).

        Both, always, because the spread alone cannot tell a narrow market from a quote cache
        that stopped updating — a stuck cache shows a frozen spread beside an age that keeps
        climbing, and no log line makes that as obvious.

        Presentation only: the colour marks a stale quote, it never decides anything. Whether a
        stale quote WARRANTS a warning is a verdict and belongs to a validator (§12).

        Args:
            quote: Snapshot from the tick source, None for sources without a quote channel

        Returns:
            Rich-markup line for the CONNECTION panel
        """
        if quote is None:
            return '[dim]— (no quote channel)[/dim]'
        if quote.state == QuoteFeedState.OFF:
            return '[dim]— (off)[/dim]'
        if quote.state == QuoteFeedState.DEGRADED:
            return '[yellow]— (degraded: no ticker)[/yellow]'
        if quote.quote_age_ms is None:
            return '[dim]— (waiting for quote)[/dim]'

        age_ms = quote.quote_age_ms
        if age_ms >= 10_000:
            age_str = f'[yellow]quote {age_ms / 1000:.0f}s[/yellow]'
        elif age_ms >= 1000:
            age_str = f'quote {age_ms / 1000:.1f}s'
        else:
            age_str = f'quote {age_ms}ms'

        return f'{quote.spread:.5g}  ({quote.spread_pct:.4f}%)   {age_str}'

    def _build_signal_transport_lines(self, stats, now: Optional[datetime] = None) -> list:
        """
        Signal-transport block for the CONNECTION panel (#141 Part 2a).

        Belongs beside the tick transport rather than in its own panel: both answer the
        same operator question, and splitting them across two places is how one of them
        stops being checked. Transport facts only — the signal VALUES have their own panel.

        Args:
            stats: Current display stats
            now: The wall clock for the envelope's age; None reads it here

        Returns:
            Lines to append (empty when no session data exists yet)
        """
        if stats is None:
            return []
        transport = stats.signal_transport
        if not transport.configured:
            # A mounted session replays its series from the archive; saying so is more
            # useful than rendering an idle connection that was never meant to run.
            return ['', '[dim]Signal Feed:    mounted (no transport)[/dim]']

        # Every dead-feed state is red. Left on the default 'dim' they under-signalled
        # exactly the silence this panel exists to surface — and the two the stream added
        # are the hardest of them: 'cursor_ahead' says our own store was restored and a
        # human must decide, 'misconfigured' says the request itself is wrong and no
        # amount of waiting will fix it. Both stop the feed for the rest of the session.
        colors = {SignalTransportState.LIVE: 'green', SignalTransportState.REPLAY: 'yellow',
                  SignalTransportState.CONNECTING: 'yellow', SignalTransportState.ERROR: 'red',
                  SignalTransportState.UNAUTHORIZED: 'red', SignalTransportState.CONTRACT: 'red',
                  SignalTransportState.CURSOR_AHEAD: 'bold red',
                  SignalTransportState.MISCONFIGURED: 'bold red'}
        color = colors.get(transport.state, 'dim')
        marker = '●' if transport.state == SignalTransportState.LIVE else '◌'
        position = (f'epoch {transport.stream_epoch}  seq {transport.last_seq}'
                    if transport.last_seq is not None else 'awaiting first envelope')

        if transport.last_envelope_at is not None:
            age_s = ((now or datetime.now(timezone.utc))
                     - transport.last_envelope_at).total_seconds()
            age_str = self._format_age(age_s)
        else:
            age_str = '[dim]—[/dim]'

        lines = [
            '',
            f'[{color}]Signal Feed:    {marker} {transport.state.value}   {position}[/{color}]',
        ]
        lines.extend(self._build_journal_lines(transport.health))
        lines.append(f'Last Envelope:  {age_str}')
        # contract_errors belongs here too: gating the line on the transport counter alone hid
        # the one failure the operator can act on — the producer answered and we could not
        # read it.
        if transport.transport_errors or transport.contract_errors:
            lines.append(
                f'[yellow]Feed Issues:    {transport.transport_errors} transport · '
                f'{transport.contract_errors} contract[/yellow]')

        if transport.tape:
            lines.append('Feed Tape:')
            for event in reversed(transport.tape):
                e_color = {AwarenessLevel.ALERT: 'bold red',
                           AwarenessLevel.NOTICE: 'yellow'}.get(event.level, 'dim')
                stamp = event.at.strftime('%H:%M:%S')
                lines.append(f'[{e_color}]  · {stamp} {escape(event.message)}[/{e_color}]')
            hidden = transport.total_events - len(transport.tape)
            if hidden > 0:
                lines.append(f'[dim]  … (+{hidden} older feed events)[/dim]')
        return lines

    def _build_journal_lines(self, health) -> list:
        """
        Producer-identity line for the CONNECTION panel (#141 Part 2a).

        The id is shown and the name is shown beside it, never the name alone: the id is
        a fingerprint of the producer's store and binds, while the name is resolved from
        a mapping on the producer's machine and can be renamed at any time. A run judged
        by the label alone is a run judged by a claim.

        Args:
            health: Producer identity as last probed

        Returns:
            Lines to append (empty when no probe is attached)
        """
        if health.probed_at is None and not health.probe_errors:
            return []
        if not health.is_identified():
            # A real answer, not a missing one: the producer has no store attached or
            # cannot read its own identifier. Either way nothing certifies this session.
            return ['[red]Journal:        ⚠ unidentified[/red]']
        if health.journal_changed:
            lines = [f'[bold red]Journal:        {escape(str(health.journal_id))} '
                     f'({escape(str(health.journal_name))})  ⚠ CHANGED[/bold red]']
        else:
            lines = [f'Journal:        {escape(str(health.journal_id))}  '
                     f'[dim]({escape(str(health.journal_name))})[/dim]']
        if health.budget_suspended:
            # Rendered only while it holds: a suspended producer stops sending while the
            # transport stays healthy, so this is the line that explains a silence the
            # rest of the panel would report as everything being fine.
            reason = f' — {escape(health.budget_reason)}' if health.budget_reason else ''
            lines.append(f'[yellow]Producer:       ⚠ budget suspended{reason}[/yellow]')
        return lines

    @staticmethod
    def _format_age(age_s: float) -> str:
        """
        Render an age compactly.

        Args:
            age_s: Age in seconds

        Returns:
            Human-readable age
        """
        if age_s < 60:
            return f'{age_s:.0f}s ago'
        if age_s < 3600:
            return f'{age_s / 60:.0f}m {age_s % 60:.0f}s ago'
        return f'{age_s / 3600:.1f}h ago'

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

        # Percentiles from processing_times_ms — the monitor's recent window, not the session,
        # so the line says how many ticks it covers
        p50 = p95 = p99 = 0.0
        n = len(stats.processing_times_ms)
        if n:
            sorted_times = sorted(stats.processing_times_ms)
            p50 = sorted_times[int(n * 0.50)]
            p95 = sorted_times[min(int(n * 0.95), n - 1)]
            p99 = sorted_times[min(int(n * 0.99), n - 1)]

        lines = [
            f'Avg:      {stats.avg_processing_ms:.2f}ms',
            f'Max:      {stats.max_processing_ms:.2f}ms',
            f'p50={p50:.2f}  p95={p95:.2f}  p99={p99:.2f}',
            f'          last {n:,} ticks',
            f'Clipped:  [{clip_color}]{stats.total_ticks_clipped} ({clipping_pct:.2f}%)[/{clip_color}]',
            f'          [{clip_color}]{clip_bar}[/{clip_color}]',
            f'Queue:    {stats.queue_depth}',
        ]
        return Panel('\n'.join(lines), title='[bold]TICK PROCESSING[/bold]', box=box.ROUNDED)

    def _build_worker_perf_panel(self, stats: AutoTraderDisplayStats) -> Optional[Panel]:
        """Per-worker performance bars. Returns None when Layer-A tracking is disabled (#137)."""
        if not stats.worker_times_ms:
            return None

        lines = []
        # Scale: max bar = 50ms (typical tick budget for 20 ticks/sec)
        max_scale_ms = 50.0
        bar_width = 16

        # Decision first (result before details)
        if stats.decision_time_ms > 0:
            # Short name: "CORE/aggressive_trend" → "aggressive_trend"
            dl_type = self._config.strategy_config.get('decision_logic_type', '')
            dl_label = escape((dl_type.split('/')[-1] if dl_type else 'decision')[:16])
            rolling_ms = stats.decision_rolling_avg_ms
            display_ms = rolling_ms if rolling_ms > 0 else stats.decision_time_ms
            d_filled = min(bar_width, int((display_ms / max_scale_ms) * bar_width))
            d_bar = '█' * max(1, d_filled) + '░' * (bar_width - max(1, d_filled))
            d_max = stats.decision_max_time_ms
            lines.append(
                f'{dl_label:<16s} {d_bar} avg={display_ms:.2f}ms  [dim]max {d_max:.2f}ms[/dim]')
            lines.append('─' * 40)

        for name, avg_ms in stats.worker_times_ms.items():
            rolling_ms = stats.worker_rolling_avg_times_ms.get(name, 0.0)
            display_ms = rolling_ms if rolling_ms > 0 else avg_ms
            filled = min(bar_width, int((display_ms / max_scale_ms) * bar_width))
            bar = '█' * max(1, filled) + '░' * (bar_width - max(1, filled))
            max_ms = stats.worker_max_times_ms.get(name, 0.0)
            lines.append(f'{escape(f"{name:<16s}")} {bar} avg={display_ms:.2f}ms  '
                         f'[dim]max {max_ms:.2f}ms[/dim]')

        return Panel('\n'.join(lines), title='[bold]WORKER PERFORMANCE[/bold]', box=box.ROUNDED)

    def _build_api_perf_panel(self, stats: AutoTraderDisplayStats,
                              now: datetime) -> Optional[Panel]:
        """
        Per-endpoint broker REST latency + errors (#351). One row per endpoint
        (a new endpoint adds a row; repeat calls update it). Returns None when no
        monitor is wired / no calls yet. Rows over the slow threshold are red.

        Args:
            stats: The snapshot
            now: The wall clock, for each endpoint's last call

        Returns:
            The API PERFORMANCE panel, or None
        """
        snap = stats.api_perf
        if snap is None or not snap.endpoints:
            return None

        threshold = self._config.api_monitor.slow_call_threshold_ms
        lines = []
        for s in snap.endpoints:
            name = s.endpoint.rsplit('/', 1)[-1][:16]   # /0/private/OpenOrders → OpenOrders
            age = (f'{self._fmt_duration((now - s.last_fired_at).total_seconds())} ago'
                   if s.last_fired_at else '—')
            max_str = f'{s.max_ms:.0f}ms'
            if s.max_ms > threshold:
                max_str = f'[red]{max_str}[/red]'
            err = f'  [yellow]⚠{s.error_count}[/yellow]' if s.error_count else ''
            lines.append(
                f'{name:<16s} {s.count:>3}× avg {s.avg_ms:.0f}ms · max {max_str} · {age}{err}')
        lines.append('─' * 40)
        lines.append(
            f'[dim]slow >{threshold:.0f}ms: {snap.slow_count} · errors: {snap.total_errors}[/dim]')
        return Panel('\n'.join(lines), title='[bold]API PERFORMANCE[/bold]', box=box.ROUNDED)

    def _build_algo_state_panel(self, stats: AutoTraderDisplayStats, now: datetime) -> Panel:
        """
        Worker display=True outputs, last decision, and static config params.

        The strategy's own words — its awareness, its events, its output values — are escaped:
        a bracket in them is text, never markup (#586).

        Args:
            stats: The snapshot
            now: The wall clock, where the snapshot carries no tick time to measure ages by

        Returns:
            The ALGO STATE panel
        """
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
                decision_parts.append(f'{escape(str(label))}={value:.2f}')
            else:
                decision_parts.append(f'{escape(str(label))}={escape(str(value))}')
        lines.append(f'Decision:  {" ".join(decision_parts)}')

        # Params line — static decision logic config thresholds (#271)
        if cache.config_param_specs and stats.config_params:
            param_parts = []
            for raw_key, display_key in cache.config_param_specs:
                if raw_key not in stats.config_params:
                    continue
                value = stats.config_params[raw_key]
                if isinstance(value, float):
                    param_parts.append(f'{escape(str(display_key))}={value:.2f}')
                else:
                    param_parts.append(f'{escape(str(display_key))}={escape(str(value))}')
            if param_parts:
                lines.append(f'Params:    {" ".join(param_parts)}')

        # AwarenessChannel — ephemeral narration from decision logic
        if stats.core.last_awareness is not None:
            awareness = stats.core.last_awareness
            if awareness.level == AwarenessLevel.ALERT:
                a_color = 'bold red'
                a_icon = '!!'
            elif awareness.level == AwarenessLevel.NOTICE:
                a_color = 'yellow'
                a_icon = '!'
            else:
                a_color = 'dim'
                a_icon = 'i'
            lines.append(f'[{a_color}]  {a_icon} {escape(awareness.message)}[/{a_color}]')

        # Event tape — last N strategy moments, newest first
        if stats.event_history:
            lines.append('')
            lines.append('Events:')
            for event in reversed(stats.event_history):
                if event.level == AwarenessLevel.ALERT:
                    e_color = 'bold red'
                elif event.level == AwarenessLevel.NOTICE:
                    e_color = 'yellow'
                else:
                    e_color = 'dim'
                t_str = event.tick_time.strftime('%H:%M:%S') if event.tick_time else '??:??:??'
                ago = (self._format_time_ago(event.tick_time, stats.last_tick_time or now)
                       if event.tick_time else '')
                ago_suffix = f' ({ago})' if ago else ''
                lines.append(
                    f'[{e_color}]  · {t_str}{ago_suffix} {escape(event.message)}[/{e_color}]')
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
                    parts.append(f'{escape(str(label))}={value:.4f}')
                else:
                    parts.append(f'{escape(str(label))}={escape(str(value))}')
            if parts:
                worker_lines.append(f'{escape(f"{worker_name:<16s}")} {", ".join(parts)}')
        if worker_lines:
            lines.append('─' * 40)
            # Sentiment feed line (#431) — envelope-flagged when a SIGNAL result is stale (#434)
            if cache.data_sentiment_type:
                if stats.feed_stale:
                    lines.append(
                        f'[yellow]📡 Feed: {escape(cache.data_sentiment_type)} [STALE][/yellow]')
                else:
                    lines.append(f'📡 Feed: {escape(cache.data_sentiment_type)}')
            lines.extend(worker_lines)

        if not lines:
            return Panel('[dim]No algo data[/dim]', title='[bold]ALGO STATE[/bold]', box=box.ROUNDED)

        return Panel('\n'.join(lines), title='[bold]ALGO STATE[/bold]', box=box.ROUNDED)

    @staticmethod
    def _format_time_ago(tick_time: datetime, reference_time: datetime) -> str:
        """
        Human-readable relative time for event tape display.

        Uses reference_time (last tick time) instead of wall-clock so
        the display is correct in both a live-adapter session and a mock session.
        Callers hand in the frame's wall clock where no tick time exists yet.

        Args:
            tick_time: Event tick timestamp (timezone-aware)
            reference_time: Current tick time to measure against

        Returns:
            Compact string like 'just now', '2m ago', '1h 15m ago'
        """
        delta = reference_time - tick_time
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
