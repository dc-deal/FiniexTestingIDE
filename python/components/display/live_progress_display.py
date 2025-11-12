"""
FiniexTestingIDE - Live Progress Display
Real-time scenario execution progress with queue-based updates

Features:
- Overhead line: System resources (CPU, RAM) + scenario count
- Per-scenario lines: Progress bar, time, portfolio, trades
- Thread-based polling (300ms updates)
- Flicker-free display using rich.live
- Queue-based updates (ProcessPool compatible)
- Graceful shutdown
- Fully typed with LiveScenarioStats
- Type-safe status handling with ScenarioStatus enum

Usage:
    # In BatchOrchestrator
    display = LiveProgressDisplay(scenarios, live_queue)
    display.start()
    # ... run scenarios ...
    display.stop()
"""

import threading
import time
import traceback
import psutil
from multiprocessing import Queue
from typing import List, Optional, Dict
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box

from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.live_scenario_stats_types import LiveScenarioStats, ScenarioStatus
from python.components.logger.bootstrap_logger import get_logger

vLog = get_logger()


class LiveProgressDisplay:
    """
    Live progress display for scenario execution.

    Queue-based design for ProcessPool compatibility.
    Reads updates from multiprocessing.Queue and renders display.

    Shows:
    - System resources (CPU, RAM)
    - Number of running/completed scenarios
    - Per-scenario progress bars with stats
    - Real-time updates every 300ms
    """

    def __init__(
        self,
        scenarios: List[SingleScenario],
        live_queue: Queue,
        update_interval: float = 0.3
    ):
        """
        Initialize live progress display.

        Args:
            scenarios: List of scenarios to track
            live_queue: Queue for receiving live updates
            update_interval: Update interval in seconds (default: 0.3)
        """
        self.scenarios = scenarios
        self.live_queue = live_queue
        self.update_interval = update_interval

        # Local stats cache (updated from queue)
        self._stats_cache: Dict[int, LiveScenarioStats] = {}
        self._init_stats_cache()

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Rich console
        self.console = Console()
        self._live: Optional[Live] = None

    def _init_stats_cache(self) -> None:
        """Initialize stats cache with INITIALIZED status."""
        for idx, scenario in enumerate(self.scenarios):
            self._stats_cache[idx] = LiveScenarioStats(
                scenario_name=scenario.name,
                symbol=scenario.symbol,
                scenario_index=idx,
                status=ScenarioStatus.INITIALIZED
            )

    def start(self) -> None:
        """Start the live display thread."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._thread = threading.Thread(
                target=self._update_loop,
                daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the live display thread."""
        # Final render BEFORE stopping
        if self._live:
            try:
                self._live.update(self._render())
                time.sleep(0.5)  # Give it time to display
            except:
                pass

        with self._lock:
            self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)

        # Stop live display
        if self._live:
            self._live.stop()

    def _update_loop(self) -> None:
        """Main update loop running in thread."""
        with Live(
            self._render(),
            console=self.console,
            refresh_per_second=2
        ) as live:
            self._live = live

            while self._running:
                try:
                    # === 1. AGGRESSIV Queue lesen - NICHT empty() prüfen! ===
                    updates_processed = 0
                    max_updates_per_cycle = 100  # Prevent infinite loop

                    for _ in range(max_updates_per_cycle):
                        try:
                            # Versuche zu lesen - wirft Exception wenn leer
                            update = self.live_queue.get_nowait()
                            self._process_update(update)
                            updates_processed += 1
                        except:
                            # Queue ist wirklich leer
                            break

                    # === 2. Render display (IMMER!) ===
                    live.update(self._render())

                    # === 3. HART warten ===
                    time.sleep(self.update_interval)

                except Exception as e:
                    vLog.error(f"\n❌ CRITICAL ERROR in LiveProgressDisplay:")
                    vLog.error(traceback.format_exc())
                    self._running = False
                    raise

    def _process_update(self, update: dict) -> None:
        """
        Process a single queue update and update cache.

        Args:
            update: Update message from queue
        """
        # === REGULÄRE UPDATES (benötigen scenario_index) ===
        scenario_index = update.get("scenario_index")
        if scenario_index is None:
            return

        update_type = update.get("type", "progress")
        with self._lock:
            stats = self._stats_cache.get(scenario_index)
            if not stats:
                return

            # Status-only updates
            if update_type == "status":
                status_str = update.get("status", "initialized")
                stats.status = ScenarioStatus(status_str)
                return

            # Progress updates
            if update_type == "progress":
                # Progress
                stats.ticks_processed = update.get(
                    "ticks_processed", stats.ticks_processed)
                stats.total_ticks = update.get(
                    "total_ticks", stats.total_ticks)
                stats.progress_percent = update.get(
                    "progress_percent", stats.progress_percent)

                # In-Time tracking
                stats.first_tick_time = update.get(
                    "first_tick_time", stats.first_tick_time)
                stats.current_tick_time = update.get(
                    "current_tick_time", stats.current_tick_time)
                stats.tick_timespan_seconds = update.get(
                    "tick_timespan_seconds",
                    stats.tick_timespan_seconds
                )

                # Basic Portfolio
                stats.current_balance = update.get(
                    "current_balance", stats.current_balance)
                stats.initial_balance = update.get(
                    "initial_balance", stats.initial_balance)
                stats.total_trades = update.get(
                    "total_trades", stats.total_trades)
                stats.winning_trades = update.get(
                    "winning_trades", stats.winning_trades)
                stats.losing_trades = update.get(
                    "losing_trades", stats.losing_trades)
                stats.portfolio_dirty = update.get(
                    "portfolio_dirty", stats.portfolio_dirty)

                # Status
                status_str = update.get("status", "running")
                stats.status = ScenarioStatus(status_str)

                # Detailed exports (if present)
                # Note: portfolio_stats, performance_stats, current_bars
                # are available in update but not stored in LiveScenarioStats
                # Display can access them directly from update if needed

    def _render(self) -> Panel:
        """
        Render the live display.

        Returns:
            Rich Panel with overhead + scenario progress
        """
        # Get all stats from cache
        with self._lock:
            all_stats = list(self._stats_cache.values())

        # Build overhead line
        overhead = self._build_overhead(all_stats)

        # Build scenario table
        scenario_table = self._build_scenario_table(all_stats)

        # Combine into panel
        layout = Layout()
        layout.split_column(
            Layout(overhead, size=3),
            Layout(scenario_table)
        )

        return Panel(
            layout,
            title="[bold cyan]🔬 Strategy Execution Progress[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED
        )

    def _build_overhead(self, all_stats: List[LiveScenarioStats]) -> str:
        """
        Build overhead resource line.

        Args:
            all_stats: List of LiveScenarioStats objects

        Returns:
            Formatted string with system resources
        """
        # Count scenarios by status
        running_count = sum(
            1 for s in all_stats if s.status == ScenarioStatus.RUNNING
        )
        completed_count = sum(
            1 for s in all_stats if s.status == ScenarioStatus.COMPLETED
        )
        total_count = len(self.scenarios)

        # System resources
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            ram_used_gb = mem.used / (1024**3)
            ram_total_gb = mem.total / (1024**3)
        except Exception:
            cpu_percent = 0.0
            ram_used_gb = 0.0
            ram_total_gb = 0.0

        # Format
        overhead = (
            f"[bold yellow]⚡ System Resources[/bold yellow] │ "
            f"[cyan]CPU:[/cyan] {cpu_percent:>5.1f}% │ "
            f"[cyan]RAM:[/cyan] {ram_used_gb:>5.1f}/{ram_total_gb:.1f} GB │ "
            f"[green]Running:[/green] {running_count}/{total_count} │ "
            f"[blue]Completed:[/blue] {completed_count}/{total_count}"
        )

        return overhead

    def _build_scenario_table(
        self,
        all_stats: List[LiveScenarioStats]
    ) -> Table:
        """
        Build scenario progress table.

        Args:
            all_stats: List of LiveScenarioStats objects

        Returns:
            Rich Table with scenario progress bars
        """
        table = Table(show_header=False, box=None, padding=(0, 1))

        # Add columns
        name_length = 25
        table.add_column("Icon", width=2)
        table.add_column("Scenario", width=name_length)
        table.add_column("Progress", width=20)
        table.add_column("Stats", width=50)

        if not all_stats:
            table.add_row(
                "",
                "",
                "[yellow]No scenarios running...[/yellow]",
                ""
            )
            return table

        # Add scenario rows
        for stats in all_stats:
            # Truncate scenario name
            name = stats.scenario_name
            if len(name) > name_length - 2:
                name = name[:name_length - 5] + "..."

            # Status-based icon and color
            icon, name_color = self._get_status_display(stats.status)

            # Progress bar and text
            progress_text = self._build_progress_text(stats)

            # Stats
            portfolio_value = stats.current_balance
            total_trades = stats.total_trades
            winning = stats.winning_trades
            losing = stats.losing_trades

            trades = ""
            # Only show trades when not in initial states
            if stats.status not in (
                ScenarioStatus.INITIALIZED,
                ScenarioStatus.WARMUP_DATA_TICKS,
                ScenarioStatus.WARMUP_DATA_BARS,
                ScenarioStatus.WARMUP_TRADER,
                ScenarioStatus.INIT_PROCESS
            ):
                trades = f"Trades: {total_trades} ({winning}W / {losing}L)"

            # Format P/L
            pnl = stats.current_balance - stats.initial_balance
            if pnl >= 0:
                pnl_color = "green"
                pnl_sign = "+"
            else:
                pnl_color = "red"
                pnl_sign = ""

            # Dirty flag indicator
            dirty_flag = " 🏴" if stats.portfolio_dirty else ""

            stats_text = (
                f"[{pnl_color}]${portfolio_value:>8,.0f}{dirty_flag}[/{pnl_color}] "
                f"[dim]({pnl_sign}${pnl:>6,.2f})[/dim] \n"
                f"[blue]{trades}[/blue]"
            )

            # Add row
            table.add_row(
                icon,
                f"[{name_color}]{name}[/{name_color}]",
                progress_text,
                stats_text
            )

        return table

    def _get_status_display(self, status: ScenarioStatus) -> tuple[str, str]:
        """
        Get icon and color for a scenario status.

        Args:
            status: ScenarioStatus enum value

        Returns:
            Tuple of (icon, name_color)
        """
        match status:
            case ScenarioStatus.INITIALIZED:
                return "⏸️", "dim"
            case ScenarioStatus.WARMUP_DATA_TICKS:
                return "🔥", "yellow"
            case ScenarioStatus.WARMUP_DATA_BARS:
                return "🔥", "yellow"
            case ScenarioStatus.WARMUP_TRADER:
                return "🔥", "yellow"
            case ScenarioStatus.INIT_PROCESS:
                return "⚙️", "cyan"
            case ScenarioStatus.RUNNING:
                return "🔬", "cyan"
            case ScenarioStatus.COMPLETED:
                return "✅", "green"
            case ScenarioStatus.FINISHED_WITH_ERROR:
                return "❌", "red"
            case _:
                return "❓", "white"

    def _build_progress_text(self, stats: LiveScenarioStats) -> str:
        """
        Build progress bar and text based on scenario status.

        Args:
            stats: LiveScenarioStats object

        Returns:
            Formatted progress string
        """
        # Status-specific messages for non-running states
        match stats.status:
            case ScenarioStatus.INITIALIZED:
                return "[dim]Initialized[/dim]"
            case ScenarioStatus.WARMUP_DATA_TICKS:
                return "[yellow]Loading ticks...[/yellow]"
            case ScenarioStatus.WARMUP_DATA_BARS:
                return "[yellow]Rendering bars...[/yellow]"
            case ScenarioStatus.WARMUP_TRADER:
                return "[yellow]Loading broker...[/yellow]"
            case ScenarioStatus.INIT_PROCESS:
                return "[cyan]Starting...[/cyan]"

        # For RUNNING and COMPLETED: show progress bar
        progress_percent = stats.progress_percent
        bar_width = 20
        filled = int((progress_percent / 100.0) * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        return f"{bar} {progress_percent:>5.1f}%"
