"""
Import Live Display Tests (#175)

The screen renders any frame at a narrow and a wide terminal, shows the memory bar and the
render workers, and names the log file once the import ended. It formats what the frame
carries — a step's remaining time included — and derives nothing itself. No live terminal is
started: the panel is rendered into a recording console.
"""

import io
import queue
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest
from rich.console import Console

import python.system.ui.import_live_display as import_live_display
from python.framework.types.import_types.import_progress_types import (
    BarRenderPhase,
    ImportMessage,
    ImportOutcome,
    ImportProgressFrame,
    ImportSection,
    ImportStepFrame,
    ImportStepId,
    ImportStepStatus,
    RenderWorkerLine,
)
from python.framework.types.log_level import LogLevel
from python.system.ui.import_live_display import build_import_panel
from python.system.ui.ui_render_utils import format_size, progress_bar

GB = 1024 ** 3


def _frame(outcome=None) -> ImportProgressFrame:
    steps = [
        ImportStepFrame(ImportStepId.SCAN, ImportSection.INBOXES, 'Scan import inboxes',
                        ImportStepStatus.DONE, 614, 614, 'files',
                        detail='kraken_spot 312 · mt5 256 · signals 46', elapsed_s=2.0),
        ImportStepFrame(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files',
                        ImportStepStatus.DONE, 568, 568, 'files', elapsed_s=1038.0),
        ImportStepFrame(ImportStepId.BARS_RENDER, ImportSection.TICKS, 'Render bars',
                        ImportStepStatus.RUNNING, 9, 17, 'symbols', elapsed_s=120.0,
                        estimate_s=270.0, remaining_s=150.0, workers=[
                            RenderWorkerLine(1, 'mt5/GBPUSD', BarRenderPhase.LOADING_TICKS,
                                             640, 810, int(3.1 * GB)),
                            RenderWorkerLine(2, 'mt5/EURUSD', BarRenderPhase.WRITING,
                                             5, 8, int(2.4 * GB))]),
        ImportStepFrame(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS,
                        'Import signal files', ImportStepStatus.PENDING, 0, 46, 'files',
                        estimate_s=35.0, remaining_s=35.0),
        ImportStepFrame(ImportStepId.CACHES, ImportSection.SHARED, 'Rebuild discovery caches',
                        ImportStepStatus.SKIPPED, counter_unit='caches',
                        detail='no bars rendered'),
    ]
    return ImportProgressFrame(
        import_id='20261010_090011_a3f9c2d1',
        started_at=datetime(2026, 10, 10, 9, 0, 11, tzinfo=timezone.utc),
        elapsed_s=1082.0, remaining_s=540.0, estimated_total_s=1622.0,
        estimated_finish_at=datetime(2026, 10, 10, 9, 27, 13, tzinfo=timezone.utc), cpu_percent=34.0,
        ram_used_bytes=int(18.4 * GB), ram_total_bytes=int(30.3 * GB),
        tick_files=568, tick_bytes=int(4.1 * GB), signal_files=46, signal_bytes=int(1.2 * 1024 ** 2),
        ticks_written=41_203_880, signal_rows_written=0, warning_count=1, error_count=0,
        last_message=ImportMessage(LogLevel.WARNING, 'DUPLICATE DETECTED in EURUSD_x_ticks.json'),
        steps=steps, log_path='logs/imports/import_20261010_090011_a3f9c2d1.log', outcome=outcome)


def _render(frame: ImportProgressFrame, width: int) -> str:
    console = Console(record=True, width=width, color_system=None)
    console.print(build_import_panel(frame))
    return console.export_text()


def _line_with(text: str, needle: str) -> str:
    return next(line for line in text.splitlines() if needle in line)


@pytest.mark.parametrize('width', [80, 160])
def test_screen_renders_at_narrow_and_wide_terminals(width):
    text = _render(_frame(), width)
    for label in ('Scan import inboxes', 'Render bars', 'Rebuild discovery caches'):
        assert label in text
    assert '#1 mt5/GBPUSD' in text and '#2 mt5/EURUSD' in text
    assert 'RAM' in text and '18.4 / 30.3 GB' in text
    assert 'DUPLICATE DETECTED' in text
    assert 'remaining ~9m' in text
    assert 'total ~27m 2s' in text and 'ends ~09:27 UTC' in text
    assert '1 warning(s)' in text and '0 error(s)' in text
    # Each step's time is the frame's own figure
    assert '~2m 30s' in _line_with(text, 'Render bars')
    assert '~35s' in _line_with(text, 'Import signal files')


def test_a_running_step_shows_the_frames_remaining_time_not_its_own():
    frame = _frame()
    # Estimate minus elapsed would read 2m 30s; the tracker's figure is what the screen shows
    frame.steps[2].remaining_s = 600.0
    assert '~10m' in _line_with(_render(frame, 160), 'Render bars')


def test_finished_import_names_its_outcome_and_log_file():
    text = _render(_frame(outcome=ImportOutcome.COMPLETED), 160)
    assert 'completed' in text
    assert 'Full log: logs/imports/import_20261010_090011_a3f9c2d1.log' in text
    assert 'remaining' not in text and 'ends ~' not in text


@pytest.mark.parametrize('status', list(ImportStepStatus))
def test_every_step_status_renders(status):
    # A status the screen has no icon for would end the display thread with a KeyError,
    # leaving a frozen screen over a muted console
    frame = _frame()
    frame.steps[1].status = status
    assert 'Import tick files' in _render(frame, 120)


@pytest.mark.parametrize('outcome', list(ImportOutcome))
def test_every_outcome_renders(outcome):
    assert 'Full log' in _render(_frame(outcome=outcome), 120)


def test_a_narrow_terminal_keeps_every_status_icon():
    frame = _frame(outcome=ImportOutcome.INTERRUPTED)
    frame.steps[1].status = ImportStepStatus.FAILED
    frame.steps[2].status = ImportStepStatus.INTERRUPTED
    text = _render(frame, 64)
    assert '✅' in _line_with(text, 'Scan import inboxes')
    assert '❌' in _line_with(text, 'Import tick files')
    assert '✋' in _line_with(text, 'Render bars')


def test_a_cut_note_ends_in_an_ellipsis_never_in_a_smaller_number():
    # At 80 columns the scan note does not fit; cut without a mark, '312' read as '31'
    scan = _line_with(_render(_frame(), 80), 'Scan import inboxes')
    assert '…' in scan
    assert 'kraken_spot 31 ' not in scan


def test_progress_bar_clamps_and_fills():
    assert progress_bar(0.6, 10) == '██████░░░░'
    assert progress_bar(-1.0, 4) == '░░░░'
    assert progress_bar(2.0, 4) == '████'


def test_sizes_use_one_mb():
    assert format_size(412 * 1024 ** 2) == '412.0 MB'
    assert format_size(int(4.1 * GB)) == '4.1 GB'


def test_a_frame_that_cannot_be_drawn_is_reported_and_the_screen_goes_on(monkeypatch):
    """
    A screen that fails used to end the display thread without a word; the import's console is
    muted while it runs, so not even the traceback was seen (#586).
    """
    reports: List[str] = []
    monkeypatch.setattr(import_live_display, 'vLog', SimpleNamespace(error=reports.append))
    terminal = Console(file=io.StringIO(), width=120, height=60, force_terminal=True,
                       color_system=None, record=True)
    monkeypatch.setattr(import_live_display, 'Console', lambda: terminal)
    frames: queue.Queue = queue.Queue()
    frames.put(object())

    display = import_live_display.ImportLiveDisplay(frames, update_interval_s=0.01)
    display.start()
    deadline = time.monotonic() + 5.0
    while not reports and time.monotonic() < deadline:
        time.sleep(0.01)
    frames.put(_frame())
    time.sleep(0.1)
    display.stop()

    assert len(reports) == 1
    assert reports[0].startswith('❌ [DISPLAY] The import screen failed — AttributeError')
    # The next frame was drawn: the thread outlived the failure
    assert 'Scan import inboxes' in terminal.export_text()

