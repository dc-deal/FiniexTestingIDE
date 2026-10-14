"""
Bar Render Replace Tests (#175, #442)

A render replaces each bar file only once its new version is verified, removes files
without a source only after every symbol rendered, reports each worker's progress while it
runs, and names an abruptly ended worker for what it most likely was.
"""

import hashlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import List

import psutil
import pyarrow.parquet as pq
import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers import bar_importer
from python.data_management.importers.bar_importer import BarImporter
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.framework.exceptions.data_quality_errors import BarFileVerificationException
from python.framework.types.import_types.import_progress_types import (
    BarRenderPhase,
    BarRenderProgress,
)
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture


@pytest.fixture
def tick_tree(tmp_path) -> Path:
    """A processed tree with ticks for two kraken_spot symbols and no bars yet."""
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    for symbol in ('AAAUSD', 'BBBUSD'):
        write_json_fixture(source, f'{symbol}_20260115_100000_ticks.json',
                           build_minimal_tick_json(symbol=symbol, tick_count=40))
    TickDataImporter(source_dir=str(source), target_dir=str(target),
                     move_processed_files=False, auto_render_bars=False).process_all_exports()
    return target


def _bar_files(target: Path) -> List[Path]:
    return sorted((target / 'kraken_spot' / 'bars').glob('*/*_BARS.parquet'))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# A parent that starts a pool with the render's own initializer, notes its workers and kills
# itself the way no handler can see — what a debugger's hard stop or `kill -9` does.
_KILLED_PARENT = '''
import os, signal, sys, time
from concurrent.futures import ProcessPoolExecutor
import psutil
from python.data_management.importers.bar_importer import _ignore_interrupt_signals

if __name__ == '__main__':
    executor = ProcessPoolExecutor(max_workers=2, initializer=_ignore_interrupt_signals)
    executor.submit(os.getpid).result()
    for _ in range(4):
        executor.submit(time.sleep, 30)
    pids = [child.pid for child in psutil.Process().children()]
    with open(sys.argv[1], 'w') as stream:
        stream.write(' '.join(str(pid) for pid in pids))
    os.kill(os.getpid(), signal.SIGKILL)
'''


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class TestReplaceAndClean:
    """Nothing is deleted before it has been rendered again."""

    def test_render_replaces_files_and_leaves_no_partial(self, tick_tree):
        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        first = {path: path.stat().st_mtime_ns for path in _bar_files(tick_tree)}

        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])

        assert first
        for path, mtime in first.items():
            assert path.stat().st_mtime_ns > mtime
        assert not list(tick_tree.glob('**/*.partial'))

    def test_a_file_that_fails_verification_never_replaces_the_good_one(
            self, tick_tree, monkeypatch):
        monkeypatch.setattr(ImportConfigManager, 'get_bar_render_workers', lambda self: 1)
        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        kept = tick_tree / 'kraken_spot' / 'bars' / 'AAAUSD' / 'AAAUSD_M5_BARS.parquet'
        before = _sha256(kept)
        real_verify = bar_importer._verify_bar_file

        def verify_fails_for_m5(filepath, expected_rows):
            if filepath.name.startswith('AAAUSD_M5_'):
                raise BarFileVerificationException(f'{filepath.name} reads back short')
            real_verify(filepath, expected_rows)

        monkeypatch.setattr(bar_importer, '_verify_bar_file', verify_fails_for_m5)
        importer = BarImporter(data_dir=str(tick_tree))
        importer.render_bars_for_all_symbols(['kraken_spot'])

        # A direct overwrite would have put the unverified file in its place
        assert _sha256(kept) == before
        assert pq.read_table(kept).num_rows > 0
        assert any('AAAUSD' in error for error in importer.errors)

    def test_leftover_partial_is_removed_at_start(self, tick_tree):
        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        # A timeframe the render does not write: only the start-of-render cleanup removes it
        leftover = (tick_tree / 'kraken_spot' / 'bars' / 'AAAUSD'
                    / 'AAAUSD_M2_BARS.parquet.partial')
        leftover.write_bytes(b'half written')

        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        assert not leftover.exists()

    def test_files_without_a_source_go_after_a_complete_render(self, tick_tree):
        bars = tick_tree / 'kraken_spot' / 'bars'
        orphan_symbol = bars / 'ZZZUSD' / 'ZZZUSD_M1_BARS.parquet'
        orphan_symbol.parent.mkdir(parents=True)
        orphan_symbol.write_bytes(b'')
        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        unconfigured = bars / 'AAAUSD' / 'AAAUSD_M2_BARS.parquet'
        unconfigured.write_bytes(b'')

        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])

        assert not orphan_symbol.parent.exists()
        assert not unconfigured.exists()
        assert (bars / 'AAAUSD' / 'AAAUSD_M1_BARS.parquet').exists()

    def test_bars_whose_ticks_are_on_disk_are_kept_when_the_index_misses_them(self, tick_tree):
        BarImporter(data_dir=str(tick_tree)).render_bars_for_all_symbols(['kraken_spot'])
        importer = BarImporter(data_dir=str(tick_tree))
        # A tick index that lost a symbol (stale, or its files unreadable at the rebuild)
        del importer.tick_index.index['kraken_spot']['BBBUSD']
        kept = _bar_files(tick_tree)
        kept = [path for path in kept if path.parent.name == 'BBBUSD']

        importer.render_bars_for_all_symbols(['kraken_spot'])

        assert kept and all(path.exists() for path in kept)
        assert any('Kept the bars of kraken_spot/BBBUSD' in warning
                   for warning in importer.warnings)

    def test_an_interrupted_render_removes_nothing(self, tick_tree, monkeypatch):
        bars = tick_tree / 'kraken_spot' / 'bars'
        orphan = bars / 'ZZZUSD' / 'ZZZUSD_M1_BARS.parquet'
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b'')

        importer = BarImporter(data_dir=str(tick_tree))

        def stop(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(importer, '_render_sequential', stop)
        monkeypatch.setattr(importer, '_render_parallel', stop)
        with pytest.raises(KeyboardInterrupt):
            importer.render_bars_for_all_symbols(['kraken_spot'])
        assert orphan.exists()


class TestParallelRender:
    """Two workers, each reporting its progress while it runs."""

    def test_workers_report_progress_and_each_symbol_is_counted(self, tick_tree, monkeypatch):
        monkeypatch.setattr(ImportConfigManager, 'get_bar_render_workers', lambda self: 2)
        events: List[BarRenderProgress] = []
        done: List[str] = []

        importer = BarImporter(data_dir=str(tick_tree))
        importer.render_bars_for_all_symbols(
            ['kraken_spot'],
            on_symbol_done=lambda finished, total, item: done.append(item),
            on_worker_progress=events.append,
        )

        assert sorted(done) == ['kraken_spot/AAAUSD', 'kraken_spot/BBBUSD']
        assert importer.processed_symbols == 2 and not importer.errors
        assert {event.symbol for event in events} == {'AAAUSD', 'BBBUSD'}
        assert {event.phase for event in events} == set(BarRenderPhase)
        assert all(event.rss_bytes > 0 for event in events)
        assert len(_bar_files(tick_tree)) == 2 * len(ImportConfigManager().get_render_timeframes())


class TestBarIndexUpdate:
    """The bar index rebuild says whether it describes every bar file on disk."""

    def test_an_unreadable_bar_file_is_counted(self, tick_tree):
        importer = BarImporter(data_dir=str(tick_tree))
        importer.render_bars_for_all_symbols(['kraken_spot'])
        assert importer.update_bar_index() is True and not importer.errors

        broken = _bar_files(tick_tree)[0]
        broken.write_bytes(b'not a parquet file')
        assert importer.update_bar_index() is False
        assert len(importer.errors) == 1
        assert 'could not be read' in importer.errors[0]
        assert '--clean' not in importer.errors[0]


class TestWorkerLifetime:
    """A render's workers never outlive the process that started them."""

    @pytest.mark.skipif(not sys.platform.startswith('linux'), reason='the OS request is Linux only')
    def test_workers_end_when_their_parent_is_killed(self, tmp_path):
        script = tmp_path / 'killed_parent.py'
        script.write_text(textwrap.dedent(_KILLED_PARENT), encoding='utf-8')
        pids_file = tmp_path / 'worker_pids'
        subprocess.run([sys.executable, str(script), str(pids_file)], cwd=PROJECT_ROOT,
                       env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT)}, timeout=120)
        pids = [int(pid) for pid in pids_file.read_text().split()]
        try:
            assert pids
            deadline = time.monotonic() + 10.0
            while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(0.1)
            # Without the request they would sleep their 30 s and then wait under PID 1 forever
            assert not [pid for pid in pids if _alive(pid)]
        finally:
            for pid in pids:
                if _alive(pid):
                    os.kill(pid, signal.SIGKILL)


class TestCrashDiagnosis:
    """An abruptly ended worker is named for what it most likely was (#442)."""

    def test_broken_pool_names_memory_and_the_setting(self, tick_tree):
        importer = BarImporter(data_dir=str(tick_tree))
        future: Future = Future()
        future.set_exception(BrokenProcessPool('A process in the process pool was terminated'))

        result = importer._collect_result(future, 'kraken_spot', 'AAAUSD', max_workers=2)

        assert not result.success
        assert 'ran out of memory' in result.error_message
        assert 'bar_render_workers' in result.error_message
        assert '(now 2)' in result.error_message
