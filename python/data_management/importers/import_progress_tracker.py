"""
FiniexTestingIDE - Import Progress Tracker
Holds the state of a running import and publishes it as frames (#175).

Written to by the import's main thread; a refresh thread publishes a frame from the same state
every update interval, so the two take one lock. The display reads frames from the queue and
never shares a mutable object with either. Every figure the screen shows — CPU, memory, the
estimate — is computed here and travels in the frame, so a display (or a later transport) only
renders.
"""

import copy
import functools
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import psutil

from python.framework.types.import_types.import_progress_types import (
    BarRenderProgress,
    ImportMessage,
    ImportOutcome,
    ImportProgressFrame,
    ImportRateUnit,
    ImportSection,
    ImportStepFrame,
    ImportStepId,
    ImportStepRate,
    ImportStepStatus,
    ImportStepTiming,
    RenderWorkerLine,
)
from python.framework.types.log_level import LogLevel

# Below this share of its work a step's own progress says too little; its rate decides.
_PROGRESS_SHARE_FOR_ESTIMATE = 0.05
_FINISHED = (ImportStepStatus.DONE, ImportStepStatus.SKIPPED,
             ImportStepStatus.FAILED, ImportStepStatus.INTERRUPTED)
# The refresh never runs faster than this, whatever the configured interval.
_MIN_REFRESH_S = 0.05


def _locked(method: Callable) -> Callable:
    """
    Run a tracker method under the tracker's lock: the refresh thread builds frames from the
    state the import's thread changes.

    Args:
        method: The method

    Returns:
        The method, taking the lock for its duration
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class ImportProgressTracker:
    """
    The state of one import, and the frames built from it.

    Args:
        import_id: The import's identity
        log_path: The import's own log file
        rates: Measured rates of earlier imports, by step
        frame_queue: Where frames go for the display, None when nothing displays them
        update_interval_s: Minimum time between two published frames
    """

    def __init__(
        self,
        import_id: str,
        log_path: str,
        rates: Dict[ImportStepId, ImportStepRate],
        frame_queue: Optional[queue.Queue] = None,
        update_interval_s: float = 0.25
    ):
        self._import_id = import_id
        self._log_path = log_path
        self._rates = rates
        self._queue = frame_queue
        self._update_interval_s = update_interval_s

        self._started_at = datetime.now(timezone.utc)
        self._started_mono = time.monotonic()
        self._last_published = 0.0
        # Reentrant: a method that changes state publishes, and publishing takes it again
        self._lock = threading.RLock()
        self._refresher: Optional[threading.Thread] = None
        self._refresh_stopped = threading.Event()

        self._steps: Dict[ImportStepId, ImportStepFrame] = {}
        self._step_started: Dict[ImportStepId, float] = {}
        # The quantity each step scales with, in its rate's unit (MB, files, million ticks …)
        self._quantities: Dict[ImportStepId, float] = {}
        self._units: Dict[ImportStepId, ImportRateUnit] = {}
        self._render_workers: Optional[int] = None
        self._workers: Dict[int, RenderWorkerLine] = {}
        # A worker's number, fixed by the process id the first time it reports: its line leaves
        # with each finished symbol, so numbering by the visible lines gave two workers one number
        self._worker_numbers: Dict[int, int] = {}

        self._tick_files = 0
        self._tick_bytes = 0
        self._signal_files = 0
        self._signal_bytes = 0
        self._ticks_written = 0
        self._signal_rows_written = 0
        self._warning_count = 0
        self._error_count = 0
        self._last_message: Optional[ImportMessage] = None
        self._outcome: Optional[ImportOutcome] = None
        # The estimates as they stood once the plan was complete — what the summary checks
        self._plan_estimates: Dict[ImportStepId, Optional[float]] = {}

        # The first cpu_percent() call only starts the measurement and returns 0.0.
        psutil.cpu_percent(interval=None)

    # =========================================================================
    # STEPS
    # =========================================================================

    @_locked
    def add_step(
        self,
        step_id: ImportStepId,
        section: ImportSection,
        label: str,
        counter_unit: str = '',
        total: Optional[int] = None,
        detail: str = ''
    ) -> None:
        """
        Add a step to the plan, pending.

        Args:
            step_id: Which step
            section: The part of the import it belongs to
            label: The line the screen prints
            counter_unit: What its counter counts
            total: Units it has, None while unknown
            detail: A fixed note beside the counter

        Returns:
            None
        """
        self._steps[step_id] = ImportStepFrame(
            step_id=step_id, section=section, label=label, counter_unit=counter_unit,
            total=total, detail=detail)

    @_locked
    def has_step(self, step_id: ImportStepId) -> bool:
        """
        Whether the plan contains a step.

        Args:
            step_id: Which step

        Returns:
            True when it was added
        """
        return step_id in self._steps

    @_locked
    def is_pending(self, step_id: ImportStepId) -> bool:
        """
        Whether a step is planned and has not started.

        Args:
            step_id: Which step

        Returns:
            True when it waits to run
        """
        step = self._steps.get(step_id)
        return step is not None and step.status == ImportStepStatus.PENDING

    @_locked
    def step_ids(self, section: ImportSection) -> List[ImportStepId]:
        """
        The planned steps of one section, in order.

        Args:
            section: The section

        Returns:
            Its step ids
        """
        return [step_id for step_id, step in self._steps.items() if step.section == section]

    @_locked
    def set_total(self, step_id: ImportStepId, total: Optional[int]) -> None:
        """
        Set how many units a step has, once the scan knows.

        Args:
            step_id: Which step
            total: Units it has, None while unknown

        Returns:
            None
        """
        self._steps[step_id].total = total

    @_locked
    def set_detail(self, step_id: ImportStepId, detail: str) -> None:
        """
        Set the fixed note beside a step's counter.

        Args:
            step_id: Which step
            detail: The note

        Returns:
            None
        """
        self._steps[step_id].detail = detail

    @_locked
    def skip_step(self, step_id: ImportStepId, reason: str) -> None:
        """
        Mark a planned step skipped, with the reason beside it.

        Args:
            step_id: Which step
            reason: Why it does not run

        Returns:
            None
        """
        step = self._steps[step_id]
        step.status = ImportStepStatus.SKIPPED
        step.detail = reason
        step.estimate_s = None
        self.publish(force=True)

    @_locked
    def set_quantity(self, step_id: ImportStepId, rate_unit: ImportRateUnit, quantity: float,
                     workers: Optional[int] = None) -> None:
        """
        Declare what a step scales with, which turns a stored rate into its estimate.

        Args:
            step_id: Which step
            rate_unit: The unit of its rate
            quantity: How many of them this import has
            workers: Worker count, for the render step only

        Returns:
            None
        """
        self._units[step_id] = rate_unit
        self._quantities[step_id] = quantity
        if workers is not None:
            self._render_workers = workers
        step = self._steps.get(step_id)
        if step is not None:
            step.estimate_s = self._estimate(step_id)

    @_locked
    def start_step(self, step_id: ImportStepId, total: Optional[int] = None) -> None:
        """
        Mark a step running.

        Args:
            step_id: Which step
            total: Units it has, when only known now

        Returns:
            None
        """
        step = self._steps[step_id]
        step.status = ImportStepStatus.RUNNING
        step.elapsed_s = 0.0
        if total is not None:
            step.total = total
        self._step_started[step_id] = time.monotonic()
        self.publish(force=True)

    @_locked
    def advance(self, step_id: ImportStepId, done: int, total: Optional[int] = None,
                current_item: Optional[str] = None) -> None:
        """
        Record a running step's progress.

        Args:
            step_id: Which step
            done: Units finished
            total: Units it has, when it changed
            current_item: What it works on now, None to keep the previous one

        Returns:
            None
        """
        step = self._steps[step_id]
        step.done = done
        if total is not None:
            step.total = total
        if current_item is not None:
            step.current_item = current_item
        self.publish()

    @_locked
    def finish_step(self, step_id: ImportStepId,
                    status: ImportStepStatus = ImportStepStatus.DONE) -> None:
        """
        Mark a step finished.

        Args:
            step_id: Which step
            status: How it finished

        Returns:
            None
        """
        step = self._steps[step_id]
        step.status = status
        step.current_item = ''
        if step_id in self._step_started:
            step.elapsed_s = time.monotonic() - self._step_started[step_id]
        if step_id == ImportStepId.BARS_RENDER:
            self._workers.clear()
            step.workers = []
        self.publish(force=True)

    @_locked
    def mark_plan_complete(self) -> None:
        """
        Keep every planned step's estimate as it stands now that the scan has run — the
        figures the summary later holds against what each step took.

        Returns:
            None
        """
        self._plan_estimates = {
            step_id: step.estimate_s for step_id, step in self._steps.items()
            if step.status == ImportStepStatus.PENDING}

    @_locked
    def estimate_comparison(self) -> Tuple[List[ImportStepTiming], Optional[ImportStepTiming]]:
        """
        Each planned step's estimate at the start against what it took, and both summed over
        the steps that can be compared: those that finished and had an estimate. A step that
        was skipped, stopped or had no estimate stays out of both sums, so the total compares
        like with like — and its label says so when that is not every planned step.

        Returns:
            (one timing per planned step, the total) — the total None when no step compares
        """
        timings: List[ImportStepTiming] = []
        compared = 0
        estimate_total = 0.0
        actual_total = 0.0
        for step_id, estimate in self._plan_estimates.items():
            step = self._steps[step_id]
            actual = step.elapsed_s if step.status == ImportStepStatus.DONE else None
            timings.append(ImportStepTiming(
                label=step.label, estimate_s=estimate, actual_s=actual,
                deviation_percent=_deviation(estimate, actual)))
            if estimate is not None and actual is not None:
                compared += 1
                estimate_total += estimate
                actual_total += actual
        if not compared:
            return timings, None
        label = ('Total' if compared == len(timings)
                 else f'Total ({compared} of {len(timings)} steps)')
        return timings, ImportStepTiming(
            label=label, estimate_s=estimate_total, actual_s=actual_total,
            deviation_percent=_deviation(estimate_total, actual_total))

    @_locked
    def finish_open_steps(self, status: ImportStepStatus) -> None:
        """
        Close every step that has not finished: the running one as given, the pending ones
        as skipped. Used when an import stops before its plan is through.

        Args:
            status: The status for the step that was running

        Returns:
            None
        """
        for step_id, step in self._steps.items():
            if step.status == ImportStepStatus.RUNNING:
                self.finish_step(step_id, status)
            elif step.status == ImportStepStatus.PENDING:
                step.status = ImportStepStatus.SKIPPED

    # =========================================================================
    # RENDER WORKERS
    # =========================================================================

    @_locked
    def on_worker_progress(self, event: BarRenderProgress) -> None:
        """
        Show one worker's progress under the render step.

        Args:
            event: The worker's event

        Returns:
            None
        """
        line = self._workers.get(event.worker_pid)
        item = f'{event.broker_type}/{event.symbol}'
        if line is None:
            worker_no = self._worker_numbers.setdefault(
                event.worker_pid, len(self._worker_numbers) + 1)
            line = RenderWorkerLine(
                worker_no=worker_no, item=item, phase=event.phase,
                done=event.done, total=event.total, rss_bytes=event.rss_bytes)
            self._workers[event.worker_pid] = line
        else:
            line.item = item
            line.phase = event.phase
            line.done = event.done
            line.total = event.total
            line.rss_bytes = event.rss_bytes
        self._steps[ImportStepId.BARS_RENDER].workers = sorted(
            self._workers.values(), key=lambda worker: worker.worker_no)
        self.publish()

    @_locked
    def on_symbol_done(self, done: int, total: int, item: str) -> None:
        """
        Count one rendered symbol and drop its worker line until the worker reports again.

        Args:
            done: Symbols finished
            total: Symbols the render has
            item: The finished symbol, as broker_type/symbol

        Returns:
            None
        """
        for pid, line in list(self._workers.items()):
            if line.item == item:
                del self._workers[pid]
        self._steps[ImportStepId.BARS_RENDER].workers = sorted(
            self._workers.values(), key=lambda worker: worker.worker_no)
        self.advance(ImportStepId.BARS_RENDER, done, total)

    # =========================================================================
    # COUNTERS
    # =========================================================================

    @_locked
    def set_inbox(self, tick_files: int, tick_bytes: int,
                  signal_files: int, signal_bytes: int) -> None:
        """
        Record what the scan found.

        Args:
            tick_files: Tick files in the inbox
            tick_bytes: Their size
            signal_files: Signal files in the inbox
            signal_bytes: Their size

        Returns:
            None
        """
        self._tick_files = tick_files
        self._tick_bytes = tick_bytes
        self._signal_files = signal_files
        self._signal_bytes = signal_bytes

    @_locked
    def set_written(self, ticks_written: int, signal_rows_written: int) -> None:
        """
        Record what has been written so far.

        Args:
            ticks_written: Ticks written to the tick archive
            signal_rows_written: Rows written to the signal archive

        Returns:
            None
        """
        self._ticks_written = ticks_written
        self._signal_rows_written = signal_rows_written

    @_locked
    def set_issue_counts(self, warning_count: int, error_count: int) -> None:
        """
        Record the importers' own warning and error counts — the ones the summary lists.

        Args:
            warning_count: Warnings so far
            error_count: Errors so far

        Returns:
            None
        """
        self._warning_count = warning_count
        self._error_count = error_count

    @_locked
    def note_issue(self, level: LogLevel, text: str) -> None:
        """
        Pin the newest warning or error the importers counted, for the bottom line of the
        screen. Taken from the same lists the counters and the summary come from, so the line
        never shows a warning the counter does not count.

        Args:
            level: WARNING or ERROR
            text: The counted entry; only its first line is kept

        Returns:
            None
        """
        lines = text.strip().splitlines()
        if lines:
            self._last_message = ImportMessage(level=level, text=_without_emoji_variation(lines[0]))

    @_locked
    def set_outcome(self, outcome: ImportOutcome) -> None:
        """
        Record how the import ended.

        Args:
            outcome: The outcome

        Returns:
            None
        """
        self._outcome = outcome
        self.publish(force=True)

    # =========================================================================
    # ESTIMATE
    # =========================================================================

    def _estimate(self, step_id: ImportStepId) -> Optional[float]:
        """
        A step's estimated total seconds from its stored rate and this import's quantity.

        Args:
            step_id: Which step

        Returns:
            Seconds, or None without a rate in the same unit and worker count
        """
        rate = self._rates.get(step_id)
        quantity = self._quantities.get(step_id)
        if rate is None or quantity is None or rate.unit != self._units.get(step_id):
            return None
        if step_id == ImportStepId.BARS_RENDER and rate.workers != self._render_workers:
            return None
        return rate.seconds_per_unit * quantity

    def _step_remaining(self, step_id: ImportStepId, step: ImportStepFrame,
                        now: float) -> Optional[float]:
        """
        Estimated seconds one step still needs. A running step past a small share of its work
        extrapolates its own progress; before that, and for a pending step, its estimate
        decides. The screen shows exactly this per step, and the import's remaining time is
        their sum — one rule, so the step lines and the header cannot disagree.

        Args:
            step_id: Which step
            step: Its state
            now: The monotonic time to measure against

        Returns:
            Seconds, None for a finished step or one without an estimate
        """
        if step.status in _FINISHED:
            return None
        if step.status == ImportStepStatus.RUNNING:
            elapsed = now - self._step_started[step_id]
            share = (step.done / step.total) if step.total else 0.0
            if share >= _PROGRESS_SHARE_FOR_ESTIMATE:
                return max(elapsed / share - elapsed, 0.0)
            if step.estimate_s is None:
                return None
            return max(step.estimate_s - elapsed, 0.0)
        return step.estimate_s

    def _remaining(self) -> Optional[float]:
        """
        Estimated seconds left over every step not finished yet.

        Returns:
            Seconds, or None while a step left has no estimate
        """
        remaining = 0.0
        now = time.monotonic()
        for step_id, step in self._steps.items():
            if step.status in _FINISHED:
                continue
            left = self._step_remaining(step_id, step, now)
            if left is None:
                return None
            remaining += left
        return remaining

    @_locked
    def measured_rates(self) -> Dict[ImportStepId, ImportStepRate]:
        """
        The rate each finished step achieved in this import.

        Returns:
            Rates of the steps that finished with a known, non-zero quantity
        """
        rates: Dict[ImportStepId, ImportStepRate] = {}
        for step_id, step in self._steps.items():
            quantity = self._quantities.get(step_id)
            if (step.status != ImportStepStatus.DONE or not quantity
                    or step.elapsed_s is None):
                continue
            rates[step_id] = ImportStepRate(
                unit=self._units[step_id],
                seconds_per_unit=step.elapsed_s / quantity,
                workers=self._render_workers if step_id == ImportStepId.BARS_RENDER else None,
            )
        return rates

    # =========================================================================
    # FRAMES
    # =========================================================================

    def start_refresh(self) -> None:
        """
        Publish a fresh frame every update interval from a thread of its own, also while the
        import's thread is inside one long call — a tick file of hundreds of MB, a cache
        family, a render on one worker. Without it the screen's clock, its memory bar and the
        current item stood still for exactly as long as such a call took, which reads as a
        hung import. Nothing starts without a queue: nobody would see the frames.

        Returns:
            None
        """
        if self._queue is None or self._refresher is not None:
            return
        self._refresh_stopped.clear()
        self._refresher = threading.Thread(
            target=self._refresh_loop, name='Import-Refresh', daemon=True)
        self._refresher.start()

    def stop_refresh(self) -> None:
        """
        End the refresh thread, waiting for its last frame. Holds no lock while it waits, so
        the thread can finish the frame it is building.

        Returns:
            None
        """
        if self._refresher is None:
            return
        self._refresh_stopped.set()
        self._refresher.join(timeout=2.0)
        self._refresher = None

    def _refresh_loop(self) -> None:
        """
        The refresh thread: publish whenever the import's thread has not for an interval.

        Returns:
            None
        """
        interval = max(self._update_interval_s, _MIN_REFRESH_S)
        while not self._refresh_stopped.wait(interval):
            self.publish()

    @_locked
    def snapshot(self) -> ImportProgressFrame:
        """
        The import's state right now, as a frame that shares nothing with the tracker.

        Returns:
            The frame
        """
        now = time.monotonic()
        for step_id, step in self._steps.items():
            if step.status == ImportStepStatus.RUNNING:
                step.elapsed_s = now - self._step_started[step_id]
            step.remaining_s = self._step_remaining(step_id, step, now)
        memory = psutil.virtual_memory()
        steps: List[ImportStepFrame] = copy.deepcopy(list(self._steps.values()))
        elapsed = now - self._started_mono
        remaining = self._remaining() if self._outcome is None else 0.0
        open_estimate = self._outcome is None and remaining is not None
        return ImportProgressFrame(
            import_id=self._import_id,
            started_at=self._started_at,
            elapsed_s=elapsed,
            remaining_s=remaining,
            estimated_total_s=elapsed + remaining if open_estimate else None,
            # A point in time to read off a clock, not an event stamp — the wall clock is right
            estimated_finish_at=(datetime.now(timezone.utc) + timedelta(seconds=remaining)
                                 if open_estimate else None),
            cpu_percent=psutil.cpu_percent(interval=None),
            ram_used_bytes=memory.total - memory.available,
            ram_total_bytes=memory.total,
            tick_files=self._tick_files,
            tick_bytes=self._tick_bytes,
            signal_files=self._signal_files,
            signal_bytes=self._signal_bytes,
            ticks_written=self._ticks_written,
            signal_rows_written=self._signal_rows_written,
            warning_count=self._warning_count,
            error_count=self._error_count,
            last_message=copy.copy(self._last_message),
            steps=steps,
            log_path=self._log_path,
            outcome=self._outcome,
        )

    @_locked
    def publish(self, force: bool = False) -> None:
        """
        Hand a frame to the display, at most once per update interval unless forced.

        The queue keeps the newest frames: when it is full the oldest is dropped, because a
        display only ever shows the latest.

        Args:
            force: Publish now, regardless of the interval (a step changed state)

        Returns:
            None
        """
        if self._queue is None:
            return
        now = time.monotonic()
        if not force and now - self._last_published < self._update_interval_s:
            return
        self._last_published = now
        frame = self.snapshot()
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(frame)


def _without_emoji_variation(text: str) -> str:
    """
    A line without emoji variation selectors (U+FE0F): with one, a terminal draws the sign two
    cells wide while the layout counts one, which shifts the panel border on that line.

    Args:
        text: The line

    Returns:
        The line without U+FE0F, and without a leading warning sign the screen adds itself
    """
    return text.replace('\ufe0f', '').lstrip('⚠').strip()


def _deviation(estimate: Optional[float], actual: Optional[float]) -> Optional[float]:
    """
    How far an actual duration lay from its estimate, in percent of the estimate.

    Args:
        estimate: Estimated seconds
        actual: Actual seconds

    Returns:
        +/- percent, None when either is missing or the estimate is zero
    """
    if estimate is None or actual is None or estimate <= 0:
        return None
    return (actual / estimate - 1.0) * 100.0
