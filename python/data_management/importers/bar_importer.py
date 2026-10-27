"""
Bar Importer - Pre-Render Bars from Tick Data
==============================================

Orchestrates bar pre-rendering for all symbols and timeframes.
Called automatically after tick import or manually for re-rendering.

Supports parallel rendering via ProcessPoolExecutor (symbol-level granularity).
All broker_types and symbols are rendered in one pool.

Workflow:
1. Load ALL tick files for a symbol
2. Call VectorizedBarRenderer for all timeframes
3. Write bar parquet files (one per timeframe) beside the old ones, then replace them
4. After a COMPLETE render: remove what no longer has a source
5. Update bar index (caller responsibility)

Nothing is deleted before it has been rendered again: a render stopped half-way leaves every
symbol it did not reach with its previous bars, never with none.

"""

import ctypes
import ctypes.util
import os
import signal
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from multiprocessing.managers import SyncManager
from pathlib import Path
from queue import Empty
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.importers.bar_render_progress_reporter import BarRenderProgressReporter
from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.exceptions.data_quality_errors import BarFileVerificationException
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.import_types.import_progress_types import (
    BarRenderPhase,
    BarRenderProgress,
)
from python.framework.types.import_types.import_result_types import BarRenderResult
from python.framework.utils.file_utils import create_directory

vLog = get_global_logger()

BAR_FILE_SUFFIX = '_BARS.parquet'
# A bar file being written. The index scan matches *_BARS.parquet and never sees it.
PARTIAL_SUFFIX = '.partial'
# How long the parent waits for a finished symbol before it shows the workers' progress again.
_POLL_INTERVAL_S = 0.25
# prctl option (Linux): the signal a process receives when its parent dies.
_PR_SET_PDEATHSIG = 1
# What a render reads of a tick file, and what it keeps once the reader has derived `price` and
# `volume`: event time and its order, the price, a column to count ticks by, the traded volume.
# A worker holds a symbol's whole history in one frame, so every other column is memory spent for
# nothing — measured 2026-10-13 on ETHUSD's 60M ticks: 20.5 GB with every column, 8.4 GB with
# these, every bar of every timeframe identical.
_RENDER_SOURCE_COLUMNS = ['timestamp', 'time_msc', 'bid', 'ask', 'last', 'real_volume', 'volume']
_RENDER_COLUMNS = ['timestamp', 'time_msc', 'price', 'bid', 'volume']
# What one render worker holds, from the same measurement: 8.4 GB peak for 60.0M ticks, the
# concatenation and the event-order sort included, over a process that starts at about 0.4 GB.
_RENDER_BASE_MB = 400
_RENDER_BYTES_PER_TICK = 140


# =============================================================================
# TOP-LEVEL WORKER FUNCTION (required for multiprocessing pickle)
# =============================================================================

def _ignore_interrupt_signals() -> None:
    """
    Initializer of every child a render starts: leave Ctrl+C and SIGTERM to the parent, and
    end with the parent.

    Ctrl+C reaches every process of the terminal's foreground group, so without this each
    worker printed its own traceback over the screen, and a worker inherits a SIGTERM handler
    an import installs for itself. The parent ends its children when it is interrupted.

    Returns:
        None
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _end_with_parent()


def _end_with_parent() -> None:
    """
    Ask the OS to end this process when its parent dies — the one stop no handler of the parent
    can see. Measured 2026-10-10: the workers of a parent killed outright finished the symbol they
    held and the ones already queued, replacing bar files nobody indexed, then stayed idle under
    PID 1 until ended by hand. Linux only; elsewhere nothing changes.

    Returns:
        None
    """
    if not sys.platform.startswith('linux'):
        return
    libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
    libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def _render_symbol_worker(
    symbol: str,
    broker_type: str,
    data_dir: str,
    importer_version: str,
    progress_queue: Optional[Any] = None
) -> BarRenderResult:
    """
    Standalone worker function for parallel bar rendering.

    Runs in a subprocess — must be top-level for pickle compatibility.
    Each worker builds its own TickIndexManager and VectorizedBarRenderer.
    All log output is buffered and returned in the result.

    Args:
        symbol: Trading symbol to render
        broker_type: Broker type identifier
        data_dir: Path to data directory (string for pickle)
        importer_version: BarImporter.VERSION string
        progress_queue: Manager queue for progress events, None when nobody listens

    Returns:
        BarRenderResult with rendered bar count, log buffer, and status
    """
    log_buffer: list[str] = []
    data_path = Path(data_dir)
    reporter = BarRenderProgressReporter(progress_queue, symbol, broker_type)

    try:
        start_time = time.monotonic()

        # === 1. BUILD OWN TICK INDEX ===
        tick_index = TickIndexManager(data_dir=data_dir)
        tick_index.build_index()

        # === 2. LOAD TICK DATA ===
        log_buffer.append(f'  ├─ Loading tick data for {broker_type}/{symbol}...')
        ticks_df = _load_all_ticks_for_symbol(
            tick_index, symbol, broker_type, log_buffer,
            on_file_loaded=lambda done, total: reporter.report(
                BarRenderPhase.LOADING_TICKS, done, total))

        if ticks_df.empty:
            log_buffer.append(f'  └─ No tick data found for {broker_type}/{symbol}')
            return BarRenderResult(
                symbol=symbol,
                broker_type=broker_type,
                bars_rendered=0,
                success=True,
                log_buffer=log_buffer
            )

        log_buffer.append(f'  ├─ Loaded {len(ticks_df):,} ticks')

        # === 2.5 EXTRACT SOURCE VERSIONS ===
        tick_files = [
            Path(entry['path'])
            for entry in tick_index.index[broker_type][symbol]
        ]
        source_version_min, source_version_max = _extract_source_versions(
            tick_files, log_buffer)
        # Which origins the ticks came from, and where our own collection takes over (#561)
        origin_classes, collected_from = _source_origin(tick_index.index[broker_type][symbol])

        # === 3. RENDER BARS ===
        log_buffer.append('  ├─ Rendering bars...')
        renderer = VectorizedBarRenderer(symbol, log_buffer=log_buffer)
        all_bars = renderer.render_all_timeframes(
            ticks_df,
            on_timeframe_rendered=lambda done, total: reporter.report(
                BarRenderPhase.RENDERING, done, total))

        # === 4. WRITE BAR FILES ===
        log_buffer.append('  ├─ Writing bar files...')
        bars_written = 0

        for written, (timeframe, bars_df) in enumerate(all_bars.items(), 1):
            if len(bars_df) > 0:
                _write_bar_file(
                    data_path, symbol, timeframe, bars_df,
                    broker_type, importer_version,
                    source_version_min, source_version_max,
                    log_buffer,
                    origin_classes=origin_classes, collected_from=collected_from
                )
                bars_written += len(bars_df)
            reporter.report(BarRenderPhase.WRITING, written, len(all_bars))

        # === 5. LOG STATISTICS ===
        elapsed = time.monotonic() - start_time
        log_buffer.append(
            f'  └─ ✅ {broker_type}/{symbol}: {bars_written:,} bars across '
            f'{len(all_bars)} timeframes in {elapsed:.2f}s'
        )

        return BarRenderResult(
            symbol=symbol,
            broker_type=broker_type,
            bars_rendered=bars_written,
            success=True,
            log_buffer=log_buffer
        )

    except Exception as e:
        log_buffer.append(f'  └─ ❌ ERROR: {str(e)}')
        log_buffer.append(traceback.format_exc())
        return BarRenderResult(
            symbol=symbol,
            broker_type=broker_type,
            bars_rendered=0,
            success=False,
            error_message=f'ERROR in {broker_type}/{symbol}: {str(e)}',
            log_buffer=log_buffer
        )


# =============================================================================
# WORKER HELPER FUNCTIONS (top-level for pickle)
# =============================================================================

def _load_all_ticks_for_symbol(
    tick_index: TickIndexManager,
    symbol: str,
    broker_type: str,
    log_buffer: list[str],
    on_file_loaded: Optional[Callable[[int, int], None]] = None
) -> pd.DataFrame:
    """
    Load ALL tick files for a symbol.

    Args:
        tick_index: TickIndexManager instance (worker-local)
        symbol: Trading symbol
        broker_type: Broker type identifier
        log_buffer: Buffer for log messages
        on_file_loaded: Called with (files loaded, file count) after each tick file

    Returns:
        DataFrame with all ticks for symbol
    """
    if broker_type not in tick_index.index:
        log_buffer.append(
            f"  ⚠️ Broker type '{broker_type}' not found in tick index")
        return pd.DataFrame()

    if symbol not in tick_index.index[broker_type]:
        log_buffer.append(
            f"  ⚠️ Symbol '{symbol}' not found in tick index for broker_type '{broker_type}'")
        return pd.DataFrame()

    tick_files = [
        Path(entry['path'])
        for entry in tick_index.index[broker_type][symbol]
    ]

    dfs = []
    for loaded, tick_file in enumerate(tick_files, 1):
        df = read_tick_parquet(tick_file, columns=_RENDER_SOURCE_COLUMNS)
        dfs.append(df[[column for column in _RENDER_COLUMNS if column in df.columns]])
        if on_file_loaded is not None:
            on_file_loaded(loaded, len(tick_files))

    if not dfs:
        return pd.DataFrame()

    # Not sorted here: the renderer puts the ticks in event order itself, so the rule that
    # decides a bar's open and close exists in one place.
    return pd.concat(dfs, ignore_index=True)


def _write_bar_file(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    bars_df: pd.DataFrame,
    broker_type: str,
    importer_version: str,
    source_version_min: str = '1.0.0',
    source_version_max: str = '1.0.0',
    log_buffer: list[str] = None,
    origin_classes: str = '',
    collected_from: str = ''
) -> None:
    """
    Write bar DataFrame to parquet file.

    Written beside the existing file first and verified there; only a file that reads back in
    full replaces the old one. An interruption therefore leaves the previous file in place,
    and a broken render never replaces a good file.

    Args:
        data_dir: Base data directory
        symbol: Trading symbol
        timeframe: Timeframe string
        bars_df: Bar DataFrame
        broker_type: Broker type identifier
        importer_version: BarImporter version string
        source_version_min: Minimum source data version
        source_version_max: Maximum source data version
        log_buffer: Buffer for log messages
    """
    bars_dir = data_dir / broker_type / 'bars' / symbol
    create_directory(bars_dir, holds="a symbol's rendered bar files")

    filename = f'{symbol}_{timeframe}{BAR_FILE_SUFFIX}'
    filepath = bars_dir / filename
    partial_path = bars_dir / f'{filename}{PARTIAL_SUFFIX}'

    market_config = MarketConfigManager()
    market_type = market_config.get_market_type(broker_type)

    metadata = {
        'symbol': symbol,
        'timeframe': timeframe,
        'broker_type': broker_type,
        'market_type': market_type.value,
        'bar_count': str(len(bars_df)),
        'start_time': bars_df['timestamp'].min().isoformat(),
        'end_time': bars_df['timestamp'].max().isoformat(),
        'importer_version': importer_version,
        'rendered_at': pd.Timestamp.now(tz='UTC').isoformat(),
        'source_version_min': source_version_min,
        'source_version_max': source_version_max,
        # What this file was rendered FROM. Without it a half-re-rendered archive is
        # indistinguishable from a consistent one, and nothing can cross-check a run's bars
        # against the source it read them with.
        'price_basis': market_config.get_price_formation(broker_type).value,
        # Which origins the ticks behind these bars came from, and the instant our own
        # collection takes over from a venue's published history (#561) — the seam is empty
        # unless external history precedes our collection. One file covers a symbol's whole
        # history, so without the seam a warmup reaching back into external bars could not be
        # told apart.
        'origin_classes': origin_classes,
        'collected_from': collected_from,
    }

    table = pa.Table.from_pandas(bars_df)
    table = table.replace_schema_metadata(metadata)
    pq.write_table(table, partial_path, compression='snappy')

    _verify_bar_file(partial_path, len(bars_df))
    os.replace(partial_path, filepath)

    file_size_mb = filepath.stat().st_size / (1024 * 1024)
    if log_buffer is not None:
        log_buffer.append(
            f'    ├─ Written: {filename} '
            f'({len(bars_df):,} bars, {file_size_mb:.2f} MB)'
        )


def _verify_bar_file(filepath: Path, expected_rows: int) -> None:
    """
    Read a freshly written bar file back in full to prove it is readable.

    Only a FULL read finds a truncated or partially written column: the footer still
    reports the correct row count, and projecting a single column reads cleanly — a
    projection of `timestamp` reported 128 healthy files while one carried a corrupt
    `low`. Measured: 64 Kraken bar files read completely in 2.6 s against a render of
    about four minutes, roughly 1 %. The largest file today (BTCUSD M1, 329,253 rows)
    costs 434 ms and 25 MB; it is a single row group, so reading it piecewise would
    bound nothing.

    Args:
        filepath: Path of the bar parquet that was just written
        expected_rows: Number of rows the file is supposed to carry

    Returns:
        None
    """
    try:
        table = pq.read_table(filepath)
    except Exception as e:
        raise BarFileVerificationException(
            f'Bar file unreadable right after writing: {filepath.name} — {e}'
        ) from e

    if table.num_rows != expected_rows:
        raise BarFileVerificationException(
            f'Bar file row count mismatch: {filepath.name} reads back '
            f'{table.num_rows} rows, {expected_rows} were written'
        )


def _source_origin(entries: List[Dict]) -> Tuple[str, str]:
    """
    The origins a symbol's ticks came from, and where our own collection takes over.

    Args:
        entries: The symbol's tick index entries

    Returns:
        Tuple (distinct origin classes, sorted and comma-joined; the first tick not from a
        venue's published history, as ISO — empty unless external history precedes it)
    """
    classes = sorted({entry.get('origin_class', 'unknown') for entry in entries})
    if OriginClass.EXTERNAL.value not in classes or len(classes) == 1:
        return ','.join(classes), ''
    collected = [entry['start_time'] for entry in entries
                 if entry.get('origin_class') != OriginClass.EXTERNAL.value
                 and entry.get('start_time')]
    first = min(pd.to_datetime(collected, utc=True)) if collected else None
    return ','.join(classes), first.isoformat() if first is not None else ''


def _extract_source_versions(
    tick_files: List[Path],
    log_buffer: list[str]
) -> Tuple[str, str]:
    """
    Extract min/max data_format_version from tick parquet files.

    Args:
        tick_files: List of tick parquet file paths
        log_buffer: Buffer for log messages

    Returns:
        Tuple (version_min, version_max)
    """
    versions = []

    for tick_file in tick_files:
        try:
            pq_file = pq.ParquetFile(tick_file)
            metadata_raw = pq_file.metadata.metadata

            version = metadata_raw.get(b'data_format_version', b'1.0.0')
            if isinstance(version, bytes):
                version = version.decode('utf-8')

            # Fallback: Try collector_version (legacy field)
            if version == '1.0.0':
                legacy = metadata_raw.get(b'collector_version', b'1.0.0')
                if isinstance(legacy, bytes):
                    legacy = legacy.decode('utf-8')
                version = legacy

            versions.append(version)

        except Exception as e:
            log_buffer.append(
                f'  ⚠️ Could not extract version from {tick_file.name}: {e}')
            versions.append('1.0.0')

    if not versions:
        return ('1.0.0', '1.0.0')

    sorted_versions = sorted(set(versions), key=lambda v: [
        int(x) for x in v.split('.')])
    return (sorted_versions[0], sorted_versions[-1])


def effective_render_workers(configured_workers: int, symbol_count: int) -> int:
    """
    The worker count a render of this many symbols runs with: never more workers than
    symbols, and at least one. The render and the import's estimate both ask here, so a
    stored render rate is keyed to the count that actually ran.

    Args:
        configured_workers: processing.bar_render_workers
        symbol_count: Symbols to render

    Returns:
        Effective worker count
    """
    return max(1, min(configured_workers, symbol_count))


def estimate_render_mb(tick_count: int) -> float:
    """
    What one render worker is expected to hold for a symbol, from its tick count alone.

    Args:
        tick_count: The symbol's ticks, from the tick index

    Returns:
        Estimated peak memory in MB
    """
    return _RENDER_BASE_MB + tick_count * _RENDER_BYTES_PER_TICK / 1e6


def admit_render_tasks(
    waiting: List[Tuple[str, str]],
    estimates: Dict[Tuple[str, str], float],
    reserved_mb: float,
    running: int,
    max_workers: int,
    budget_mb: float
) -> List[Tuple[str, str]]:
    """
    The waiting symbols that may start now: while a worker is free and the reserved memory plus
    a symbol's estimate fits the budget. A symbol larger than the whole budget starts only when
    nothing else runs, so it renders alone rather than never. Scheduling only — every symbol
    renders independently, so the bars do not depend on the order.

    Args:
        waiting: Symbols not started yet, largest estimate first
        estimates: Estimated MB per symbol
        reserved_mb: Estimates of the running symbols, summed
        running: Running symbols
        max_workers: processing.bar_render_workers, as effective for this render
        budget_mb: processing.bar_render_max_ram_mb

    Returns:
        The symbols to start, in order
    """
    admitted = []
    for task in waiting:
        if running >= max_workers:
            break
        if reserved_mb + estimates[task] <= budget_mb or running == 0:
            admitted.append(task)
            reserved_mb += estimates[task]
            running += 1
    return admitted


def is_production_archive(data_dir: Path) -> bool:
    """
    Whether a directory is the production archive — the one the discovery caches and the
    import timings describe. An import scoped anywhere else must touch neither.

    Args:
        data_dir: The directory an import writes to

    Returns:
        True when it is the configured processed-data directory
    """
    production = Path(AppConfigManager().get_data_processed_path()).resolve()
    return data_dir.resolve() == production


# =============================================================================
# BAR IMPORTER CLASS
# =============================================================================

class BarImporter:
    """
    Main orchestrator for bar pre-rendering system.

    Renders bars from tick data and saves them as parquet files.
    One file per timeframe per symbol.
    """

    VERSION = '1.1'  # Updated for broker_type-first index structure

    def __init__(self, data_dir: Optional[str] = None):
        """
        Initialize Bar importer.

        Args:
            data_dir: Override data directory (default: from ImportConfigManager)
        """
        self._import_config = ImportConfigManager()

        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(self._import_config.get_import_output_path())
        if not self.data_dir.exists():
            raise FileNotFoundError(
                f'Data directory not found: {self.data_dir}')

        # Initialize tick index for finding tick files
        self.tick_index = TickIndexManager(data_dir=str(self.data_dir))
        self.tick_index.build_index()

        # Statistics
        self.processed_symbols = 0
        self.total_bars_rendered = 0
        self.errors = []
        # What the operator must act on without the render having failed: an interrupted
        # render, bars kept because the tick index does not list their symbol
        self.warnings: List[str] = []
        # The symbols a render has finished, as broker_type/symbol — what an interrupted
        # render can say it did not reach.
        self._finished_items: Set[str] = set()

    def render_bars_for_all_symbols(
        self,
        broker_types: List[str],
        on_symbol_done: Optional[Callable[[int, int, str], None]] = None,
        on_worker_progress: Optional[Callable[[BarRenderProgress], None]] = None
    ):
        """
        Render bars for ALL symbols across one or more broker_types.

        All symbols from all broker_types are rendered in a single pool. A symbol's files
        replace its previous ones only once written and verified, and files without a source
        are removed only after every symbol rendered — a render stopped half-way leaves each
        symbol it did not reach with its previous bars.
        Index/cache rebuild is NOT performed — caller is responsible.

        Args:
            broker_types: List of broker type identifiers (e.g., ['mt5', 'kraken_spot'])
            on_symbol_done: Called with (symbols done, symbol count, broker_type/symbol)
            on_worker_progress: Called with each progress event a worker sends (parallel only)
        """
        max_workers = self._import_config.get_bar_render_workers()

        vLog.info('\n' + '=' * 80)
        vLog.info(
            f"Bar Pre-Rendering - Batch Mode ({', '.join(broker_types)})")
        vLog.info(f'Workers: {max_workers}')
        vLog.info('=' * 80)

        # A file left half-written by an earlier, interrupted render
        for bt in broker_types:
            self._remove_partial_files(bt)

        # === COLLECT ALL (broker_type, symbol) PAIRS ===
        all_tasks: List[Tuple[str, str]] = []
        for bt in broker_types:
            symbols = self.tick_index.list_symbols(bt)
            if not symbols:
                vLog.warning(
                    f"No symbols found in tick data for broker_type '{bt}'!")
                continue
            for symbol in symbols:
                all_tasks.append((bt, symbol))

        if not all_tasks:
            vLog.warning('No symbols found across any broker_type!')
            return

        vLog.info(
            f'Found {len(all_tasks)} symbols to process across '
            f'{len(broker_types)} broker_types'
        )
        vLog.info('=' * 80 + '\n')

        # === PARALLEL RENDERING ===
        effective_workers = effective_render_workers(max_workers, len(all_tasks))

        try:
            if effective_workers <= 1:
                self._render_sequential(all_tasks, on_symbol_done)
            else:
                self._render_parallel(
                    all_tasks, effective_workers, on_symbol_done, on_worker_progress)
        except KeyboardInterrupt:
            self._log_interrupted(all_tasks)
            raise

        # Only now, with every symbol rendered: what has no source any more
        for bt in broker_types:
            self._remove_stale_bar_files(bt)

        # Print summary
        self._print_summary()

    def _render_sequential(
        self,
        tasks: List[Tuple[str, str]],
        on_symbol_done: Optional[Callable[[int, int, str], None]] = None
    ) -> None:
        """
        Render symbols sequentially (single-process fallback).

        Args:
            tasks: List of (broker_type, symbol) tuples
            on_symbol_done: Called with (symbols done, symbol count, broker_type/symbol)

        Returns:
            None
        """
        for i, (broker_type, symbol) in enumerate(tasks, 1):
            vLog.info(
                f'\n[{i}/{len(tasks)}] Processing {broker_type}/{symbol}...')

            result = _render_symbol_worker(
                symbol, broker_type, str(self.data_dir), self.VERSION
            )

            # Flush log buffer
            for line in result.log_buffer:
                vLog.info(line)

            self._book_result(result)
            if on_symbol_done is not None:
                on_symbol_done(i, len(tasks), f'{broker_type}/{symbol}')

    def _render_parallel(
        self,
        tasks: List[Tuple[str, str]],
        max_workers: int,
        on_symbol_done: Optional[Callable[[int, int, str], None]] = None,
        on_worker_progress: Optional[Callable[[BarRenderProgress], None]] = None
    ) -> None:
        """
        Render symbols in parallel using ProcessPoolExecutor.

        Each symbol is logged the moment it finishes. Until #175 every line was held back
        until the whole pool was done, so a render of several minutes printed nothing between
        its start line and its summary — and a silent terminal reads as a finished one.

        Args:
            tasks: List of (broker_type, symbol) tuples
            max_workers: Number of worker processes
            on_symbol_done: Called with (symbols done, symbol count, broker_type/symbol)
            on_worker_progress: Called with each progress event a worker sends

        Returns:
            None
        """
        vLog.info(
            f'🚀 Launching {max_workers} worker processes for '
            f'{len(tasks)} symbols...\n'
        )

        # The progress channel exists only when someone listens: a manager is a process of its own.
        manager: Optional[SyncManager] = None
        if on_worker_progress is not None:
            manager = SyncManager()
            manager.start(_ignore_interrupt_signals)
        progress_queue = manager.Queue() if manager is not None else None
        children_before = {child.pid for child in psutil.Process().children()}

        # Admission by memory (#442): a symbol starts only while the estimates of the running
        # ones plus its own fit the budget, largest first so a large one is not left to the end
        estimates = {
            task: estimate_render_mb(sum(
                entry.get('tick_count', 0)
                for entry in self.tick_index.get_symbol_entries(*task)))
            for task in tasks}
        budget_mb = self._import_config.get_bar_render_max_ram_mb()
        waiting = sorted(tasks, key=lambda task: estimates[task], reverse=True)
        for task in waiting:
            if estimates[task] > budget_mb:
                vLog.warning(
                    f'⚠️  {task[0]}/{task[1]}: an estimated {estimates[task] / 1000:.1f} GB exceeds '
                    f'processing.bar_render_max_ram_mb ({budget_mb / 1000:.1f} GB) — it renders alone')

        executor = ProcessPoolExecutor(
            max_workers=max_workers, initializer=_ignore_interrupt_signals)
        try:
            future_to_task: Dict[Future, Tuple[str, str]] = {}
            pending: Set[Future] = set()
            reserved_mb = 0.0
            finished = 0

            while waiting or pending:
                for task in admit_render_tasks(
                        waiting, estimates, reserved_mb, len(pending), max_workers, budget_mb):
                    waiting.remove(task)
                    broker_type, symbol = task
                    future = executor.submit(
                        _render_symbol_worker,
                        symbol, broker_type, str(self.data_dir), self.VERSION, progress_queue)
                    future_to_task[future] = task
                    pending.add(future)
                    reserved_mb += estimates[task]

                done, pending = wait(
                    pending, timeout=_POLL_INTERVAL_S, return_when=FIRST_COMPLETED)
                self._drain_progress(progress_queue, on_worker_progress)

                for future in done:
                    broker_type, symbol = future_to_task[future]
                    reserved_mb -= estimates[(broker_type, symbol)]
                    result = self._collect_result(future, broker_type, symbol, max_workers)
                    finished += 1
                    vLog.info(
                        f'\n[{finished}/{len(tasks)}] {result.broker_type}/{result.symbol}:')
                    for line in result.log_buffer:
                        vLog.info(line)
                    self._book_result(result)
                    if on_symbol_done is not None:
                        on_symbol_done(finished, len(tasks), f'{broker_type}/{symbol}')

            self._drain_progress(progress_queue, on_worker_progress)
            executor.shutdown(wait=True)

        except BaseException:
            # The workers would otherwise finish the symbol they hold, unseen, before the
            # process can exit. What they leave half-written is a .partial file, never a
            # damaged bar file, and the next render removes it. Any exception, not only a
            # stop: a pool left running would keep replacing bar files after the caller
            # has rebuilt the index from them.
            if manager is not None:
                manager.shutdown()
                manager = None
            self._terminate_workers(children_before)
            executor.shutdown(wait=False, cancel_futures=True)
            raise

        finally:
            if manager is not None:
                manager.shutdown()

    def _collect_result(
        self,
        future: Future,
        broker_type: str,
        symbol: str,
        max_workers: int
    ) -> BarRenderResult:
        """
        The result of one finished future, with a crashed worker turned into a failed result.

        Args:
            future: The finished future
            broker_type: Broker type of its symbol
            symbol: Its symbol
            max_workers: Worker count of the pool, named in the out-of-memory hint

        Returns:
            The worker's result, or a failed result describing the crash
        """
        try:
            return future.result()
        except BrokenProcessPool as e:
            # The OS ended a worker — in this pipeline almost always for memory (#442), and
            # the pool then fails every symbol still queued with this same exception.
            error_msg = (
                f'ERROR in {broker_type}/{symbol}: a render worker was ended abruptly — most '
                f'likely the machine ran out of memory. Lower processing.bar_render_workers '
                f'(now {max_workers}) in import_config.json, or free memory, and render again '
                f'({e})')
        except Exception as e:
            error_msg = f'ERROR in {broker_type}/{symbol}: {str(e)}'
        return BarRenderResult(
            symbol=symbol,
            broker_type=broker_type,
            success=False,
            error_message=error_msg,
            log_buffer=[f'  └─ ❌ Worker crashed: {error_msg}']
        )

    def _book_result(self, result: BarRenderResult) -> None:
        """
        Count one symbol's result into the statistics.

        Args:
            result: The symbol's render result

        Returns:
            None
        """
        self._finished_items.add(f'{result.broker_type}/{result.symbol}')
        if result.success:
            self.processed_symbols += 1
            self.total_bars_rendered += result.bars_rendered
        else:
            vLog.error(result.error_message)
            self.errors.append(result.error_message)

    @staticmethod
    def _drain_progress(
        progress_queue: Optional[Any],
        on_worker_progress: Optional[Callable[[BarRenderProgress], None]]
    ) -> None:
        """
        Hand every queued worker event to the listener.

        Args:
            progress_queue: The manager queue, or None
            on_worker_progress: The listener, or None

        Returns:
            None
        """
        if progress_queue is None or on_worker_progress is None:
            return
        while True:
            try:
                event = progress_queue.get_nowait()
            except Empty:
                return
            on_worker_progress(event)

    @staticmethod
    def _terminate_workers(children_before: Set[int]) -> None:
        """
        End the worker processes this render started.

        Args:
            children_before: Process ids of this process's children before the pool started

        Returns:
            None
        """
        workers = [child for child in psutil.Process().children()
                   if child.pid not in children_before]
        # A worker can exit on its own between the listing and the signal
        for worker in workers:
            try:
                worker.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(workers, timeout=3)
        for worker in alive:
            try:
                worker.kill()
            except psutil.NoSuchProcess:
                pass

    def _log_interrupted(self, tasks: List[Tuple[str, str]]) -> None:
        """
        Say that the render stopped, what it did not finish, and how to finish it — counted
        as a warning, so the summary lists it even when a live screen held the console.

        Args:
            tasks: Every (broker_type, symbol) the render was asked for

        Returns:
            None
        """
        missing = [(bt, symbol) for bt, symbol in tasks
                   if f'{bt}/{symbol}' not in self._finished_items]
        if not missing:
            return
        missing_brokers = sorted({bt for bt, _ in missing})
        rerun = (f'render {missing_brokers[0]}' if len(missing_brokers) == 1
                 else 'render --all')
        # A worker replaces its timeframes one by one, so a symbol stopped while writing
        # holds new and previous files side by side — only one never reached is untouched
        message = (
            f'⚠️  Bar render INTERRUPTED after {len(tasks) - len(missing)} of {len(tasks)} '
            f'symbols — not finished: '
            f"{', '.join(f'{bt}/{symbol}' for bt, symbol in missing)}. "
            f'A symbol the render never reached keeps its previous bars; one stopped while '
            f'writing may mix new and previous timeframes until it is rendered again. '
            f'Re-run: python python/cli/bar_index_cli.py {rerun}'
        )
        vLog.warning(message)
        self.warnings.append(message)

    def _remove_partial_files(self, broker_type: str) -> None:
        """
        Remove bar files an interrupted render left half-written.

        Args:
            broker_type: Broker type identifier

        Returns:
            None
        """
        bars_base_dir = self.data_dir / broker_type / 'bars'
        if not bars_base_dir.exists():
            return
        for partial in bars_base_dir.glob(f'**/*{BAR_FILE_SUFFIX}{PARTIAL_SUFFIX}'):
            partial.unlink()
            vLog.info(f'   🧹 Removed unfinished {partial.name}')

    def _remove_stale_bar_files(self, broker_type: str) -> None:
        """
        Remove bar files that no longer have a source: symbols without tick data, and
        timeframes no longer in render_timeframes. Bars are DERIVED — a bar file without its
        ticks is misfiled, and a timeframe nobody renders goes stale without saying so.

        "Without tick data" is decided on disk, not by the tick index alone: an index that
        missed a symbol (a stale one, or files it could not read) would otherwise delete
        bars whose ticks are right there. Such bars are kept, and the gap is counted.

        Args:
            broker_type: Broker type identifier

        Returns:
            None
        """
        bars_base_dir = self.data_dir / broker_type / 'bars'
        if not bars_base_dir.exists():
            return

        symbols_with_ticks = set(self.tick_index.list_symbols(broker_type))
        timeframes = set(self._import_config.get_render_timeframes())
        ticks_base_dir = self.data_dir / broker_type / 'ticks'

        for symbol_dir in sorted(bars_base_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            symbol = symbol_dir.name
            if (symbol not in symbols_with_ticks
                    and any((ticks_base_dir / symbol).glob('*.parquet'))):
                message = (f'⚠️  Kept the bars of {broker_type}/{symbol}: its tick files are '
                           f'on disk, but the tick index does not list them — rebuild the '
                           f'tick index, then render again')
                vLog.warning(message)
                self.warnings.append(message)
                continue
            prefix = f'{symbol}_'
            for bar_file in sorted(symbol_dir.glob(f'*{BAR_FILE_SUFFIX}')):
                if not bar_file.name.startswith(prefix):
                    continue
                timeframe = bar_file.name[len(prefix):-len(BAR_FILE_SUFFIX)]
                if symbol in symbols_with_ticks and timeframe in timeframes:
                    continue
                reason = ('no tick data for this symbol' if symbol not in symbols_with_ticks
                          else f'{timeframe} is not in render_timeframes')
                bar_file.unlink()
                vLog.info(f'   🗑️  Removed {broker_type}/{bar_file.name} — {reason}')

            if not any(symbol_dir.iterdir()):
                symbol_dir.rmdir()
                vLog.debug(f'   Removed empty directory: {symbol}')

    def update_bar_index(
        self,
        on_file_scanned: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Rebuild the bar index from the bar files on disk.

        Called by the caller after rendering — also after an interrupted one, so the index
        describes what is there. A failure is counted among the errors, and so is a bar file
        the scan could not read: either way the index no longer describes the archive, and an
        import must not report success over it.

        Args:
            on_file_scanned: Called with (file number, file count) for each bar file scanned

        Returns:
            True when the index was rebuilt and lists every bar file on disk
        """
        vLog.info('\n📄 Updating bar index...')
        try:
            bar_index = BarsIndexManager(data_dir=str(self.data_dir))
            bar_index.build_index(force_rebuild=True, on_file_scanned=on_file_scanned)

            # Count symbols across all broker_types
            total_symbols = len(bar_index.list_symbols())
            broker_types = bar_index.list_broker_types()
            vLog.info(
                f"✅ Bar index updated: {total_symbols} symbols across "
                f"{len(broker_types)} broker_types ({', '.join(broker_types)})"
            )

        except Exception as e:
            vLog.error(f'❌ Failed to update bar index: {e}')
            vLog.error('   Index may be outdated - run manual rebuild!')
            self.errors.append(f'Bar index update failed: {e}')
            return False

        unreadable = bar_index.get_unreadable_files()
        if unreadable:
            self.errors.append(
                f'Bar index: {len(unreadable)} bar file(s) could not be read and are missing '
                f'from the index — re-render: python python/cli/bar_index_cli.py render --all')
            return False
        return True

    def rebuild_discovery_caches(
        self,
        on_cache_done: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Rebuild all discovery caches after a render — production archive only.

        Args:
            on_cache_done: Called with (caches done, cache count) after each cache family

        Returns:
            True when the caches were rebuilt, False when this import may not touch them
        """
        # Rebuild all discovery caches. They resolve their own paths and cannot be
        # scoped, so rebuilding them from an import directed elsewhere would rewrite
        # production caches from bars this run never touched (#583 owns the
        # convergence of the three index managers).
        if not self._writes_production_archive():
            vLog.info(
                '⏭️  Discovery caches NOT rebuilt: this import is scoped to '
                f'{self.data_dir}, and the caches can only be built for the '
                'production archive.'
            )
            return False
        try:
            DiscoveryCacheManager().rebuild_all(force=True, on_cache_done=on_cache_done)
            vLog.info('✅ Discovery caches rebuilt')
            return True
        except Exception as e:
            vLog.error(f'❌ Failed to rebuild discovery caches: {e}')
            self.errors.append(f'Discovery cache rebuild failed: {e}')
            return False

    def _writes_production_archive(self) -> bool:
        """
        Check whether this importer renders into the production archive.

        Returns:
            True when data_dir is the configured processed-data directory
        """
        return is_production_archive(self.data_dir)

    def _print_summary(self):
        """Print processing summary"""
        vLog.info('\n' + '=' * 80)
        vLog.info('BAR RENDERING SUMMARY')
        vLog.info('=' * 80)
        vLog.info(f'✅ Processed Symbols: {self.processed_symbols}')
        vLog.info(f'✅ Rendered Bars: {self.total_bars_rendered:,}')
        vLog.info(f'❌ Errors: {len(self.errors)}')

        if self.errors:
            vLog.error('\nERROR LIST:')
            for error in self.errors:
                vLog.error(f'  - {error}')

        vLog.info('=' * 80 + '\n')
