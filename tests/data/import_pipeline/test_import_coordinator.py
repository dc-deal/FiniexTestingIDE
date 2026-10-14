"""
Import Coordinator Tests (#175)

One import over both inboxes: what it plans from the scan, what it writes, what its own log
file holds, how a stop in the middle of the bar render leaves the archive, and when the
measured step durations are stored.
"""

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List

import pyarrow.parquet as pq
import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers import bar_importer, import_coordinator
from python.data_management.importers.import_coordinator import (
    IMPORT_EXIT_CODES,
    ImportCoordinator,
)
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportPaths,
    ImportSection,
    ImportStepId,
    ImportStepStatus,
)
from python.framework.utils.interrupt_utils import sigterm_as_interrupt
from python.framework.utils.live_frame_serialization_utils import frame_to_json
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture

SIGNAL_FIXTURE = (Path(__file__).resolve().parents[2]
                  / 'fixtures' / 'signals' / 'signal_import_sample.jsonl')
SIGNAL_SOURCE = 'test_sentiment'


def _paths(root: Path) -> ImportPaths:
    """An import tree under a temporary root."""
    return ImportPaths(
        tick_source=root / 'raw',
        tick_target=root / 'processed',
        tick_finished=root / 'finished',
        signal_source=root / 'raw' / 'signals',
        signal_target=root / 'processed' / 'signals',
        signal_finished=root / 'finished' / 'signals',
        log_dir=root / 'logs' / 'imports',
        timings_file=root / 'runtime' / 'import_timings.json',
    )


def _add_ticks(paths: ImportPaths, symbol: str, start_time: str, tick_count: int = 30) -> Path:
    """Drop one tick export for a symbol into the inbox."""
    data = build_minimal_tick_json(symbol=symbol, start_time=start_time, tick_count=tick_count)
    stamp = start_time.replace('.', '').replace(':', '').replace(' ', '_')
    return write_json_fixture(paths.tick_source, f'{symbol}_{stamp}_ticks.json', data)


def _add_signals(paths: ImportPaths) -> Path:
    """Drop the signal fixture JSONL into the signal inbox."""
    target = paths.signal_source / SIGNAL_SOURCE / SIGNAL_FIXTURE.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SIGNAL_FIXTURE, target)
    return target


def _step_status(coordinator: ImportCoordinator) -> Dict[ImportStepId, ImportStepStatus]:
    """The final status of every planned step."""
    frame = coordinator._tracker.snapshot()
    return {step.step_id: step.status for step in frame.steps}


def _step(coordinator: ImportCoordinator, step_id: ImportStepId):
    """One step of the final frame."""
    return next(step for step in coordinator._tracker.snapshot().steps
                if step.step_id == step_id)


class TestBothArchives:
    """A plain import of both inboxes."""

    def test_imports_ticks_and_signals_and_names_its_log(self, tmp_path):
        paths = _paths(tmp_path)
        tick_file = _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        signal_file = _add_signals(paths)

        coordinator = ImportCoordinator(paths=paths, show_display=False)
        outcome = coordinator.run()

        assert outcome == ImportOutcome.COMPLETED
        assert not tick_file.exists() and (paths.tick_finished / tick_file.name).exists()
        assert not signal_file.exists()
        assert (paths.signal_target / SIGNAL_SOURCE / f'{SIGNAL_FIXTURE.stem}.parquet').exists()
        assert list((paths.tick_target / 'kraken_spot' / 'bars' / 'AAAUSD').glob('*_BARS.parquet'))

        statuses = _step_status(coordinator)
        # Scoped to a temporary tree, the import must not touch the production caches — and
        # says so on the screen rather than leaving the step out
        assert statuses.pop(ImportStepId.CACHES) == ImportStepStatus.SKIPPED
        caches = _step(coordinator, ImportStepId.CACHES)
        assert caches.detail == 'not the production archive'
        assert all(status == ImportStepStatus.DONE for status in statuses.values()), statuses

        log_text = coordinator.get_log_path().read_text(encoding='utf-8')
        assert 'Tick Data Importer' in log_text
        assert 'Signal Data Importer' in log_text
        assert 'IMPORT SUMMARY' in log_text
        assert f'Full log: {coordinator.get_log_path()}' in log_text

    def test_frame_serializes_to_json(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        coordinator = ImportCoordinator(paths=paths, show_display=False)
        coordinator.run()

        encoded = frame_to_json(coordinator._tracker.snapshot())
        json.dumps(encoded)
        assert encoded['outcome'] == ImportOutcome.COMPLETED.value
        assert encoded['steps'][0]['step_id'] == ImportStepId.SCAN.value

    def test_scan_names_the_broker_from_the_file_head(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _add_signals(paths)
        coordinator = ImportCoordinator(paths=paths, show_display=False)
        coordinator.run()

        scan = next(step for step in coordinator._tracker.snapshot().steps
                    if step.step_id == ImportStepId.SCAN)
        assert scan.detail == 'kraken_spot 1 · signals 1'
        assert (scan.done, scan.total) == (2, 2)


class TestPinnedIssue:
    """The bottom line shows the newest COUNTED issue, never a log line the counters ignore."""

    def test_a_duplicate_is_counted_and_pinned(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        assert ImportCoordinator(paths=paths, show_display=False).run() \
            == ImportOutcome.COMPLETED
        # The same file again: a duplicate without --override is skipped as a warning
        shutil.copy(next(paths.tick_finished.glob('AAAUSD_*_ticks.json')), paths.tick_source)

        coordinator = ImportCoordinator(paths=paths, show_display=False)
        coordinator.run()

        frame = coordinator._tracker.snapshot()
        assert frame.warning_count == 1
        assert frame.last_message.text.startswith('DUPLICATE DETECTED in AAAUSD_')

    def test_an_override_run_pins_nothing(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        ImportCoordinator(paths=paths, show_display=False).run()
        shutil.copy(next(paths.tick_finished.glob('AAAUSD_*_ticks.json')), paths.tick_source)

        coordinator = ImportCoordinator(paths=paths, override=True, show_display=False)
        coordinator.run()

        # Override logs a warning per replaced file, but counts none — the line stays empty
        frame = coordinator._tracker.snapshot()
        assert frame.warning_count == 0
        assert frame.last_message is None


class TestOnlyOneArchive:
    """`--only` imports one inbox and leaves the other untouched."""

    def test_only_ticks_leaves_the_signal_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        signal_file = _add_signals(paths)

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.TICKS,
                                        show_display=False)
        assert coordinator.run() == ImportOutcome.COMPLETED

        assert signal_file.exists()
        assert ImportStepId.SIGNALS_IMPORT not in _step_status(coordinator)

    def test_only_signals_leaves_the_tick_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        tick_file = _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _add_signals(paths)

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)
        assert coordinator.run() == ImportOutcome.COMPLETED

        assert tick_file.exists()
        assert ImportStepId.TICKS_IMPORT not in _step_status(coordinator)


class TestEmptyInboxes:
    """An inbox with nothing in it keeps its steps, marked skipped."""

    def test_nothing_to_import(self, tmp_path):
        paths = _paths(tmp_path)
        coordinator = ImportCoordinator(paths=paths, show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED
        statuses = _step_status(coordinator)
        assert statuses.pop(ImportStepId.SCAN) == ImportStepStatus.DONE
        assert statuses and all(status == ImportStepStatus.SKIPPED
                                for status in statuses.values())


class TestInterruptedRender:
    """A stop in the middle of the render keeps the bars it did not reach."""

    def test_unreached_symbol_keeps_its_previous_bars(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _add_ticks(paths, 'BBBUSD', '2026.01.15 10:00:00')
        assert ImportCoordinator(paths=paths, show_display=False).run() \
            == ImportOutcome.COMPLETED

        bars_root = paths.tick_target / 'kraken_spot' / 'bars'
        before = {path: path.stat().st_mtime_ns for path in bars_root.glob('*/*_BARS.parquet')}
        assert before

        _add_ticks(paths, 'AAAUSD', '2026.01.16 10:00:00')
        _add_ticks(paths, 'BBBUSD', '2026.01.16 10:00:00')
        # A new symbol rendered before the stop: only a REBUILT index can list it
        _add_ticks(paths, 'AABUSD', '2026.01.16 10:00:00')

        real_worker = bar_importer._render_symbol_worker

        def worker_stopped_at_bbb(symbol, *args, **kwargs):
            if symbol == 'BBBUSD':
                raise KeyboardInterrupt
            return real_worker(symbol, *args, **kwargs)

        monkeypatch.setattr(bar_importer, '_render_symbol_worker', worker_stopped_at_bbb)
        monkeypatch.setattr(ImportConfigManager, 'get_bar_render_workers', lambda self: 1)

        coordinator = ImportCoordinator(paths=paths, show_display=False)
        outcome = coordinator.run()

        assert outcome == ImportOutcome.INTERRUPTED
        assert IMPORT_EXIT_CODES[outcome] == 130
        statuses = _step_status(coordinator)
        assert statuses[ImportStepId.BARS_RENDER] == ImportStepStatus.INTERRUPTED
        # The index is rebuilt even after the stop, so it describes the files that are there
        assert statuses[ImportStepId.BARS_INDEX] == ImportStepStatus.DONE

        for path, mtime in before.items():
            assert path.exists(), f'{path.name} was deleted by an interrupted render'
            if 'BBBUSD' in path.name:
                assert path.stat().st_mtime_ns == mtime
            else:
                assert path.stat().st_mtime_ns > mtime
        assert not list(bars_root.glob('**/*.partial'))

        index = BarsIndexManager(data_dir=str(paths.tick_target))
        index.build_index()
        assert set(index.list_symbols('kraken_spot')) == {'AAAUSD', 'AABUSD', 'BBBUSD'}

        log_text = coordinator.get_log_path().read_text(encoding='utf-8')
        assert 'Bar render INTERRUPTED after 2 of 3 symbols' in log_text
        assert 'kraken_spot/BBBUSD' in log_text
        # Counted, so the summary lists it and the screen pins it — a live screen holds the
        # console while the render runs, and a line that is only logged never reaches it
        frame = coordinator._tracker.snapshot()
        assert frame.warning_count == 1
        assert frame.last_message.text.startswith('Bar render INTERRUPTED')
        assert 'WARNING LIST' in log_text.split('IMPORT SUMMARY')[1]


class TestFailedSteps:
    """A step that did not do its work says so, counts an error, and stores no rate."""

    def test_a_failed_bar_index_is_failed_and_measures_nothing(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')

        def broken_build(self, *args, **kwargs):
            raise OSError('disk gone')

        monkeypatch.setattr(BarsIndexManager, 'build_index', broken_build)
        coordinator = ImportCoordinator(paths=paths, only=ImportSection.TICKS,
                                        show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _step_status(coordinator)[ImportStepId.BARS_INDEX] == ImportStepStatus.FAILED
        assert ImportStepId.BARS_INDEX not in coordinator._tracker.measured_rates()
        assert coordinator._tracker.snapshot().last_message.text.startswith(
            'Bar index update failed')

    def test_the_caches_are_not_rebuilt_over_a_failed_bar_index(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)
        rebuilt: List[bool] = []

        def broken_build(self, *args, **kwargs):
            raise OSError('disk gone')

        monkeypatch.setattr(BarsIndexManager, 'build_index', broken_build)
        monkeypatch.setattr(import_coordinator.BarImporter, 'rebuild_discovery_caches',
                            lambda self, on_cache_done=None: rebuilt.append(True) or True)
        coordinator = ImportCoordinator(paths=paths, only=ImportSection.TICKS,
                                        show_display=False)
        coordinator.run()

        assert not rebuilt
        assert _step(coordinator, ImportStepId.CACHES).detail == 'bar index not rebuilt'

    def test_an_unreadable_bar_file_counts_as_an_error(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        # Another broker's bar file, which this import does not render again
        broken = paths.tick_target / 'mt5' / 'bars' / 'EURUSD' / 'EURUSD_M5_BARS.parquet'
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b'not a parquet file')

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.TICKS,
                                        show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _step_status(coordinator)[ImportStepId.BARS_INDEX] == ImportStepStatus.FAILED
        assert 'could not be read' in coordinator._tracker.snapshot().last_message.text

    def test_an_unreadable_tick_file_counts_as_an_error(self, tmp_path):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        broken = paths.tick_target / 'kraken_spot' / 'ticks' / 'ZZZUSD' / 'ZZZUSD_x.parquet'
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b'not a parquet file')

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.TICKS,
                                        show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED_WITH_ERRORS
        statuses = _step_status(coordinator)
        assert statuses[ImportStepId.TICKS_INDEX] == ImportStepStatus.FAILED
        # The index it built without that file still serves the steps after it
        assert statuses[ImportStepId.BARS_RENDER] == ImportStepStatus.DONE
        assert 'could not be read' in coordinator._tracker.snapshot().last_message.text

    def test_an_unreadable_signal_file_counts_as_an_error(self, tmp_path):
        paths = _paths(tmp_path)
        _add_signals(paths)
        broken = paths.signal_target / SIGNAL_SOURCE / 'broken.parquet'
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b'not a parquet file')

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _step_status(coordinator)[ImportStepId.SIGNALS_INDEX] == ImportStepStatus.FAILED
        assert 'could not be read' in coordinator._tracker.snapshot().last_message.text

    def test_a_failed_signal_index_is_failed(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_signals(paths)

        def broken_build(self, *args, **kwargs):
            raise OSError('disk gone')

        monkeypatch.setattr(import_coordinator.SignalIndexManager, 'build_index', broken_build)
        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)

        assert coordinator.run() == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _step_status(coordinator)[ImportStepId.SIGNALS_INDEX] == ImportStepStatus.FAILED
        assert ImportStepId.SIGNALS_INDEX not in coordinator._tracker.measured_rates()

    def test_the_newest_error_is_pinned_even_from_an_earlier_list(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _add_signals(paths)
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)

        def failing_signal_import(self, root, path):
            self.errors.append(f'ERROR in {path.name}: unreadable')

        def failing_caches(self, on_cache_done=None):
            # The caches write into the BAR importer's list, after the signal steps ran
            self.errors.append('Discovery cache rebuild failed: boom')
            return False

        monkeypatch.setattr(import_coordinator.SignalDataImporter, 'import_file',
                            failing_signal_import)
        monkeypatch.setattr(import_coordinator.BarImporter, 'rebuild_discovery_caches',
                            failing_caches)
        coordinator = ImportCoordinator(paths=paths, show_display=False)
        coordinator.run()

        frame = coordinator._tracker.snapshot()
        assert frame.error_count == 2
        assert frame.last_message.text == 'Discovery cache rebuild failed: boom'
        assert _step_status(coordinator)[ImportStepId.CACHES] == ImportStepStatus.FAILED


class _RecordingDisplay:
    """Stands in for the live screen: records that it ran, renders nothing."""

    instances: List['_RecordingDisplay'] = []

    def __init__(self, frame_queue, update_interval_s):
        self.started = False
        self.stopped = False
        _RecordingDisplay.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TestWithTheScreen:
    """The default path from a terminal: screen on, console muted, summary printed after."""

    def test_the_summary_reaches_the_console_and_the_run_lines_do_not(
            self, tmp_path, monkeypatch, capsys):
        paths = _paths(tmp_path)
        _add_signals(paths)
        _RecordingDisplay.instances.clear()
        monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
        monkeypatch.setattr(import_coordinator, 'ImportLiveDisplay', _RecordingDisplay)

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS)
        assert coordinator.run() == ImportOutcome.COMPLETED

        display = _RecordingDisplay.instances[-1]
        assert display.started and display.stopped
        out = capsys.readouterr().out
        # Logged while the screen ran: in the file, not on the console it held
        assert 'Signal Data Importer' not in out
        assert 'Signal Data Importer' in coordinator.get_log_path().read_text(encoding='utf-8')
        # The summary comes after the screen stopped
        assert 'IMPORT SUMMARY' in out and 'Full log' in out


class TestStops:
    """A crash and a SIGTERM end the import cleanly and leave nothing installed behind."""

    def test_a_crash_is_raised_after_the_summary_and_stores_nothing(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_signals(paths)
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)

        def crash(self, root, path):
            raise RuntimeError('a code defect')

        monkeypatch.setattr(import_coordinator.SignalDataImporter, 'import_file', crash)
        previous = signal.getsignal(signal.SIGTERM)
        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)

        with pytest.raises(RuntimeError, match='a code defect'):
            coordinator.run()
        assert signal.getsignal(signal.SIGTERM) == previous
        assert not paths.timings_file.exists()
        statuses = _step_status(coordinator)
        assert statuses[ImportStepId.SIGNALS_IMPORT] == ImportStepStatus.FAILED
        assert statuses[ImportStepId.SIGNALS_INDEX] == ImportStepStatus.SKIPPED
        assert 'IMPORT SUMMARY' in coordinator.get_log_path().read_text(encoding='utf-8')

    def test_sigterm_ends_the_import_as_interrupted(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_signals(paths)
        received: List[int] = []
        # Stands in for the default action, which would end the test process: if the import
        # did not install its own handler, the signal lands here and the import COMPLETES
        previous = signal.signal(signal.SIGTERM, lambda signum, frame: received.append(signum))
        try:
            def terminated(self, *args, **kwargs):
                os.kill(os.getpid(), signal.SIGTERM)
                return True

            monkeypatch.setattr(import_coordinator.SignalDataImporter, 'rebuild_index',
                                terminated)
            coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                            show_display=False)
            installed_before = signal.getsignal(signal.SIGTERM)

            assert coordinator.run() == ImportOutcome.INTERRUPTED
            assert not received
            assert signal.getsignal(signal.SIGTERM) is installed_before
            assert (_step_status(coordinator)[ImportStepId.SIGNALS_INDEX]
                    == ImportStepStatus.INTERRUPTED)
        finally:
            signal.signal(signal.SIGTERM, previous)


class TestSigtermBlock:
    """The block the import and a standalone render run under: SIGTERM becomes a stop."""

    def test_sigterm_inside_the_block_is_a_stop_and_the_handler_returns(self):
        received: List[int] = []
        previous = signal.signal(signal.SIGTERM, lambda signum, frame: received.append(signum))
        try:
            installed = signal.getsignal(signal.SIGTERM)
            with pytest.raises(KeyboardInterrupt):
                with sigterm_as_interrupt():
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(1.0)
            assert not received
            assert signal.getsignal(signal.SIGTERM) is installed
        finally:
            signal.signal(signal.SIGTERM, previous)


class TestTimings:
    """The measured step durations are stored after a complete production import only."""

    def test_scoped_import_stores_nothing(self, tmp_path):
        paths = _paths(tmp_path)
        _add_signals(paths)
        ImportCoordinator(paths=paths, show_display=False).run()
        assert not paths.timings_file.exists()

    def test_complete_production_import_stores_rates(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_signals(paths)
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)
        assert coordinator.run() == ImportOutcome.COMPLETED

        stored = json.loads(paths.timings_file.read_text(encoding='utf-8'))
        assert ImportStepId.SIGNALS_IMPORT.value in stored['rates']
        assert stored['rates'][ImportStepId.SIGNALS_IMPORT.value]['unit'] == 'mb'

    def test_the_next_import_compares_its_estimate_with_the_duration(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)
        _add_signals(paths)
        ImportCoordinator(paths=paths, only=ImportSection.SIGNALS, override=True,
                          show_display=False).run()
        _add_signals(paths)

        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS, override=True,
                                        show_display=False)
        coordinator.run()

        log_text = coordinator.get_log_path().read_text(encoding='utf-8')
        assert 'Estimated at the start' in log_text
        assert 'Import signal files' in log_text.split('Estimated at the start')[1]

    def test_interrupted_import_stores_nothing(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _add_signals(paths)
        monkeypatch.setattr(import_coordinator, 'is_production_archive', lambda path: True)

        def stop(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(
            import_coordinator.SignalDataImporter, 'rebuild_index', stop)
        coordinator = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                        show_display=False)
        assert coordinator.run() == ImportOutcome.INTERRUPTED
        assert not paths.timings_file.exists()


class TestExitCodes:
    """Every outcome has an exit code."""

    @pytest.mark.parametrize('outcome', list(ImportOutcome))
    def test_every_outcome_is_mapped(self, outcome):
        assert outcome in IMPORT_EXIT_CODES

    def test_errors_are_not_reported_as_success(self, tmp_path):
        paths = _paths(tmp_path)
        broken = paths.tick_source / 'BROKEN_20260115_100000_ticks.json'
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text('{"metadata": {}, "ticks": [{"bid": 1}]}', encoding='utf-8')

        outcome = ImportCoordinator(paths=paths, show_display=False).run()
        assert outcome == ImportOutcome.COMPLETED_WITH_ERRORS
        assert IMPORT_EXIT_CODES[outcome] != 0


def test_bar_files_carry_the_render_after_a_complete_import(tmp_path):
    """A complete import leaves only finished bar files, each readable in full."""
    paths = _paths(tmp_path)
    _add_ticks(paths, 'AAAUSD', '2026.01.15 10:00:00')
    ImportCoordinator(paths=paths, show_display=False).run()

    bar_files = list((paths.tick_target / 'kraken_spot' / 'bars').glob('**/*'))
    assert bar_files
    assert not [path for path in bar_files if path.suffix == '.partial']
    for path in bar_files:
        if path.is_file():
            pq.read_table(path)
