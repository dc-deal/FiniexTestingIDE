"""
FiniexTestingIDE - Import Progress Types
The frame a data import publishes while it runs (#175), and what feeds it.

The frame follows the live-telemetry pattern (#400): a @dataclass of primitives, lists and
enums, built in the importing process and handed to a display through a queue. It carries
every number the screen shows — the display computes nothing — so a later transport can
serve it unchanged through frame_to_json.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from python.framework.types.log_level import LogLevel


class ImportSection(Enum):
    """
    The part of an import a step belongs to. TICKS and SIGNALS are the two archive stores an
    import writes; INBOXES is the scan that precedes both, SHARED what follows both.
    """
    INBOXES = 'inboxes'
    TICKS = 'ticks'
    SIGNALS = 'signals'
    SHARED = 'shared'


class ImportStepId(Enum):
    """
    Every step an import can run, in the order it runs them. The frame holds the steps the
    import asked for; one whose inbox turned out empty is shown as skipped, not dropped.
    """
    SCAN = 'scan'
    TICKS_IMPORT = 'ticks_import'
    TICKS_INDEX = 'ticks_index'
    TICKS_ORDER = 'ticks_order'
    BARS_RENDER = 'bars_render'
    BARS_INDEX = 'bars_index'
    SIGNALS_IMPORT = 'signals_import'
    SIGNALS_INDEX = 'signals_index'
    CACHES = 'caches'


class ImportStepStatus(Enum):
    """Where one step stands."""
    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    SKIPPED = 'skipped'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'


class ImportOutcome(Enum):
    """
    How an import ended. Its own word rather than RunOutcome: an import is not a run, has no
    header and is not in the run index.
    """
    COMPLETED = 'completed'
    COMPLETED_WITH_ERRORS = 'completed_with_errors'
    INTERRUPTED = 'interrupted'
    FAILED = 'failed'


class BarRenderPhase(Enum):
    """What a bar-render worker is doing for its symbol right now."""
    LOADING_TICKS = 'loading_ticks'
    RENDERING = 'rendering'
    WRITING = 'writing'


class ImportRateUnit(Enum):
    """
    What an import step's duration scales with — the unit of its stored rate. MB is 2^20
    bytes, the MB the screen shows.
    """
    MB = 'mb'
    FILE = 'file'
    MILLION_TICKS = 'million_ticks'
    BAR_FILE = 'bar_file'


@dataclass
class BarRenderProgress:
    """
    One progress event a bar-render worker sends to its parent while it works.

    Args:
        symbol: Symbol the worker renders
        broker_type: Broker type of the symbol
        worker_pid: Process id of the worker, which tells two workers apart
        phase: What the worker is doing
        done: Units finished in this phase (tick files loaded, timeframes rendered or written)
        total: Units this phase has
        rss_bytes: The worker's resident memory at the moment of the event
    """
    symbol: str
    broker_type: str
    worker_pid: int
    phase: BarRenderPhase
    done: int
    total: int
    rss_bytes: int


@dataclass
class RenderWorkerLine:
    """
    One render worker as the screen shows it, under the render step.

    Args:
        worker_no: 1-based number of the worker, in the order it first reported
        item: The symbol it works on, as broker_type/symbol
        phase: What it is doing
        done: Units finished in this phase
        total: Units this phase has
        rss_bytes: Its resident memory
    """
    worker_no: int
    item: str
    phase: BarRenderPhase
    done: int
    total: int
    rss_bytes: int


@dataclass
class ImportStepFrame:
    """
    One step of an import as the screen shows it.

    Args:
        step_id: Which step
        section: The part of the import it belongs to
        label: The line the screen prints for it
        status: Where it stands
        done: Units finished
        total: Units it has, or None while unknown
        counter_unit: What its counter counts (files, symbols, caches)
        current_item: What it works on right now, empty when nothing
        detail: A fixed note beside the counter, e.g. the files per broker of the scan
        elapsed_s: Seconds it has run, None before it started
        estimate_s: Estimated total seconds for the step, None without a measured rate
        remaining_s: Estimated seconds still to go — a pending step's estimate, a running
            step's by the same rule as the import's remaining time; None without an estimate
            and once the step finished
        workers: The render workers, filled for the render step only
    """
    step_id: ImportStepId
    section: ImportSection
    label: str
    status: ImportStepStatus = ImportStepStatus.PENDING
    done: int = 0
    total: Optional[int] = None
    counter_unit: str = ''
    current_item: str = ''
    detail: str = ''
    elapsed_s: Optional[float] = None
    estimate_s: Optional[float] = None
    remaining_s: Optional[float] = None
    workers: List[RenderWorkerLine] = field(default_factory=list)


@dataclass
class ImportMessage:
    """
    The last warning or error an import logged, pinned at the bottom of the screen.

    Args:
        level: Its log level
        text: Its first line
    """
    level: LogLevel
    text: str


@dataclass
class ImportProgressFrame:
    """
    The whole state of a running import, as one snapshot.

    Args:
        import_id: The import's identity, also the name of its log file
        started_at: When it started, UTC
        elapsed_s: Seconds since it started
        remaining_s: Estimated seconds left, None while nothing can be estimated
        estimated_total_s: Elapsed plus remaining — the whole import's estimated duration,
            None while the remaining time is unknown or once the import ended
        estimated_finish_at: When it is estimated to end, UTC; None like estimated_total_s
        cpu_percent: Machine CPU load
        ram_used_bytes: Machine memory in use
        ram_total_bytes: Machine memory in total
        tick_files: Tick files found in the inbox
        tick_bytes: Their size
        signal_files: Signal files found in the inbox
        signal_bytes: Their size
        ticks_written: Ticks written to the tick archive so far
        signal_rows_written: Rows written to the signal archive so far
        warning_count: Warnings logged so far
        error_count: Errors logged or counted so far
        last_message: The last warning or error, None while there was none
        steps: Every step of this import, in order
        log_path: The import's own log file
        outcome: How it ended, None while it runs
    """
    import_id: str
    started_at: datetime
    elapsed_s: float
    remaining_s: Optional[float]
    estimated_total_s: Optional[float]
    estimated_finish_at: Optional[datetime]
    cpu_percent: float
    ram_used_bytes: int
    ram_total_bytes: int
    tick_files: int
    tick_bytes: int
    signal_files: int
    signal_bytes: int
    ticks_written: int
    signal_rows_written: int
    warning_count: int
    error_count: int
    last_message: Optional[ImportMessage]
    steps: List[ImportStepFrame]
    log_path: str
    outcome: Optional[ImportOutcome] = None


@dataclass
class InboxFile:
    """
    One file the scan found in an inbox, read from its size and — for a tick file — the
    metadata block at its head. Only for the screen and the estimate: the import itself
    parses every file in full.

    Args:
        path: The file
        size_bytes: Its size
        broker_type: Broker type from the metadata head, None for a signal file or when the
            head did not show it
    """
    path: Path
    size_bytes: int
    broker_type: Optional[str] = None


@dataclass
class InboxScan:
    """
    What both inboxes hold at the start of an import.

    Args:
        tick_files: The tick JSON files
        signal_files: The signal JSONL files
    """
    tick_files: List[InboxFile] = field(default_factory=list)
    signal_files: List[InboxFile] = field(default_factory=list)


@dataclass
class ImportStepTiming:
    """
    One step's estimate as the import started, against what it took — the summary's check of
    the estimate.

    Args:
        label: The step's line
        estimate_s: Seconds estimated once the scan had run, None without a stored rate
        actual_s: Seconds it took, None when it did not finish
        deviation_percent: How far the actual duration lay above (+) or below (−) the
            estimate, None when either is missing
    """
    label: str
    estimate_s: Optional[float]
    actual_s: Optional[float]
    deviation_percent: Optional[float]


@dataclass
class ImportPaths:
    """
    Where one import reads and writes. Taken from import_config.json; a test points it at a
    temporary tree.

    Args:
        tick_source: The tick inbox
        tick_target: The processed-data root the tick and bar archives live in
        tick_finished: Where imported tick files move, None when they stay
        signal_source: The signal inbox
        signal_target: The signal archive root
        signal_finished: Where imported signal files move, None when they stay
        log_dir: Where the import's own log file goes
        timings_file: The measured step durations
    """
    tick_source: Path
    tick_target: Path
    tick_finished: Optional[Path]
    signal_source: Path
    signal_target: Path
    signal_finished: Optional[Path]
    log_dir: Path
    timings_file: Path


@dataclass
class ImportStepRate:
    """
    The measured speed of one import step: seconds per unit of the quantity it scales with.

    Args:
        unit: The quantity it scales with
        seconds_per_unit: Seconds one unit took
        samples: How many imports the value is smoothed over
        workers: Worker count it was measured with, for the render step only
    """
    unit: ImportRateUnit
    seconds_per_unit: float
    samples: int = 1
    workers: Optional[int] = None
