"""
FiniexTestingIDE - Import Coordinator
One import over every archive: ticks first, then signals, then the shared caches (#175).

The coordinator owns the order and the reporting; the importers keep their own logic and are
called step by step. Before anything is written, both inboxes are scanned, so the screen shows
every step with its count and an estimate from the start. Every line the import logs also goes
to its own file, logs/imports/import_<import_id>.log, which the summary names at the end.
"""

import queue
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.bar_importer import (
    BarImporter,
    effective_render_workers,
    is_production_archive,
)
from python.data_management.importers.import_progress_tracker import ImportProgressTracker
from python.data_management.importers.signal_data_importer import SignalDataImporter
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.store.import_timings_store import ImportTimingsStore
from python.framework.types.activity_registry_types import ActivityKind
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportPaths,
    ImportRateUnit,
    ImportSection,
    ImportStepId,
    ImportStepStatus,
    InboxFile,
    InboxScan,
)
from python.framework.types.log_layout_types import IMPORT_LOG_SUBDIR
from python.framework.types.log_level import LogLevel
from python.framework.types.run_origin_types import RunChannel
from python.framework.utils.interrupt_utils import sigterm_as_interrupt
from python.framework.utils.run_id_utils import mint_run_id
from python.framework.utils.time_utils import format_duration
from python.system.ui.import_live_display import ImportLiveDisplay
from python.system.ui.ui_render_utils import BYTES_PER_MB, format_size

vLog = get_global_logger()

# The collector writes its metadata block first; broker_type sits well inside this many bytes.
_HEAD_BYTES = 8192
_BROKER_TYPE_PATTERN = re.compile(r'"broker_type"\s*:\s*"([^"]+)"')
_FRAME_QUEUE_SIZE = 8

# The exit code of `data_index_cli.py import` per outcome: 130 is the shell's code for Ctrl+C.
IMPORT_EXIT_CODES: Dict[ImportOutcome, int] = {
    ImportOutcome.COMPLETED: 0,
    ImportOutcome.COMPLETED_WITH_ERRORS: 1,
    ImportOutcome.INTERRUPTED: 130,
    ImportOutcome.FAILED: 1,
}


def import_paths_from_config() -> ImportPaths:
    """
    The paths of a production import, from import_config.json and app_config.json.

    Returns:
        ImportPaths
    """
    import_config = ImportConfigManager()
    move_files = import_config.get_move_processed_files()
    file_logging = AppConfigManager().get_file_logging_config_object()
    return ImportPaths(
        tick_source=Path(import_config.get_data_raw_path()),
        tick_target=Path(import_config.get_import_output_path()),
        tick_finished=Path(import_config.get_data_finished_path()) if move_files else None,
        signal_source=Path(import_config.get_signal_data_raw_path()),
        signal_target=Path(import_config.get_signal_import_output_path()),
        signal_finished=(Path(import_config.get_signal_data_finished_path())
                         if move_files else None),
        log_dir=Path(file_logging.global_log_dir) / IMPORT_LOG_SUBDIR,
        timings_file=Path(import_config.get_import_timings_path()),
    )


class ImportCoordinator:
    """
    Runs one import over the tick and signal inboxes.

    Args:
        paths: Where the import reads and writes
        override: Replace existing parquet files instead of skipping them
        only: Import one archive only (TICKS or SIGNALS), None for both
        include_finished: Also re-read the finished signal archive
        show_display: Ask for the live screen; it still stays off when the config switches it
            off or stdout is not a terminal
    """

    def __init__(
        self,
        paths: ImportPaths,
        override: bool = False,
        only: Optional[ImportSection] = None,
        include_finished: bool = False,
        show_display: bool = True,
        channel: RunChannel = RunChannel.DIRECT
    ):
        self._paths = paths
        # How the import was started, as its entry point declares it — for its entry in the list
        # of what runs on the machine (#603)
        self._channel = channel
        self._override = override
        self._only = only
        self._include_finished = include_finished
        self._import_config = ImportConfigManager()
        display_config = self._import_config.get_display_config()
        self._display_enabled = (show_display and display_config.enabled
                                 and sys.stdout.isatty())
        self._update_interval_s = display_config.update_interval_ms / 1000.0

        # The run-id form — readable UTC start plus a random half: two imports started in one
        # second would otherwise share one log file, and the second would overwrite the first
        self._import_id = mint_run_id(datetime.now(timezone.utc))
        self._log_path = paths.log_dir / f'import_{self._import_id}.log'
        self._timings = ImportTimingsStore(paths.timings_file)
        self._writes_production = is_production_archive(paths.tick_target)

        self._tick_importer: Optional[TickDataImporter] = None
        self._signal_importer: Optional[SignalDataImporter] = None
        self._bar_importer: Optional[BarImporter] = None
        self._tracker: Optional[ImportProgressTracker] = None
        self._frame_queue: Optional[queue.Queue] = None
        self._started = 0.0
        self._scan = InboxScan()
        # Entries of each counted list the tracker has already seen — what tells a NEW issue
        # apart, per list, so the pinned one is the newest even when lists grow out of order
        self._issues_seen: Dict[str, int] = {}
        # The signal files with the root each was found under, as the importer needs them
        self._signal_files: List[Tuple[Path, Path]] = []

    def get_log_path(self) -> Path:
        """
        The import's own log file.

        Returns:
            Its path
        """
        return self._log_path

    # =========================================================================
    # RUN
    # =========================================================================

    def run(self) -> ImportOutcome:
        """
        Run the whole import.

        A stop (Ctrl+C, SIGTERM) ends it as INTERRUPTED: the step that was running says so,
        the rest are skipped, and an interrupted render still rebuilds the bar index from the
        files that are there. A code crash ends it as FAILED and is raised after the summary.

        Returns:
            How it ended
        """
        # Listed as running for as long as it runs (#603), named by the id its log is named by
        with ActivityRegistry().running(ActivityKind.IMPORT, self._import_id,
                                        channel=self._channel):
            return self._run()

    def _run(self) -> ImportOutcome:
        """
        The import itself, while its activity is listed.

        Returns:
            How it ended
        """
        self._started = time.monotonic()
        if self._display_enabled:
            self._frame_queue = queue.Queue(maxsize=_FRAME_QUEUE_SIZE)
        self._tracker = ImportProgressTracker(
            import_id=self._import_id,
            log_path=str(self._log_path),
            rates=self._timings.load(),
            frame_queue=self._frame_queue,
            update_interval_s=self._update_interval_s,
        )

        outcome = ImportOutcome.FAILED
        crash: Optional[BaseException] = None
        display: Optional[ImportLiveDisplay] = None

        with vLog.tee_to(self._log_path):
            try:
                # A SIGTERM ends the import like Ctrl+C, so the running step says it was
                # interrupted and an interrupted render still rebuilds its index
                with sigterm_as_interrupt():
                    vLog.info(f'📥 Import {self._import_id} — log: {self._log_path}')
                    self._plan_steps()
                    self._tracker.mark_plan_complete()
                    if self._display_enabled:
                        display = ImportLiveDisplay(self._frame_queue, self._update_interval_s)
                        display.start()
                        vLog.set_console_muted(True)
                        self._tracker.start_refresh()
                    self._tracker.publish(force=True)
                    self._run_steps()
                    outcome = (ImportOutcome.COMPLETED_WITH_ERRORS if self._error_count()
                               else ImportOutcome.COMPLETED)
            except KeyboardInterrupt:
                outcome = ImportOutcome.INTERRUPTED
                self._tracker.finish_open_steps(ImportStepStatus.INTERRUPTED)
                vLog.warning(f'⚠️  Import {self._import_id} interrupted')
            except Exception as e:
                outcome = ImportOutcome.FAILED
                crash = e
                self._tracker.finish_open_steps(ImportStepStatus.FAILED)
                vLog.error(f'❌ Import {self._import_id} failed: {e}')
                vLog.error(traceback.format_exc())
            finally:
                self._tracker.stop_refresh()
                self._update_counters()
                self._tracker.set_outcome(outcome)
                if display is not None:
                    display.stop()
                vLog.set_console_muted(False)

            self._print_summary(outcome)
            self._record_timings(outcome)
            vLog.info(f'📝 Full log: {self._log_path}')

        if crash is not None:
            raise crash
        return outcome

    # =========================================================================
    # PLAN
    # =========================================================================

    def _plan_steps(self) -> None:
        """
        Build the importers, scan both inboxes, and lay out every step with its count and
        estimate. A section whose inbox is empty keeps its steps, marked skipped.

        Returns:
            None
        """
        tracker = self._tracker
        tracker.add_step(ImportStepId.SCAN, ImportSection.INBOXES, 'Scan import inboxes',
                         counter_unit='files')
        tracker.start_step(ImportStepId.SCAN)

        wants_ticks = self._only in (None, ImportSection.TICKS)
        wants_signals = self._only in (None, ImportSection.SIGNALS)
        renders_bars = wants_ticks and self._import_config.get_auto_render_bars()

        if wants_ticks:
            self._tick_importer = TickDataImporter(
                source_dir=str(self._paths.tick_source),
                target_dir=str(self._paths.tick_target),
                override=self._override,
                move_processed_files=self._paths.tick_finished is not None,
                finished_dir=(str(self._paths.tick_finished)
                              if self._paths.tick_finished is not None else None),
                auto_render_bars=False,
            )
            tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS,
                             'Import tick files', counter_unit='files')
            tracker.add_step(ImportStepId.TICKS_INDEX, ImportSection.TICKS,
                             'Rebuild tick index', counter_unit='files')
            tracker.add_step(ImportStepId.TICKS_ORDER, ImportSection.TICKS,
                             'Check archive order')
            if renders_bars:
                tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS,
                                 'Render bars', counter_unit='symbols')
                tracker.add_step(ImportStepId.BARS_INDEX, ImportSection.TICKS,
                                 'Rebuild bar index', counter_unit='files')
        if wants_signals:
            self._signal_importer = SignalDataImporter(
                source_dir=str(self._paths.signal_source),
                target_dir=str(self._paths.signal_target),
                override=self._override,
                finished_dir=(str(self._paths.signal_finished)
                              if self._paths.signal_finished is not None else None),
                include_finished=self._include_finished,
            )
            tracker.add_step(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS,
                             'Import signal files', counter_unit='files')
            tracker.add_step(ImportStepId.SIGNALS_INDEX, ImportSection.SIGNALS,
                             'Rebuild signal index', counter_unit='files')
        if renders_bars:
            tracker.add_step(ImportStepId.CACHES, ImportSection.SHARED,
                             'Rebuild discovery caches', counter_unit='caches')
            # The caches resolve their own paths and describe the production archive only;
            # an import scoped elsewhere says so instead of leaving the step out
            if not self._writes_production:
                tracker.skip_step(ImportStepId.CACHES, 'not the production archive')
        tracker.publish(force=True)

        scan = self._scan_inboxes()
        tick_bytes = sum(entry.size_bytes for entry in scan.tick_files)
        signal_bytes = sum(entry.size_bytes for entry in scan.signal_files)
        tracker.set_inbox(len(scan.tick_files), tick_bytes,
                          len(scan.signal_files), signal_bytes)
        total_files = len(scan.tick_files) + len(scan.signal_files)
        tracker.advance(ImportStepId.SCAN, total_files, total_files)
        self._show_scan_result(scan)
        tracker.finish_step(ImportStepId.SCAN)
        self._scan = scan

        if wants_ticks:
            self._plan_tick_quantities(scan, tick_bytes)
        if wants_signals:
            self._plan_signal_quantities(scan, signal_bytes)
        tracker.publish(force=True)

    def _scan_inboxes(self) -> InboxScan:
        """
        List both inboxes: every file's size, and for a tick file its broker from the metadata
        block at its head. Only for the screen and the estimate — the import
        itself parses every file in full.

        Returns:
            InboxScan
        """
        scan = InboxScan()
        if self._tick_importer is not None:
            for path in self._tick_importer.collect_files():
                scan.tick_files.append(InboxFile(
                    path=path, size_bytes=path.stat().st_size,
                    broker_type=_read_tick_broker_type(path)))
        if self._signal_importer is not None:
            self._signal_files = self._signal_importer.collect_files()
            for _, path in self._signal_files:
                scan.signal_files.append(InboxFile(path=path, size_bytes=path.stat().st_size))
        return scan

    def _show_scan_result(self, scan: InboxScan) -> None:
        """
        Write what the scan found beside the scan step, and skip what has nothing to do.

        Args:
            scan: The scan result

        Returns:
            None
        """
        per_broker: Dict[str, int] = {}
        for entry in scan.tick_files:
            per_broker[entry.broker_type or '?'] = per_broker.get(entry.broker_type or '?', 0) + 1
        parts = [f'{broker} {count}' for broker, count in sorted(per_broker.items())]
        if scan.signal_files:
            parts.append(f'signals {len(scan.signal_files)}')
        self._tracker.set_detail(ImportStepId.SCAN, ' · '.join(parts) or 'both inboxes empty')

        if self._tick_importer is not None and not scan.tick_files:
            self._skip_section(ImportSection.TICKS, 'inbox empty')
        if self._signal_importer is not None and not scan.signal_files:
            self._skip_section(ImportSection.SIGNALS, 'inbox empty')
        if not scan.tick_files and self._tracker.is_pending(ImportStepId.CACHES):
            self._tracker.skip_step(ImportStepId.CACHES, 'no bars rendered')

    def _skip_section(self, section: ImportSection, reason: str) -> None:
        """
        Mark every step of a section skipped.

        Args:
            section: The section
            reason: Shown beside each step

        Returns:
            None
        """
        for step_id in self._tracker.step_ids(section):
            self._tracker.skip_step(step_id, reason)

    def _plan_tick_quantities(self, scan: InboxScan, tick_bytes: int) -> None:
        """
        Declare what each tick step scales with, from the scan and the indexes already there.

        Args:
            scan: The scan result
            tick_bytes: Size of the tick files found

        Returns:
            None
        """
        tracker = self._tracker
        target = self._paths.tick_target
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB,
                             tick_bytes / BYTES_PER_MB)
        tracker.set_total(ImportStepId.TICKS_IMPORT, len(scan.tick_files))

        tick_files = _index_row_count(target / TickIndexManager.INDEX_FILE_PARQUET)
        tracker.set_quantity(ImportStepId.TICKS_INDEX, ImportRateUnit.FILE,
                             tick_files + len(scan.tick_files))
        tracker.set_quantity(ImportStepId.TICKS_ORDER, ImportRateUnit.FILE,
                             tick_files + len(scan.tick_files))

        if tracker.has_step(ImportStepId.BARS_RENDER):
            brokers = {entry.broker_type for entry in scan.tick_files if entry.broker_type}
            ticks, symbols = _ticks_and_symbols(
                target / TickIndexManager.INDEX_FILE_PARQUET, brokers)
            tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS,
                                 ticks / 1e6, workers=self._render_workers(symbols))
            tracker.set_total(ImportStepId.BARS_RENDER, symbols or None)
            bar_files = _index_row_count(target / BarsIndexManager.INDEX_FILE_PARQUET)
            tracker.set_quantity(ImportStepId.BARS_INDEX, ImportRateUnit.BAR_FILE, bar_files)
            if tracker.is_pending(ImportStepId.CACHES):
                tracker.set_quantity(ImportStepId.CACHES, ImportRateUnit.BAR_FILE, bar_files)

    def _plan_signal_quantities(self, scan: InboxScan, signal_bytes: int) -> None:
        """
        Declare what each signal step scales with.

        Args:
            scan: The scan result
            signal_bytes: Size of the signal files found

        Returns:
            None
        """
        tracker = self._tracker
        tracker.set_quantity(ImportStepId.SIGNALS_IMPORT, ImportRateUnit.MB,
                             signal_bytes / BYTES_PER_MB)
        tracker.set_total(ImportStepId.SIGNALS_IMPORT, len(scan.signal_files))
        signal_files = _distinct_paths(
            self._paths.signal_target / SignalIndexManager.INDEX_FILE_PARQUET)
        tracker.set_quantity(ImportStepId.SIGNALS_INDEX, ImportRateUnit.FILE,
                             signal_files + len(scan.signal_files))

    def _render_workers(self, symbols: int) -> int:
        """
        The worker count a render of this many symbols runs with.

        Args:
            symbols: Symbols to render

        Returns:
            Effective worker count
        """
        return effective_render_workers(self._import_config.get_bar_render_workers(), symbols)

    # =========================================================================
    # STEPS
    # =========================================================================

    def _run_steps(self) -> None:
        """
        Run every planned step in order.

        Returns:
            None
        """
        if self._tick_importer is not None and self._scan.tick_files:
            self._run_tick_steps()
        if self._signal_importer is not None and self._scan.signal_files:
            self._run_signal_steps()
        if (self._tracker.is_pending(ImportStepId.CACHES)
                and self._bar_importer is not None):
            self._run_cache_step()

    def _run_tick_steps(self) -> None:
        """
        Import the tick files, rebuild and check the tick index, render the bars.

        Returns:
            None
        """
        tracker = self._tracker
        importer = self._tick_importer
        files = self._scan.tick_files

        tracker.start_step(ImportStepId.TICKS_IMPORT, total=len(files))
        importer.log_header(len(files))
        for done, entry in enumerate(files):
            tracker.advance(ImportStepId.TICKS_IMPORT, done,
                            current_item=f'{entry.broker_type or "?"}/{entry.path.name} '
                                         f'({format_size(entry.size_bytes)})')
            importer.import_file(entry.path)
            self._update_counters()
        tracker.advance(ImportStepId.TICKS_IMPORT, len(files))
        tracker.finish_step(ImportStepId.TICKS_IMPORT)

        tracker.start_step(ImportStepId.TICKS_INDEX)
        errors_before = len(importer.errors)
        index_manager = importer.rebuild_parquet_index(
            on_file_scanned=lambda done, total: tracker.advance(
                ImportStepId.TICKS_INDEX, done, total))
        self._update_counters()
        # A file the scan could not read fails the step, while the index it built — without
        # that file — still serves the order check and the render
        tracker.finish_step(ImportStepId.TICKS_INDEX, _status_after(
            index_manager is not None and len(importer.errors) == errors_before))

        if index_manager is None:
            tracker.skip_step(ImportStepId.TICKS_ORDER, 'no tick index')
        else:
            tracker.start_step(ImportStepId.TICKS_ORDER)
            importer.validate_archive_ordering(index_manager)
            self._update_counters()
            tracker.finish_step(ImportStepId.TICKS_ORDER)

        if not tracker.has_step(ImportStepId.BARS_RENDER):
            return
        if importer.processed_files == 0:
            tracker.skip_step(ImportStepId.BARS_RENDER, 'no file imported')
            tracker.skip_step(ImportStepId.BARS_INDEX, 'no file imported')
            if tracker.is_pending(ImportStepId.CACHES):
                tracker.skip_step(ImportStepId.CACHES, 'no bars rendered')
            return
        self._run_render_steps(index_manager)

    def _run_render_steps(self, index_manager: Optional[TickIndexManager]) -> None:
        """
        Render the bars of the imported broker types, then rebuild the bar index — the index
        also when the render was interrupted, so it describes the files that are there. A step
        that counted errors ends FAILED: its time measured less than the whole work, so it
        must not become the stored rate either.

        Args:
            index_manager: The rebuilt tick index, for the exact tick count

        Returns:
            None
        """
        tracker = self._tracker
        brokers = self._tick_importer.get_processed_broker_types()
        if index_manager is not None:
            ticks, symbols = _ticks_and_symbols_from_index(index_manager, brokers)
            tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS,
                                 ticks / 1e6, workers=self._render_workers(symbols))
        self._bar_importer = BarImporter(data_dir=str(self._paths.tick_target))
        symbol_count = sum(len(self._bar_importer.tick_index.list_symbols(bt)) for bt in brokers)

        tracker.start_step(ImportStepId.BARS_RENDER, total=symbol_count)
        errors_before = len(self._bar_importer.errors)
        try:
            self._bar_importer.render_bars_for_all_symbols(
                broker_types=brokers,
                on_symbol_done=tracker.on_symbol_done,
                on_worker_progress=tracker.on_worker_progress,
            )
            self._update_counters()
            tracker.finish_step(ImportStepId.BARS_RENDER, _status_after(
                len(self._bar_importer.errors) == errors_before))
        except KeyboardInterrupt:
            tracker.finish_step(ImportStepId.BARS_RENDER, ImportStepStatus.INTERRUPTED)
            raise
        finally:
            tracker.start_step(ImportStepId.BARS_INDEX)
            indexed = self._bar_importer.update_bar_index(
                on_file_scanned=lambda done, total: tracker.advance(
                    ImportStepId.BARS_INDEX, done, total))
            self._update_counters()
            tracker.finish_step(ImportStepId.BARS_INDEX, _status_after(indexed))
            # The caches read the bar index: over a failed one they would describe stale bars
            if not indexed and tracker.is_pending(ImportStepId.CACHES):
                tracker.skip_step(ImportStepId.CACHES, 'bar index not rebuilt')

    def _run_signal_steps(self) -> None:
        """
        Import the signal files and rebuild the signal index.

        Returns:
            None
        """
        tracker = self._tracker
        importer = self._signal_importer
        files = self._signal_files

        tracker.start_step(ImportStepId.SIGNALS_IMPORT, total=len(files))
        importer.log_header(len(files))
        for done, (root, path) in enumerate(files):
            tracker.advance(ImportStepId.SIGNALS_IMPORT, done,
                            current_item=str(path.relative_to(root)))
            importer.import_file(root, path)
            self._update_counters()
        tracker.advance(ImportStepId.SIGNALS_IMPORT, len(files))
        importer.prune_empty_source_dirs()
        tracker.finish_step(ImportStepId.SIGNALS_IMPORT)

        tracker.start_step(ImportStepId.SIGNALS_INDEX)
        indexed = importer.rebuild_index(
            on_file_scanned=lambda done, total: tracker.advance(
                ImportStepId.SIGNALS_INDEX, done, total))
        self._update_counters()
        tracker.finish_step(ImportStepId.SIGNALS_INDEX, _status_after(indexed))

    def _run_cache_step(self) -> None:
        """
        Rebuild the discovery caches once, after every archive is written.

        Returns:
            None
        """
        tracker = self._tracker
        tracker.start_step(ImportStepId.CACHES, total=None)
        rebuilt = self._bar_importer.rebuild_discovery_caches(
            on_cache_done=lambda done, total: tracker.advance(ImportStepId.CACHES, done, total))
        self._update_counters()
        tracker.finish_step(ImportStepId.CACHES, _status_after(rebuilt))

    # =========================================================================
    # COUNTERS, SUMMARY, TIMINGS
    # =========================================================================

    def _warnings(self) -> List[str]:
        """
        Every warning the importers counted.

        Returns:
            Warning texts, ticks first
        """
        warnings: List[str] = []
        if self._tick_importer is not None:
            warnings.extend(self._tick_importer.warnings)
        if self._bar_importer is not None:
            warnings.extend(self._bar_importer.warnings)
        if self._signal_importer is not None:
            warnings.extend(self._signal_importer.warnings)
        return warnings

    def _errors(self) -> List[str]:
        """
        Every error the importers counted, render errors included.

        Returns:
            Error texts, ticks first
        """
        errors: List[str] = []
        if self._tick_importer is not None:
            errors.extend(self._tick_importer.errors)
        if self._bar_importer is not None:
            errors.extend(self._bar_importer.errors)
        if self._signal_importer is not None:
            errors.extend(self._signal_importer.errors)
        return errors

    def _error_count(self) -> int:
        """
        How many errors the import counted.

        Returns:
            The count
        """
        return len(self._errors())

    def _update_counters(self) -> None:
        """
        Copy the importers' counters into the tracker, and pin the newest warning or error
        they counted since the last call — an error before a warning.

        Returns:
            None
        """
        ticks = self._tick_importer.total_ticks if self._tick_importer is not None else 0
        rows = self._signal_importer.total_rows if self._signal_importer is not None else 0
        self._tracker.set_written(ticks, rows)
        self._tracker.set_issue_counts(len(self._warnings()), len(self._errors()))

        # Per list, not over the joined lists: the caches write into the bar importer's
        # errors after the signal steps ran, so the joined list's last entry is not the newest
        newest: Optional[Tuple[LogLevel, str]] = None
        for key, level, entries in self._issue_lists():
            if (len(entries) > self._issues_seen.get(key, 0)
                    and (newest is None or level == LogLevel.ERROR
                         or newest[0] != LogLevel.ERROR)):
                newest = (level, entries[-1])
            self._issues_seen[key] = len(entries)
        if newest is not None:
            self._tracker.note_issue(*newest)

    def _issue_lists(self) -> List[Tuple[str, LogLevel, List[str]]]:
        """
        Every list of counted issues, in step order.

        Returns:
            (name, level, entries) per list
        """
        lists: List[Tuple[str, LogLevel, List[str]]] = []
        for name, importer in (('ticks', self._tick_importer), ('bars', self._bar_importer),
                               ('signals', self._signal_importer)):
            if importer is not None:
                lists.append((f'{name}.warnings', LogLevel.WARNING, importer.warnings))
                lists.append((f'{name}.errors', LogLevel.ERROR, importer.errors))
        return lists

    def _print_summary(self, outcome: ImportOutcome) -> None:
        """
        Log the import's summary: one line per archive, every warning and error.

        Args:
            outcome: How the import ended

        Returns:
            None
        """
        elapsed = time.monotonic() - self._started
        vLog.info('\n' + '=' * 80)
        vLog.info(f'IMPORT SUMMARY — {self._import_id} · {outcome.value} · '
                  f'{format_duration(elapsed)}')
        vLog.info('=' * 80)
        if self._tick_importer is not None:
            vLog.info(f'✅ Ticks:   {self._tick_importer.processed_files} file(s) processed · '
                      f'{self._tick_importer.total_ticks:,} ticks')
            for broker_type, offset_counts in self._tick_importer.get_applied_offsets().items():
                counts_str = ' · '.join(
                    f'{offset}h × {count} file(s)'
                    for offset, count in sorted(offset_counts.items()))
                vLog.info(f'✅ UTC offsets applied: {broker_type}: {counts_str}')
        if self._bar_importer is not None:
            vLog.info(f'✅ Bars:    {self._bar_importer.processed_symbols} symbol(s) · '
                      f'{self._bar_importer.total_bars_rendered:,} bars')
        if self._signal_importer is not None:
            vLog.info(f'✅ Signals: {self._signal_importer.processed_files} file(s) · '
                      f'{self._signal_importer.total_rows:,} rows · '
                      f'{self._signal_importer.moved_files} archived')
        warnings = self._warnings()
        errors = self._errors()
        vLog.info(f'⚠️  Warnings: {len(warnings)}')
        vLog.info(f'❌ Errors: {len(errors)}')
        if warnings:
            vLog.warning('\nWARNING LIST:')
            for warning in warnings:
                vLog.warning(f'  - {warning}')
        if errors:
            vLog.error('\nERROR LIST:')
            for error in errors:
                vLog.error(f'  - {error}')
        self._print_estimate_comparison()
        vLog.info('=' * 80)

    def _print_estimate_comparison(self) -> None:
        """
        Log each step's estimate at the start against what it took — how well the stored rates
        predicted this import. Nothing is printed when no step had an estimate.

        Returns:
            None
        """
        timings, total = self._tracker.estimate_comparison()
        if all(timing.estimate_s is None for timing in timings):
            return
        vLog.info('\n⏱  Estimated at the start → took:')
        for timing in timings + ([total] if total is not None else []):
            estimate = ('—' if timing.estimate_s is None
                        else f'~{format_duration(timing.estimate_s)}')
            actual = '—' if timing.actual_s is None else format_duration(timing.actual_s)
            deviation = ('' if timing.deviation_percent is None
                         else f'{timing.deviation_percent:+.0f} %')
            vLog.info(f'   {timing.label:<26} {estimate:>10}  →  {actual:<10} {deviation}')

    def _record_timings(self, outcome: ImportOutcome) -> None:
        """
        Store the step rates of a complete import into the production archive — only those
        describe the next import's work. An interrupted or scoped import stores nothing.

        Args:
            outcome: How the import ended

        Returns:
            None
        """
        if outcome not in (ImportOutcome.COMPLETED, ImportOutcome.COMPLETED_WITH_ERRORS):
            return
        if not self._writes_production:
            return
        measured = self._tracker.measured_rates()
        if measured:
            self._timings.record(measured)


def _status_after(succeeded: bool) -> ImportStepStatus:
    """
    How a step that ran to its end finished.

    Args:
        succeeded: Whether it did its work without a counted error

    Returns:
        DONE or FAILED
    """
    return ImportStepStatus.DONE if succeeded else ImportStepStatus.FAILED


def _read_tick_broker_type(path: Path) -> Optional[str]:
    """
    The broker type from the metadata block at the head of a collector file.

    Args:
        path: The tick JSON file

    Returns:
        The broker type, None when the head does not show it
    """
    try:
        with open(path, 'rb') as stream:
            head = stream.read(_HEAD_BYTES).decode('utf-8', errors='replace')
    except OSError:
        return None
    match = _BROKER_TYPE_PATTERN.search(head)
    return match.group(1) if match else None


def _index_row_count(index_file: Path) -> int:
    """
    Rows of an index file, read from its footer.

    Args:
        index_file: The index parquet

    Returns:
        Row count, 0 when there is no readable index
    """
    try:
        return pq.ParquetFile(index_file).metadata.num_rows
    except (OSError, ValueError):
        return 0


def _distinct_paths(index_file: Path) -> int:
    """
    Distinct data files an index describes (the signal index has one row per file and symbol).

    Args:
        index_file: The index parquet

    Returns:
        File count, 0 when there is no readable index
    """
    try:
        return int(pd.read_parquet(index_file, columns=['path'])['path'].nunique())
    except (OSError, ValueError, KeyError):
        return 0


def _ticks_and_symbols(index_file: Path, brokers: set) -> Tuple[int, int]:
    """
    Ticks and symbols the tick index holds for some broker types — the render's work, as
    known before the import adds to it.

    Args:
        index_file: The tick index parquet
        brokers: Broker types to count

    Returns:
        (tick count, symbol count), zeros without a readable index
    """
    try:
        frame = pd.read_parquet(index_file, columns=['broker_type', 'symbol', 'tick_count'])
    except (OSError, ValueError, KeyError):
        return 0, 0
    frame = frame[frame['broker_type'].isin(brokers)]
    return int(frame['tick_count'].sum()), int(frame.groupby(['broker_type', 'symbol']).ngroups)


def _ticks_and_symbols_from_index(index_manager: TickIndexManager,
                                  brokers: List[str]) -> Tuple[int, int]:
    """
    Ticks and symbols the rebuilt tick index holds for some broker types.

    Args:
        index_manager: The rebuilt tick index
        brokers: Broker types to count

    Returns:
        (tick count, symbol count)
    """
    ticks = 0
    symbols = 0
    for broker_type in brokers:
        for symbol in index_manager.list_symbols(broker_type):
            symbols += 1
            ticks += sum(int(entry.get('tick_count', 0))
                         for entry in index_manager.get_symbol_entries(broker_type, symbol))
    return ticks, symbols
