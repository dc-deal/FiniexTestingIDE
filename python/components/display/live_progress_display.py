"""
FiniexTestingIDE - Live Progress Display
Real-time scenario execution progress with resource monitoring

Features:
- Overhead line: System resources (CPU, RAM) + scenario count
- Per-scenario lines: Progress bar, time, portfolio, trades
- Thread-based polling (500ms updates)
- Flicker-free display using rich.live
- Graceful shutdown

Usage:
    # In BatchOrchestrator
    display = LiveProgressDisplay(performance_log, scenarios)
    display.start()
    
    # ... run scenarios ...
    
    display.stop()
"""

import threading
import time
import traceback
import psutil
from typing import List, Optional
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.layout import Layout
from rich import box

from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.types.global_types import TestScenario


class LiveProgressDisplay:
    """
    Live progress display for scenario execution.

    Shows:
    - System resources (CPU, RAM)
    - Number of running/completed scenarios
    - Per-scenario progress bars with stats
    - Real-time updates every 500ms
    """

    def __init__(self,
                 performance_manager: ScenarioSetPerformanceManager,
                 scenarios: List[TestScenario],
                 update_interval: float = 0.3):
        """
        Initialize live progress display.

        Args:
            performance_manager: ScenarioSetPerformanceManager instance
            scenarios: List of scenarios to track
            update_interval: Update interval in seconds (default: 0.5)
        """
        self.performance_manager = performance_manager
        self.scenarios = scenarios
        self.update_interval = update_interval

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Rich console
        self.console = Console()
        self._live: Optional[Live] = None

    def start(self):
        """Start the live display thread."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._thread = threading.Thread(
                target=self._update_loop, daemon=True)
            self._thread.start()

    def stop(self):
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

    def _update_loop(self):
        """Main update loop running in thread."""
        with Live(self._render(), console=self.console, refresh_per_second=2) as live:
            self._live = live

            while self._running:
                try:
                    # Update display
                    live.update(self._render())

                    # Sleep
                    time.sleep(self.update_interval)

                except Exception as e:
                    print(f"\n❌ CRITICAL ERROR in LiveProgressDisplay:")
                    print(traceback.format_exc())

                    # Stop display and re-raise
                    self._running = False
                    raise  # <-- HARD FAIL!

    def _render(self) -> Panel:
        """
        Render the live display.

        Returns:
            Rich Panel with overhead + scenario progress
        """
        # Get all live stats
        all_stats = self.performance_manager.get_all_live_stats()

        # Build overhead line
        overhead = self._build_overhead(all_stats)

        # Build scenario lines
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

    def _build_overhead(self, all_stats: List[dict]) -> str:
        """
        Build overhead resource line.

        Args:
            all_stats: List of scenario stats

        Returns:
            Formatted string with system resources
        """
        # Count scenarios
        running_count = sum(1 for s in all_stats if s['status'] == 'running')
        completed_count = sum(
            1 for s in all_stats if s['status'] == 'completed')
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

    def _build_scenario_table(self, all_stats: List[dict]) -> Table:
        """
        Build scenario progress table.

        Args:
            all_stats: List of scenario stats

        Returns:
            Rich Table with scenario progress bars
        """
        table = Table(show_header=False, box=None, padding=(0, 1))

        # Add columns
        name_length = 20
        table.add_column("Icon", width=2)
        table.add_column("Scenario", width=name_length)
        table.add_column("Progress", width=20)
        table.add_column("Stats", width=40)

        if not all_stats:
            table.add_row(
                "", "", "[yellow]No scenarios running...[/yellow]", "")
            return table

        # Add scenario rows
        for stats in all_stats:
            # Truncate scenario name
            name = stats['scenario_name']
            if len(name) > name_length-2:
                name = name[:name_length-5] + "..."

            # Status icon
            name_color = "white"
            match stats['status']:
                case "completed":
                    icon = "✅"
                    name_color = "green"
                case "warmup":
                    icon = "🔥"
                case _:
                    icon = "🔬"
                    name_color = "cyan"

            # Progress bar
            progress_percent = stats['progress_percent']
            bar_width = 20
            filled = int((progress_percent / 100.0) * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            progress_text = f"{bar} {progress_percent:>5.1f}%"
            if stats['status'] == "warmup":
                progress_text = f"[yellow]Warming up...[/yellow]"

            # Stats
            elapsed = stats['elapsed_time']
            portfolio_value = stats['portfolio_value']

            total_trades = stats["total_trades"]
            winning = stats["winning_trades"]
            losing = stats["losing_trades"]
            trades = ""
            if stats['status'] != "warmup":
                trades = f"Trades: {total_trades} ({winning}W / {losing}L)"

            # Format P/L
            # Assuming 10k start capital
            pnl = stats['portfolio_value'] - stats['initial_balance']
            if pnl >= 0:
                pnl_color = "green"
                pnl_sign = "+"
            else:
                pnl_color = "red"
                pnl_sign = ""

            stats_text = (
                f"[yellow]{elapsed:>5.1f}s[/yellow] │ "
                f"[{pnl_color}]${portfolio_value:>8,.0f}[/{pnl_color}] "
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

    def format_scenario_name(self, name: str, max_length: int = 10) -> str:
        """
        Format scenario name for display (truncate intelligently).

        Args:
            name: Full scenario name
            max_length: Maximum display length

        Returns:
            Truncated name
        """
        if len(name) <= max_length:
            return name

        # Try to keep symbol + meaningful part
        # Example: "EURUSD_window_02" -> "EUR_win02"
        parts = name.split('_')

        if len(parts) >= 2:
            # Keep first part (symbol) + abbreviated rest
            symbol = parts[0][:3]  # First 3 chars of symbol
            rest = ''.join(parts[1:])[:max_length - len(symbol) - 1]
            return f"{symbol}_{rest}"

        # Fallback: Simple truncation
        return name[:max_length - 3] + "..."
