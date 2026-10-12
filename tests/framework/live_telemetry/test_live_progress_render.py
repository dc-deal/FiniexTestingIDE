"""
FiniexTestingIDE - Simulation Progress Display Render Tests (#586)

The batch's live progress screen, driven the way a batch drives it — frames on its queue, its
own thread, start and stop — into a recording terminal. A frame it cannot apply is reported
and skipped; before #586 one was swallowed without a word.
"""

import io
import queue
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest
from rich.console import Console
from rich.panel import Panel

import python.system.ui.live_progress_display as live_progress_display
from python.framework.types.decision_logic_types import AwarenessLevel, DecisionAwareness
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.live_types.live_scenario_stats_types import (
    LiveScenarioStats,
    LiveStatusFrame,
    ScenarioStatus,
)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

_START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _scenarios() -> List[SingleScenario]:
    """
    One scenario per status the screen can show.

    Returns:
        The scenarios
    """
    return [SingleScenario(name=f'scenario_{status.name.lower()}', scenario_index=idx,
                           symbol='BTCUSD', data_broker_type='kraken_spot', start_date=_START)
            for idx, status in enumerate(ScenarioStatus)]


def _frame(idx: int, status: ScenarioStatus) -> LiveScenarioStats:
    """
    A scenario's progress frame.

    Args:
        idx: The scenario's index
        status: Its status

    Returns:
        The frame
    """
    return LiveScenarioStats(
        core=LiveCoreSnapshot(symbol='BTCUSD', ticks_processed=500, balance=10010.0,
                              initial_balance=10000.0, total_trades=3, winning_trades=2,
                              losing_trades=1),
        scenario_name=f'scenario_{status.name.lower()}', scenario_index=idx,
        total_ticks=1000, progress_percent=50.0, status=status)


def _show(monkeypatch, frames: list, width: int):
    """
    Run the display on its own thread over some frames, then stop it.

    Args:
        monkeypatch: pytest's monkeypatch
        frames: What the scenarios put on the queue
        width: Terminal width

    Returns:
        (the screen as text, the reports the display wrote)
    """
    reports: List[str] = []
    monkeypatch.setattr(live_progress_display, 'vLog', SimpleNamespace(error=reports.append))
    terminal = Console(file=io.StringIO(), width=width, height=80, force_terminal=True,
                       color_system=None, record=True)
    monkeypatch.setattr(live_progress_display, 'Console', lambda: terminal)
    frame_queue: queue.Queue = queue.Queue()
    for frame in frames:
        frame_queue.put(frame)

    display = live_progress_display.LiveProgressDisplay(_scenarios(), frame_queue,
                                                        update_interval=0.01)
    display.start()
    deadline = time.monotonic() + 5.0
    while not frame_queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    display.stop()
    return terminal.export_text(), reports


@pytest.mark.parametrize('width', [80, 160])
def test_every_status_a_scenario_can_be_in(monkeypatch, width):
    frames = [_frame(idx, status) for idx, status in enumerate(ScenarioStatus)]

    text, reports = _show(monkeypatch, frames, width)

    assert 'Strategy Execution Progress' in text
    assert 'progress display failed' not in text
    assert reports == []


def test_a_status_only_frame_changes_the_status(monkeypatch):
    frames = [LiveStatusFrame(scenario_index=0, scenario_name='scenario_initialized',
                              status=ScenarioStatus.COMPLETED)]

    text, reports = _show(monkeypatch, frames, 160)

    assert '✅' in text
    assert reports == []


def test_a_frame_that_cannot_be_applied_is_reported_and_skipped(monkeypatch):
    frames = [object(), LiveStatusFrame(scenario_index=0, scenario_name='scenario_initialized',
                                        status=ScenarioStatus.COMPLETED)]

    text, reports = _show(monkeypatch, frames, 160)

    assert len(reports) == 1
    assert reports[0].startswith('❌ [DISPLAY] A progress frame failed — AttributeError')
    assert 'The frame is skipped; the batch is unaffected.' in reports[0]
    # The frame after it still arrived
    assert '✅' in text


def test_the_strategy_s_words_and_the_scenario_s_name_are_text(monkeypatch):
    frame = _frame(0, ScenarioStatus.RUNNING)
    frame.core.last_awareness = DecisionAwareness(message='flat [/x] waiting',
                                                  level=AwarenessLevel.NOTICE)

    text, reports = _show(monkeypatch, [frame], 160)

    assert 'flat [/x] waiting' in text
    assert 'progress display failed' not in text
    assert reports == []


def test_a_screen_that_cannot_be_printed_never_reaches_the_display(monkeypatch):
    """
    The live display re-renders the screen it holds whenever any thread prints — the batch's
    own logging, and the report of this very failure. A held unprintable screen would make
    those prints raise; it is proven first and replaced instead.
    """
    printed: List[str] = []

    def printing_sink(message: str) -> None:
        printed.append(message)
        print(message)  # what the global logger does: through the live display's stdout

    monkeypatch.setattr(live_progress_display, 'vLog', SimpleNamespace(error=printing_sink))
    terminal = Console(file=io.StringIO(), width=120, height=60, force_terminal=True,
                       color_system=None, record=True)
    monkeypatch.setattr(live_progress_display, 'Console', lambda: terminal)
    display = live_progress_display.LiveProgressDisplay(_scenarios(), queue.Queue(),
                                                        update_interval=0.01)
    monkeypatch.setattr(display, 'render', lambda: Panel('closed [/x] by venue'))

    display.start()
    time.sleep(0.3)
    print('scenario 3 completed')  # the batch logs while the display runs
    display.stop()

    assert len(printed) == 1
    assert printed[0].startswith('❌ [DISPLAY] The progress screen failed — MarkupError')
    assert 'progress display failed: MarkupError' in terminal.export_text()

