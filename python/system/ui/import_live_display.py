"""
FiniexTestingIDE - Import Live Display
The live screen of a data import (#175): every step with its count, the render workers,
machine memory as a bar, the estimate, and the last warning.

Fed by ImportProgressFrame snapshots through a queue — the latest frame wins. The display
renders and computes nothing: every figure, the estimate included, arrives in the frame, so
another surface can show the same frame without re-deriving it.
"""

import queue
import threading
from typing import List, Optional

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportProgressFrame,
    ImportSection,
    ImportStepFrame,
    ImportStepStatus,
)
from python.framework.types.log_level import LogLevel
from python.framework.utils.time_utils import format_duration
from python.system.ui.render_failure_reporter import RenderFailureReporter
from python.system.ui.ui_render_utils import (
    BYTES_PER_GB,
    format_size,
    progress_bar,
    prove_printable,
)

vLog = get_global_logger()

_STATUS_ICON = {
    ImportStepStatus.PENDING: '⬜',
    ImportStepStatus.RUNNING: '⏳',
    ImportStepStatus.DONE: '✅',
    ImportStepStatus.SKIPPED: '➖',
    ImportStepStatus.FAILED: '❌',
    ImportStepStatus.INTERRUPTED: '✋',
}
_OUTCOME_STYLE = {
    ImportOutcome.COMPLETED: ('completed', 'bold green'),
    ImportOutcome.COMPLETED_WITH_ERRORS: ('completed with errors', 'bold yellow'),
    ImportOutcome.INTERRUPTED: ('interrupted', 'bold yellow'),
    ImportOutcome.FAILED: ('failed', 'bold red'),
}
_STEP_BAR_WIDTH = 20
_LABEL_WIDTH = 26
_RAM_BAR_WIDTH = 24
# Memory share above which the bar turns yellow, and red.
_RAM_WARN_SHARE = 0.70
_RAM_ALERT_SHARE = 0.85
# The warning sign WITHOUT the emoji variation selector U+FE0F: with it, a terminal draws the
# sign two cells wide while the layout counts one, which shifts the panel's right border.
_WARNING_SIGN = '\u26a0'


class ImportLiveDisplay:
    """
    Live screen of a running import.

    Args:
        frame_queue: Where the import publishes its frames
        update_interval_s: Seconds between two redraws
    """

    def __init__(self, frame_queue: queue.Queue, update_interval_s: float = 0.25):
        self._queue = frame_queue
        self._update_interval_s = update_interval_s
        self._frame: Optional[ImportProgressFrame] = None
        self._lock = threading.Lock()
        # Set by stop(): the display thread waits on it between redraws, so a stop wakes it
        # at once instead of after a full update interval
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._console = Console()
        # The console is muted while the import runs, so a report reaches the log files
        self._failure_reporter = RenderFailureReporter(vLog.error)

    def start(self) -> None:
        """
        Start the display thread.

        Returns:
            None
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._update_loop, name='Import-Display', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Stop the display thread. The final drain and render happen inside that thread, so
        the last frame stays on screen and no live.update() crosses threads.

        Returns:
            None
        """
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _update_loop(self) -> None:
        """
        Redraw from the newest frame until stopped.

        Nothing leaves this thread unreported (#586): a screen that cannot be drawn is
        replaced by a failure screen, and only a failure of the live display itself ends the
        loop. The import never waits on this thread.

        Returns:
            None
        """
        try:
            # No first screen here: Live prints it on entry, before any guard could catch a
            # screen that fails to print — the loop's first _show() puts it up instead
            with Live(console=self._console, auto_refresh=False) as live:
                while not self._stopped.is_set():
                    self._drain()
                    self._show(live)
                    self._stopped.wait(self._update_interval_s)
                self._drain()
                self._show(live)
        except Exception as error:
            self._failure_reporter.report(
                'The import screen', error, 'The screen has stopped; the import is unaffected.')

    def _show(self, live: Live) -> None:
        """
        Draw and print one screen; one that cannot be printed is replaced by a failure screen
        and tried again on the next cycle (#586).

        Args:
            live: The live display that owns the console

        Returns:
            None
        """
        screen = self._screen()
        try:
            # Proven before the live display holds it: a held screen is re-rendered whenever
            # any thread prints
            prove_printable(screen, self._console.size.width, self._console.size.height)
        except Exception as error:
            screen = self._failure_screen(error)
            self._failure_reporter.report(
                'The import screen', error,
                'A failure screen replaces it until it draws again; the import is unaffected.')
        live.update(screen, refresh=True)

    def _screen(self) -> RenderableType:
        """
        The screen for this moment; one that cannot be built is a failure screen.

        Returns:
            A renderable
        """
        try:
            return self._render()
        except Exception as error:
            self._failure_reporter.report(
                'The import screen', error,
                'A failure screen replaces it until it draws again; the import is unaffected.')
            return self._failure_screen(error)

    @staticmethod
    def _failure_screen(error: Exception) -> Panel:
        """
        What stands on screen while the import cannot be drawn.

        Args:
            error: The failure

        Returns:
            A plain panel naming it
        """
        return Panel(
            Text(f'{_WARNING_SIGN} import screen failed: {type(error).__name__} — '
                 f'see the log files', style='yellow'),
            box=box.ROUNDED, border_style='yellow')

    def _drain(self) -> None:
        """
        Take every queued frame and keep the newest.

        Returns:
            None
        """
        while True:
            try:
                frame = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._frame = frame

    def _render(self) -> RenderableType:
        """
        The screen for the newest frame.

        Returns:
            A renderable
        """
        with self._lock:
            frame = self._frame
        if frame is None:
            return Panel(Text('📥 Import starting…'), box=box.ROUNDED, border_style='cyan')
        return build_import_panel(frame)


def build_import_panel(frame: ImportProgressFrame) -> Panel:
    """
    Render one frame as the import screen. Pure: the same frame always gives the same screen.

    Args:
        frame: The import's state

    Returns:
        The screen as a panel
    """
    parts: List[RenderableType] = [_header(frame), Rule(style='dim')]
    current_section: Optional[ImportSection] = None
    for step in frame.steps:
        if step.section != current_section and step.section != ImportSection.INBOXES:
            parts.append(Rule(f'[dim]{step.section.value}[/dim]', style='dim', align='left'))
        current_section = step.section
        parts.append(_step_table(step))
    parts.append(Rule(style='dim'))
    parts.append(_footer(frame))

    title = f'📥 Import {frame.import_id}'
    if frame.outcome is not None:
        label, style = _OUTCOME_STYLE[frame.outcome]
        title += f' — [{style}]{label}[/{style}]'
    return Panel(Group(*parts), title=title, title_align='left',
                 box=box.ROUNDED, border_style='cyan')


def _header(frame: ImportProgressFrame) -> Text:
    """
    The top lines: inbox totals with what was written, the time with its estimate, CPU and
    memory.

    Args:
        frame: The import's state

    Returns:
        The header text
    """
    text = Text()
    text.append(f'ticks    {frame.tick_files} files · {format_size(frame.tick_bytes)}'
                f' · {frame.ticks_written:,} ticks written\n')
    text.append(f'signals  {frame.signal_files} files · {format_size(frame.signal_bytes)}'
                f' · {frame.signal_rows_written:,} rows written\n')
    text.append(f'elapsed {format_duration(frame.elapsed_s)}')
    if frame.outcome is None:
        if frame.remaining_s is None:
            text.append(' · remaining —', style='dim')
        else:
            text.append(f' · remaining ~{format_duration(frame.remaining_s)}')
            if frame.estimated_total_s is not None and frame.estimated_finish_at is not None:
                text.append(f' · total ~{format_duration(frame.estimated_total_s)}'
                            f' · ends ~{frame.estimated_finish_at:%H:%M} UTC')
            text.append(' (estimate)')
    text.append('\n')

    share = frame.ram_used_bytes / frame.ram_total_bytes if frame.ram_total_bytes else 0.0
    ram_style = ('red' if share >= _RAM_ALERT_SHARE
                 else 'yellow' if share >= _RAM_WARN_SHARE else 'green')
    text.append(f'CPU {frame.cpu_percent:3.0f} %   RAM ')
    text.append(progress_bar(share, _RAM_BAR_WIDTH), style=ram_style)
    text.append(f' {frame.ram_used_bytes / BYTES_PER_GB:.1f} / '
                f'{frame.ram_total_bytes / BYTES_PER_GB:.1f} GB  {share * 100:.0f} %')
    return text


def _step_table(step: ImportStepFrame) -> Table:
    """
    One step as a table of its line, its current item and its workers.

    Args:
        step: The step

    Returns:
        A table
    """
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    # Fixed widths except the progress column: every step is a table of its own, and only
    # fixed widths keep their columns aligned. The progress column takes the rest and is
    # cropped at its right end, which is where the bar ends, not where the percentage is.
    # Text in it (a note, a file name) ends in '…' instead, so a cut number is never shown
    # as a smaller one. The status icon opens the label cell rather than a column of its
    # own: a terminal too narrow for the layout shrinks every column alike, and an icon
    # column shrunk below two cells drew every status as the same '…'.
    table.add_column(width=_LABEL_WIDTH + 4, no_wrap=True, overflow='ellipsis')
    table.add_column(width=15, no_wrap=True, justify='right')
    table.add_column(ratio=1, no_wrap=True, overflow='crop')
    table.add_column(width=9, no_wrap=True, justify='right')

    style = ('dim' if step.status in (ImportStepStatus.PENDING, ImportStepStatus.SKIPPED)
             else 'bold' if step.status == ImportStepStatus.RUNNING else '')
    label = Text(f'{_STATUS_ICON[step.status]}  ')
    label.append(step.label, style=style)
    table.add_row(label, _counter(step), _progress_or_detail(step), _time_cell(step))

    if step.status == ImportStepStatus.RUNNING and step.current_item:
        # In the wide column: a file name is longer than the label column holds
        table.add_row(Text('      →', style='dim'), '',
                      Text(step.current_item, style='dim', overflow='ellipsis'), '')
    for worker in step.workers:
        # 'loading_ticks' → 'loading': the short word keeps the counter inside a narrow column
        phase = worker.phase.value.split('_')[0]
        table.add_row(
            # '#1', not 'worker 1': 'kraken_spot/DASHUSD' must still fit the label column
            Text(f'      #{worker.worker_no} {worker.item}', style='dim'),
            Text(f'{phase} {worker.done}/{worker.total}', style='dim'), '',
            Text(f'{worker.rss_bytes / BYTES_PER_GB:.1f} GB', style='dim'))
    return table


def _counter(step: ImportStepFrame) -> str:
    """
    A step's counter, e.g. '401/568 files'.

    Args:
        step: The step

    Returns:
        The counter text, empty for a step without units
    """
    if not step.counter_unit or step.status == ImportStepStatus.SKIPPED:
        return ''
    if step.total is None:
        return f'{step.done} {step.counter_unit}' if step.done else ''
    return f'{step.done}/{step.total} {step.counter_unit}'


def _progress_or_detail(step: ImportStepFrame) -> Text:
    """
    A running or finished step with a total shows its bar; otherwise its fixed note.

    Args:
        step: The step

    Returns:
        The cell text
    """
    if step.total and step.status in (ImportStepStatus.RUNNING, ImportStepStatus.DONE) \
            and not step.detail:
        share = step.done / step.total
        return Text(f'{share * 100:3.0f} % {progress_bar(share, _STEP_BAR_WIDTH)}')
    return Text(step.detail, style='dim', overflow='ellipsis')


def _time_cell(step: ImportStepFrame) -> Text:
    """
    A step's time: how long it took, how long it may still take (the frame's own figure), or
    its estimate.

    Args:
        step: The step

    Returns:
        The cell text
    """
    if step.status in (ImportStepStatus.DONE, ImportStepStatus.FAILED,
                       ImportStepStatus.INTERRUPTED) and step.elapsed_s is not None:
        return Text(format_duration(step.elapsed_s))
    if step.status == ImportStepStatus.RUNNING:
        if step.remaining_s is not None:
            return Text(f'~{format_duration(step.remaining_s)}')
        return Text(format_duration(step.elapsed_s or 0.0), style='dim')
    if step.status == ImportStepStatus.PENDING and step.remaining_s is not None:
        return Text(f'~{format_duration(step.remaining_s)}', style='dim')
    return Text('')


def _footer(frame: ImportProgressFrame) -> Text:
    """
    The bottom line: what was counted, the newest counted warning or error, and the log file
    once the import ended.

    Args:
        frame: The import's state

    Returns:
        The footer text
    """
    # One line each, cut with '…': the pinned issue is a pointer — the summary and the log
    # carry it in full — and a wrapping footer would change the panel's height under the user
    text = Text(no_wrap=True, overflow='ellipsis')
    text.append(f'{_WARNING_SIGN} {frame.warning_count} warning(s)   ',
                style='yellow' if frame.warning_count else 'dim')
    text.append(f'❌ {frame.error_count} error(s)', style='red' if frame.error_count else 'dim')
    if frame.last_message is not None:
        is_error = frame.last_message.level == LogLevel.ERROR
        text.append('   · last: ', style='red' if is_error else 'yellow')
        text.append(frame.last_message.text)
    if frame.outcome is not None:
        text.append(f'\n📝 Full log: {frame.log_path}')
    return text
