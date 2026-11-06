"""
Tick Inbox Tests (#120)

Every import drains the tick inbox. A file the archive took — written, or skipped by the
duplicate check — goes to the finished directory; a file the import refused or failed on goes to
the refused directory, unchanged, with a note of why beside it. A zip is only the packaging: it is
unpacked in a step of its own, its files are imported like loose ones, and the zip moves on
unchanged — to the finished directory when anything in it was of use, to the refused directory
when nothing was or when it cannot be imported at all. A zip stays in the inbox only when what
stopped it lay outside it, a disk too small for its files.
"""

import hashlib
import json
import struct
import zipfile
from collections import namedtuple
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from python.data_management.importers import tick_zip_inbox
from python.data_management.importers.import_coordinator import ImportCoordinator
from python.data_management.importers.tick_inbox_conveyor import REFUSAL_NOTE_SUFFIX
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportPaths,
    ImportRateUnit,
    ImportStepId,
    ImportStepStatus,
)
from python.framework.types.import_types.tick_inbox_types import TickImportOutcome
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture

_DiskUsage = namedtuple('_DiskUsage', 'total used free')


def _paths(root: Path) -> ImportPaths:
    """An import tree under a temporary root."""
    return ImportPaths(
        tick_source=root / 'raw',
        tick_target=root / 'processed',
        tick_finished=root / 'finished',
        tick_unpacking=root / 'unpacking',
        tick_refused=root / 'refused',
        signal_source=root / 'raw' / 'signals',
        signal_target=root / 'processed' / 'signals',
        signal_finished=root / 'finished' / 'signals',
        log_dir=root / 'logs' / 'imports',
        timings_file=root / 'runtime' / 'import_timings.json',
    )


def _export_name(symbol: str, start_time: str) -> str:
    """The collector's file name for an export starting at start_time."""
    stamp = start_time.replace('.', '').replace(':', '').replace(' ', '_')
    return f'{symbol}_{stamp}_ticks.json'


def _export_text(symbol: str, start_time: str) -> str:
    """A minimal valid export, as text."""
    return json.dumps(build_minimal_tick_json(symbol=symbol, start_time=start_time))


def _loose(paths: ImportPaths, symbol: str, start_time: str) -> Path:
    """A valid loose export in the inbox."""
    return write_json_fixture(paths.tick_source, _export_name(symbol, start_time),
                              build_minimal_tick_json(symbol=symbol, start_time=start_time))


def _zip(path: Path, members: Dict[str, str],
         compression: int = zipfile.ZIP_DEFLATED) -> Path:
    """Write a zip of name → text members."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def _tick_zip(paths: ImportPaths, zip_name: str, *exports: tuple,
              compression: int = zipfile.ZIP_DEFLATED) -> Path:
    """A zip in the inbox holding one valid export per (symbol, start_time)."""
    members = {_export_name(symbol, start): _export_text(symbol, start)
               for symbol, start in exports}
    return _zip(paths.tick_source / zip_name, members, compression)


def _as_deflate64(zip_path: Path) -> None:
    """
    Relabel a Deflate zip as Deflate64. zipfile_deflate64 reads that method but cannot write it,
    and a Deflate stream with no match of the longest length (258) and a window within 32 KB
    is also a valid Deflate64 stream — so only the method field of each header changes.
    """
    raw = bytearray(zip_path.read_bytes())
    with zipfile.ZipFile(zip_path) as archive:
        local_headers = [info.header_offset for info in archive.infolist()]
    for offset in local_headers:
        struct.pack_into('<H', raw, offset + 8, zipfile.ZIP_DEFLATED64)
    end_record = raw.rfind(b'PK\x05\x06')
    entries, _, position = struct.unpack_from('<HII', raw, end_record + 10)
    for _ in range(entries):
        struct.pack_into('<H', raw, position + 10, zipfile.ZIP_DEFLATED64)
        name_length, extra_length, comment_length = struct.unpack_from('<HHH', raw, position + 28)
        position += 46 + name_length + extra_length + comment_length
    zip_path.write_bytes(bytes(raw))


def _tick_files(paths: ImportPaths) -> List[Path]:
    """The tick parquets the import wrote, bars left out."""
    return sorted(paths.tick_target.glob('*/ticks/*/*.parquet'))


def _note(paths: ImportPaths, name: str) -> str:
    """The refusal note beside a refused file."""
    return (paths.tick_refused / f'{name}{REFUSAL_NOTE_SUFFIX}').read_text(encoding='utf-8')


def _inbox(paths: ImportPaths) -> List[str]:
    """What is left in the tick inbox, the signal inbox below it aside."""
    return sorted(path.name for path in paths.tick_source.iterdir()
                  if path.name != 'signals') if paths.tick_source.exists() else []


def _sha256(path: Path) -> str:
    """A file's SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(paths: ImportPaths, override: bool = False) -> ImportCoordinator:
    """One import over the tree, without the screen."""
    coordinator = ImportCoordinator(paths=paths, override=override, show_display=False)
    coordinator.run()
    return coordinator


def _status(coordinator: ImportCoordinator, step_id: ImportStepId) -> Optional[ImportStepStatus]:
    """The final status of one step."""
    for step in coordinator._tracker.snapshot().steps:
        if step.step_id == step_id:
            return step.status
    return None


def _outcome(coordinator: ImportCoordinator) -> Optional[ImportOutcome]:
    """How the import ended."""
    return coordinator._tracker.snapshot().outcome


def _log(coordinator: ImportCoordinator) -> str:
    """The import's own log file, which holds its summary with every warning and error."""
    return coordinator.get_log_path().read_text(encoding='utf-8')


class TestTheOutcomes:
    """What the archive holds once an export's import ended."""

    def test_taken_means_in_the_archive(self):
        taken = {outcome for outcome in TickImportOutcome if outcome.is_taken()}
        assert taken == {TickImportOutcome.IMPORTED, TickImportOutcome.ALREADY_ARCHIVED}


class TestLooseFilesLeaveTheInbox:
    """A loose export leaves the inbox whatever its import's outcome."""

    def test_a_refused_file_goes_to_refused_with_its_reason(self, tmp_path):
        paths = _paths(tmp_path)
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _run(paths)
        # Starts inside the file just imported: the archive refuses the overlap
        refused = _loose(paths, 'AAAUSD', '2026.01.15 10:00:02')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.tick_refused / refused.name).exists()
        assert 'Overlaps the archive' in _note(paths, refused.name)
        assert f'Refused: 1 file(s) moved to {paths.tick_refused}' in _log(coordinator)

    def test_a_file_that_cannot_be_read_goes_to_refused(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        (paths.tick_source).mkdir(parents=True)
        (paths.tick_source / name).write_text('not an export', encoding='utf-8')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.tick_refused / name).read_text(encoding='utf-8') == 'not an export'
        assert 'JSONDecodeError' in _note(paths, name)

    def test_a_duplicate_is_done_and_leaves_the_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        first = _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        original = first.read_bytes()
        _run(paths)
        (paths.tick_source / first.name).write_bytes(original)

        coordinator = _run(paths)

        # Counted as a warning, as before; the identical file in finished/ makes the copy go
        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert 'DUPLICATE DETECTED' in _log(coordinator)
        assert _inbox(paths) == []
        assert (paths.tick_finished / first.name).read_bytes() == original

    def test_a_different_file_of_the_same_name_is_never_overwritten(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        paths.tick_finished.mkdir(parents=True)
        (paths.tick_finished / name).write_text('another file of that name', encoding='utf-8')
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == [name]
        assert (paths.tick_finished / name).read_text(encoding='utf-8') == \
            'another file of that name'
        assert 'never overwritten' in _log(coordinator)


class TestAZipIsImported:
    """Every file of a zip lands in the archive, and the zip moves on unchanged."""

    def test_its_files_are_imported_and_the_zip_moves_to_finished(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'),
                             ('BBBUSD', '2026.01.15 10:00:00'))
        digest = _sha256(zip_path)

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 2
        assert _inbox(paths) == []
        assert _sha256(paths.tick_finished / 'week45.zip') == digest
        # The zip holds the files, so no loose copy goes to finished/
        assert not list(paths.tick_finished.glob('*_ticks.json'))
        assert not (paths.tick_unpacking / 'week45').exists()
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.DONE

    def test_loose_files_and_a_zip_in_one_import(self, tmp_path):
        paths = _paths(tmp_path)
        loose = _loose(paths, 'CCCUSD', '2026.01.15 10:00:00')
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 2
        assert (paths.tick_finished / loose.name).exists()
        assert (paths.tick_finished / 'week45.zip').exists()

    def test_a_deflate64_zip_is_read(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _tick_zip(paths, 'deflate64.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        _as_deflate64(zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            assert {info.compress_type for info in archive.infolist()} == {
                zipfile.ZIP_DEFLATED64}

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 1
        assert (paths.tick_finished / 'deflate64.zip').exists()

    def test_the_unpacking_is_its_own_step_timed_by_the_size_of_the_zips(self, tmp_path):
        paths = _paths(tmp_path)
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        rates = coordinator._tracker.measured_rates()
        assert rates[ImportStepId.TICKS_UNPACK].unit == ImportRateUnit.MB
        scan = next(step for step in coordinator._tracker.snapshot().steps
                    if step.step_id == ImportStepId.SCAN)
        assert '1 zip(s), 1 of the files inside' in scan.detail


class TestAZipWithFilesTheImportRefuses:
    """A refused file of a zip goes to refused/; the zip goes where its other files decide."""

    def test_the_zip_moves_on_and_the_refused_file_goes_to_refused(self, tmp_path):
        paths = _paths(tmp_path)
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _run(paths)
        refused_name = _export_name('AAAUSD', '2026.01.15 10:00:02')
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:02'),
                  ('BBBUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.tick_finished / 'week45.zip').exists()
        assert (paths.tick_refused / refused_name).exists()
        note = _note(paths, refused_name)
        assert 'Overlaps the archive' in note and 'week45.zip' in note
        assert not (paths.tick_unpacking / 'week45').exists()
        assert len(_tick_files(paths)) == 2

    def test_a_zip_with_nothing_of_use_goes_to_refused(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        _zip(paths.tick_source / 'broken.zip', {name: 'not an export'})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.tick_refused / 'broken.zip').exists()
        # The zip holds the file, so its copy goes rather than lying in refused/ twice
        assert not (paths.tick_refused / name).exists()
        assert name in _note(paths, 'broken.zip')
        assert not (paths.tick_unpacking / 'broken').exists()


class TestAZipFedAgain:
    """Files the archive holds already are done: a zip holding them still moves on."""

    def test_the_same_zip_twice(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        original = zip_path.read_bytes()
        _run(paths)
        zip_path.write_bytes(original)

        again = _run(paths)

        assert _outcome(again) == ImportOutcome.COMPLETED
        assert 'DUPLICATE DETECTED' in _log(again)
        # An identical zip lies in finished/ already, so the inbox copy goes
        assert _inbox(paths) == []
        assert (paths.tick_finished / 'week45.zip').read_bytes() == original

    def test_a_package_holding_files_the_archive_has_already(self, tmp_path):
        paths = _paths(tmp_path)
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _run(paths)
        _tick_zip(paths, 'first_package.zip', ('AAAUSD', '2026.01.15 10:00:00'),
                  ('BBBUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert _inbox(paths) == []
        assert (paths.tick_finished / 'first_package.zip').exists()
        assert len(_tick_files(paths)) == 2

    def test_override_re_imports_the_files_of_a_zip(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        original = zip_path.read_bytes()
        _run(paths)
        zip_path.write_bytes(original)

        coordinator = _run(paths, override=True)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert 'DUPLICATE DETECTED' not in _log(coordinator)
        assert _inbox(paths) == []

    def test_a_different_zip_of_the_same_name_in_finished_is_never_overwritten(self, tmp_path):
        paths = _paths(tmp_path)
        _zip(paths.tick_finished / 'week45.zip', {'other.txt': 'something else'})
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == ['week45.zip']
        with zipfile.ZipFile(paths.tick_finished / 'week45.zip') as archive:
            assert archive.namelist() == ['other.txt']


class TestAZipRefusedAsAWhole:
    """Nothing of a zip is written when its files cannot be imported as they stand."""

    @pytest.mark.parametrize('members', [
        {_export_name('AAAUSD', '2026.01.15 10:00:00'): _export_text('AAAUSD', '2026.01.15 10:00:00'),
         'notes.txt': 'not a tick export'},
        {'nested/' + _export_name('AAAUSD', '2026.01.15 10:00:00'):
            _export_text('AAAUSD', '2026.01.15 10:00:00')},
    ], ids=['a foreign file', 'a file in a folder'])
    def test_a_zip_holding_anything_but_flat_tick_exports(self, tmp_path, members):
        paths = _paths(tmp_path)
        _zip(paths.tick_source / 'mixed.zip', members)

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.tick_refused / 'mixed.zip').exists()
        assert not _tick_files(paths)
        assert 'ZIP REFUSED mixed.zip' in _log(coordinator)

    def test_a_name_that_also_lies_loose_in_the_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        loose = _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _zip(paths.tick_source / 'week45.zip',
             {loose.name: _export_text('AAAUSD', '2026.01.15 10:00:00')})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        # The loose file is imported; the zip is refused untouched
        assert len(_tick_files(paths)) == 1
        assert (paths.tick_finished / loose.name).exists()
        assert (paths.tick_refused / 'week45.zip').exists()
        assert 'also lies loose' in _note(paths, 'week45.zip')

    def test_a_name_twice_in_one_zip(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        zip_path = paths.tick_source / 'twice.zip'
        zip_path.parent.mkdir(parents=True)
        with pytest.warns(UserWarning):
            with zipfile.ZipFile(zip_path, 'w') as archive:
                archive.writestr(name, _export_text('AAAUSD', '2026.01.15 10:00:00'))
                archive.writestr(name, _export_text('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert (paths.tick_refused / 'twice.zip').exists()
        assert not _tick_files(paths)
        assert 'occurs twice' in _log(coordinator)

    def test_a_damaged_file_refuses_the_zip_at_the_unpacking(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _tick_zip(paths, 'damaged.zip', ('AAAUSD', '2026.01.15 10:00:00'),
                             ('BBBUSD', '2026.01.15 10:00:00'), compression=zipfile.ZIP_STORED)
        # Stored, so the text lies in the zip as it is: change one byte of the last member's
        # ticks and its CRC no longer matches
        raw = bytearray(zip_path.read_bytes())
        position = raw.rfind(b'"bid"')
        raw[position + 1:position + 4] = b'bix'
        zip_path.write_bytes(bytes(raw))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.FAILED
        assert (paths.tick_refused / 'damaged.zip').exists()
        assert not _tick_files(paths)
        assert not (paths.tick_unpacking / 'damaged').exists()

    def test_a_zip_too_large_for_the_disk_waits_in_the_inbox(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        monkeypatch.setattr(tick_zip_inbox.shutil, 'disk_usage',
                            lambda path: _DiskUsage(total=100, used=99, free=1))

        coordinator = _run(paths)

        # Not the zip's fault: it stays for the next import, nothing refused
        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.FAILED
        assert _inbox(paths) == ['week45.zip']
        assert not paths.tick_refused.exists()
        assert 'GB unpacked' in _log(coordinator)
