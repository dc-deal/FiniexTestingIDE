"""
Import Progress Tracker Tests (#175)

The tracker turns stored rates into estimates, keeps the last warning, measures what each
finished step achieved, and hands the display only the newest frames.
"""

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from python.data_management.importers import import_progress_tracker
from python.data_management.importers.import_progress_tracker import ImportProgressTracker
from python.framework.types.import_types.import_progress_types import (
    BarRenderPhase,
    BarRenderProgress,
    ImportRateUnit,
    ImportSection,
    ImportStepId,
    ImportStepRate,
    ImportStepStatus,
)
from python.framework.types.log_level import LogLevel


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        # The tracker's own reference only — the time module stays untouched for the rest
        monkeypatch.setattr(import_progress_tracker, 'time',
                            SimpleNamespace(monotonic=lambda: self.now))


def _tracker(rates=None, frame_queue=None) -> ImportProgressTracker:
    return ImportProgressTracker(import_id='20261010T090011Z', log_path='logs/imports/x.log',
                                 rates=rates or {}, frame_queue=frame_queue,
                                 update_interval_s=0.0)


class TestEstimate:
    """A stored rate times this import's quantity is the step's estimate."""

    def test_rate_and_quantity_give_the_estimate(self):
        tracker = _tracker({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.5)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 100.0)

        step = tracker.snapshot().steps[0]
        assert step.estimate_s == 50.0
        assert tracker.snapshot().remaining_s == 50.0

    def test_a_rate_in_another_unit_gives_no_estimate(self):
        tracker = _tracker({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.FILE, 0.5)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 100.0)
        assert tracker.snapshot().steps[0].estimate_s is None

    def test_render_rate_needs_the_same_worker_count(self):
        rate = ImportStepRate(ImportRateUnit.MILLION_TICKS, 2.0, workers=2)
        tracker = _tracker({ImportStepId.BARS_RENDER: rate})
        tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS, 'Render bars')
        tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS, 10.0,
                             workers=4)
        assert tracker.snapshot().steps[0].estimate_s is None

    def test_one_step_without_estimate_leaves_the_total_unknown(self):
        tracker = _tracker({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.5)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.add_step(ImportStepId.TICKS_INDEX, ImportSection.TICKS, 'Rebuild tick index')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 100.0)
        assert tracker.snapshot().remaining_s is None

    def test_a_running_step_past_five_percent_estimates_from_its_own_progress(self, monkeypatch):
        clock = _Clock(monkeypatch)
        tracker = _tracker({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 1.0)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 100.0)
        tracker.start_step(ImportStepId.TICKS_IMPORT, total=100)

        # 4 % done after 20 s: still the rate decides — 100 s estimated, 80 s left
        clock.now += 20.0
        tracker.advance(ImportStepId.TICKS_IMPORT, 4)
        assert tracker.snapshot().remaining_s == 80.0

        # 10 % after 20 s: its own progress decides — 20 s per 10 %, 180 s left
        tracker.advance(ImportStepId.TICKS_IMPORT, 10)
        frame = tracker.snapshot()
        assert frame.remaining_s == pytest.approx(180.0)
        # The step line carries the same figure the header sums
        assert frame.steps[0].remaining_s == pytest.approx(180.0)

    def test_a_pending_step_carries_its_estimate_and_a_finished_one_nothing(self):
        tracker = _tracker({ImportStepId.SIGNALS_IMPORT: ImportStepRate(ImportRateUnit.MB, 1.0)})
        tracker.add_step(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS, 'Import signal files')
        tracker.set_quantity(ImportStepId.SIGNALS_IMPORT, ImportRateUnit.MB, 3.0)
        assert tracker.snapshot().steps[0].remaining_s == 3.0
        tracker.start_step(ImportStepId.SIGNALS_IMPORT)
        tracker.finish_step(ImportStepId.SIGNALS_IMPORT)
        assert tracker.snapshot().steps[0].remaining_s is None


class TestTotalAndComparison:
    """The whole import's estimate, and how each step fared against its estimate."""

    def test_total_and_finish_time_are_carried_while_running(self):
        tracker = _tracker({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.5)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 100.0)
        frame = tracker.snapshot()
        assert frame.estimated_total_s is not None and frame.estimated_total_s >= 50.0
        assert frame.estimated_finish_at is not None and frame.estimated_finish_at.tzinfo is not None

    def test_comparison_holds_the_estimate_at_the_start_against_the_duration(self):
        tracker = _tracker({ImportStepId.SIGNALS_IMPORT: ImportStepRate(ImportRateUnit.MB, 1.0)})
        tracker.add_step(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS, 'Import signal files')
        tracker.add_step(ImportStepId.SIGNALS_INDEX, ImportSection.SIGNALS, 'Rebuild signal index')
        tracker.set_quantity(ImportStepId.SIGNALS_IMPORT, ImportRateUnit.MB, 2.0)
        tracker.mark_plan_complete()
        tracker.start_step(ImportStepId.SIGNALS_IMPORT)
        tracker.finish_step(ImportStepId.SIGNALS_IMPORT)

        timings, total = tracker.estimate_comparison()
        by_label = {timing.label: timing for timing in timings}
        assert by_label['Import signal files'].estimate_s == 2.0
        assert by_label['Import signal files'].actual_s is not None
        assert by_label['Import signal files'].deviation_percent is not None
        assert by_label['Rebuild signal index'].estimate_s is None
        # The total sums only what compares, and says so
        assert total.label == 'Total (1 of 2 steps)'
        assert total.estimate_s == 2.0

    def test_skipped_steps_stay_out_of_the_total(self, monkeypatch):
        clock = _Clock(monkeypatch)
        tracker = _tracker({
            ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 1.0),
            ImportStepId.BARS_RENDER: ImportStepRate(ImportRateUnit.MILLION_TICKS, 1.0,
                                                     workers=2)})
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS, 'Render bars')
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB, 600.0)
        tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS, 600.0,
                             workers=2)
        tracker.mark_plan_complete()
        tracker.start_step(ImportStepId.TICKS_IMPORT)
        clock.now += 600.0
        tracker.finish_step(ImportStepId.TICKS_IMPORT)
        tracker.skip_step(ImportStepId.BARS_RENDER, 'no file imported')

        _, total = tracker.estimate_comparison()
        # The step that ran matched its estimate; the skipped render is no estimate error
        assert (total.estimate_s, total.actual_s, total.deviation_percent) == (600.0, 600.0, 0.0)
        assert total.label == 'Total (1 of 2 steps)'


class TestMeasuredRates:
    """Only steps that finished with a known quantity yield a rate."""

    def test_finished_steps_yield_rates(self):
        tracker = _tracker()
        tracker.add_step(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS, 'Import signal files')
        tracker.add_step(ImportStepId.SIGNALS_INDEX, ImportSection.SIGNALS, 'Rebuild signal index')
        tracker.set_quantity(ImportStepId.SIGNALS_IMPORT, ImportRateUnit.MB, 2.0)
        tracker.set_quantity(ImportStepId.SIGNALS_INDEX, ImportRateUnit.FILE, 10)
        tracker.start_step(ImportStepId.SIGNALS_IMPORT)
        tracker.finish_step(ImportStepId.SIGNALS_IMPORT)
        tracker.start_step(ImportStepId.SIGNALS_INDEX)
        tracker.finish_step(ImportStepId.SIGNALS_INDEX, ImportStepStatus.INTERRUPTED)

        rates = tracker.measured_rates()
        assert set(rates) == {ImportStepId.SIGNALS_IMPORT}
        assert rates[ImportStepId.SIGNALS_IMPORT].unit == ImportRateUnit.MB

    def test_open_steps_close_as_interrupted_and_skipped(self):
        tracker = _tracker()
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.add_step(ImportStepId.TICKS_INDEX, ImportSection.TICKS, 'Rebuild tick index')
        tracker.start_step(ImportStepId.TICKS_IMPORT)
        tracker.finish_open_steps(ImportStepStatus.INTERRUPTED)
        statuses = [step.status for step in tracker.snapshot().steps]
        assert statuses == [ImportStepStatus.INTERRUPTED, ImportStepStatus.SKIPPED]


class TestMessagesAndWorkers:
    """The last warning is pinned; workers appear under the render step."""

    def test_the_newest_counted_issue_is_pinned_by_its_first_line(self):
        tracker = _tracker()
        assert tracker.snapshot().last_message is None
        tracker.note_issue(LogLevel.WARNING, '\u26a0\ufe0f DUPLICATE DETECTED in X\nreport body')
        message = tracker.snapshot().last_message
        assert message.level == LogLevel.WARNING
        # The variation selector and the leading sign go: the screen draws its own sign
        assert message.text == 'DUPLICATE DETECTED in X'

    def test_worker_lines_follow_events_and_leave_with_their_symbol(self):
        tracker = _tracker()
        tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS, 'Render bars')
        tracker.on_worker_progress(BarRenderProgress(
            'AAAUSD', 'kraken_spot', 11, BarRenderPhase.LOADING_TICKS, 3, 10, 2048))
        tracker.on_worker_progress(BarRenderProgress(
            'BBBUSD', 'kraken_spot', 12, BarRenderPhase.WRITING, 5, 8, 4096))
        workers = tracker.snapshot().steps[0].workers
        assert [(w.worker_no, w.item) for w in workers] == [
            (1, 'kraken_spot/AAAUSD'), (2, 'kraken_spot/BBBUSD')]

        tracker.on_symbol_done(1, 2, 'kraken_spot/AAAUSD')
        step = tracker.snapshot().steps[0]
        assert [w.item for w in step.workers] == ['kraken_spot/BBBUSD']
        assert (step.done, step.total) == (1, 2)


    def test_a_worker_keeps_its_number_across_symbols(self):
        tracker = _tracker()
        tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS, 'Render bars')
        tracker.on_worker_progress(BarRenderProgress(
            'AAAUSD', 'mt5', 11, BarRenderPhase.RENDERING, 1, 8, 1))
        tracker.on_worker_progress(BarRenderProgress(
            'BBBUSD', 'mt5', 12, BarRenderPhase.RENDERING, 1, 8, 1))
        tracker.on_symbol_done(1, 3, 'mt5/AAAUSD')
        # The first worker takes its next symbol: still #1, never a second #2
        tracker.on_worker_progress(BarRenderProgress(
            'CCCUSD', 'mt5', 11, BarRenderPhase.LOADING_TICKS, 1, 9, 1))
        numbers = {w.item: w.worker_no for w in tracker.snapshot().steps[0].workers}
        assert numbers == {'mt5/BBBUSD': 2, 'mt5/CCCUSD': 1}


class TestPublish:
    """The display gets the newest frames; a full queue drops the oldest."""

    def test_full_queue_keeps_the_newest_frame(self):
        frames: queue.Queue = queue.Queue(maxsize=2)
        tracker = _tracker(frame_queue=frames)
        tracker.add_step(ImportStepId.SCAN, ImportSection.INBOXES, 'Scan import inboxes',
                         counter_unit='files')
        for done in range(5):
            tracker.advance(ImportStepId.SCAN, done, 4)
        newest = None
        while not frames.empty():
            newest = frames.get_nowait()
        assert newest.steps[0].done == 4

    def test_a_frame_shares_nothing_with_the_tracker(self):
        tracker = _tracker()
        tracker.add_step(ImportStepId.SCAN, ImportSection.INBOXES, 'Scan import inboxes',
                         counter_unit='files')
        frame = tracker.snapshot()
        tracker.advance(ImportStepId.SCAN, 3, 3)
        assert frame.steps[0].done == 0


class TestRefresh:
    """Frames keep coming while the import's thread is inside one long call."""

    def _refreshing_tracker(self, frames: queue.Queue) -> ImportProgressTracker:
        tracker = ImportProgressTracker(import_id='x', log_path='l', rates={},
                                        frame_queue=frames, update_interval_s=0.05)
        tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS, 'Import tick files')
        tracker.start_step(ImportStepId.TICKS_IMPORT, total=10)
        return tracker

    def test_the_clock_moves_while_nothing_reports(self):
        frames: queue.Queue = queue.Queue(maxsize=8)
        tracker = self._refreshing_tracker(frames)
        # A file starts, and at once the next one: the second report falls inside the
        # throttle and is dropped — only the refresh ever shows it
        tracker.advance(ImportStepId.TICKS_IMPORT, 0, current_item='first.json')
        tracker.advance(ImportStepId.TICKS_IMPORT, 1, current_item='second.json')
        tracker.start_refresh()
        seen = []
        try:
            # The import's thread is now "inside one long call" and reports nothing
            deadline = time.monotonic() + 5.0
            while len(seen) < 4 and time.monotonic() < deadline:
                try:
                    seen.append(frames.get(timeout=0.5))
                except queue.Empty:
                    pass
        finally:
            tracker.stop_refresh()

        assert len(seen) >= 4
        assert seen[-1].elapsed_s > seen[1].elapsed_s
        assert seen[-1].steps[0].elapsed_s > seen[1].steps[0].elapsed_s
        assert seen[-1].steps[0].current_item == 'second.json'

    def test_a_stopped_refresh_publishes_nothing_more(self):
        frames: queue.Queue = queue.Queue(maxsize=8)
        tracker = self._refreshing_tracker(frames)
        tracker.start_refresh()
        time.sleep(0.2)
        tracker.stop_refresh()
        while not frames.empty():
            frames.get_nowait()
        time.sleep(0.2)
        assert frames.empty()

    def test_no_queue_no_refresh(self):
        tracker = _tracker()
        tracker.start_refresh()
        # Without a queue nobody would see the frames, so no thread starts
        assert 'Import-Refresh' not in {thread.name for thread in threading.enumerate()}
        tracker.stop_refresh()
