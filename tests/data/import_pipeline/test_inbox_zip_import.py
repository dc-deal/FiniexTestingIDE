"""
Import Inbox Tests (#120)

Every import drains the one import inbox, tick exports and signal files alike. A file the archive took — written, or skipped by the
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

from python.data_management.importers import import_zip_inbox
from python.data_management.importers.import_coordinator import ImportCoordinator
from python.data_management.importers.import_inbox_conveyor import REFUSAL_NOTE_SUFFIX
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportSection,
    ImportPaths,
    ImportRateUnit,
    ImportStepId,
    ImportStepStatus,
)
from python.framework.types.import_types.import_inbox_types import InboxImportOutcome
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture

_DiskUsage = namedtuple('_DiskUsage', 'total used free')


def _paths(root: Path) -> ImportPaths:
    """An import tree under a temporary root."""
    return ImportPaths(
        inbox=root / 'raw',
        tick_target=root / 'processed',
        finished=root / 'finished',
        unpacking=root / 'unpacking',
        refused=root / 'refused',
        signal_target=root / 'processed' / 'signals',
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
    return write_json_fixture(paths.inbox, _export_name(symbol, start_time),
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
    return _zip(paths.inbox / zip_name, members, compression)


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
    return (paths.refused / f'{name}{REFUSAL_NOTE_SUFFIX}').read_text(encoding='utf-8')


def _inbox(paths: ImportPaths) -> List[str]:
    """What is left in the inbox."""
    return sorted(path.name for path in paths.inbox.iterdir()) if paths.inbox.exists() else []


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
        taken = {outcome for outcome in InboxImportOutcome if outcome.is_taken()}
        assert taken == {InboxImportOutcome.IMPORTED, InboxImportOutcome.ALREADY_ARCHIVED}


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
        assert (paths.refused / refused.name).exists()
        assert 'Overlaps the archive' in _note(paths, refused.name)
        assert f'Refused: 1 file(s) moved to {paths.refused}' in _log(coordinator)

    def test_a_file_that_cannot_be_read_goes_to_refused(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        (paths.inbox).mkdir(parents=True)
        (paths.inbox / name).write_text('not an export', encoding='utf-8')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.refused / name).read_text(encoding='utf-8') == 'not an export'
        assert 'JSONDecodeError' in _note(paths, name)

    def test_a_duplicate_is_done_and_leaves_the_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        first = _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        original = first.read_bytes()
        _run(paths)
        (paths.inbox / first.name).write_bytes(original)

        coordinator = _run(paths)

        # Counted as a warning, as before; the identical file in finished/ makes the copy go
        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert 'DUPLICATE DETECTED' in _log(coordinator)
        assert _inbox(paths) == []
        assert (paths.finished / first.name).read_bytes() == original

    def test_a_different_file_of_the_same_name_is_never_overwritten(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        paths.finished.mkdir(parents=True)
        (paths.finished / name).write_text('another file of that name', encoding='utf-8')
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == [name]
        assert (paths.finished / name).read_text(encoding='utf-8') == \
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
        assert _sha256(paths.finished / 'week45.zip') == digest
        # The zip holds the files, so no loose copy goes to finished/
        assert not list(paths.finished.glob('*_ticks.json'))
        # The unpacking directory goes with its last folder: left empty, it reads as unhomed data
        assert not paths.unpacking.exists()
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.DONE

    def test_the_unpacking_directory_stays_while_another_zips_folder_is_in_it(self, tmp_path):
        paths = _paths(tmp_path)
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        leftover = paths.unpacking / 'week44' / 'left_by_a_crash.json'
        leftover.parent.mkdir(parents=True)
        leftover.write_text('{}', encoding='utf-8')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert not (paths.unpacking / 'week45').exists()
        assert leftover.exists()

    def test_loose_files_and_a_zip_in_one_import(self, tmp_path):
        paths = _paths(tmp_path)
        loose = _loose(paths, 'CCCUSD', '2026.01.15 10:00:00')
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 2
        assert (paths.finished / loose.name).exists()
        assert (paths.finished / 'week45.zip').exists()

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
        assert (paths.finished / 'deflate64.zip').exists()

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
        assert (paths.finished / 'week45.zip').exists()
        assert (paths.refused / refused_name).exists()
        note = _note(paths, refused_name)
        assert 'Overlaps the archive' in note and 'week45.zip' in note
        assert not paths.unpacking.exists()
        assert len(_tick_files(paths)) == 2

    def test_a_zip_with_nothing_of_use_goes_to_refused(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        _zip(paths.inbox / 'broken.zip', {name: 'not an export'})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.refused / 'broken.zip').exists()
        # The zip holds the file, so its copy goes rather than lying in refused/ twice
        assert not (paths.refused / name).exists()
        assert name in _note(paths, 'broken.zip')
        assert not paths.unpacking.exists()


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
        assert (paths.finished / 'week45.zip').read_bytes() == original

    def test_a_package_holding_files_the_archive_has_already(self, tmp_path):
        paths = _paths(tmp_path)
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _run(paths)
        _tick_zip(paths, 'first_package.zip', ('AAAUSD', '2026.01.15 10:00:00'),
                  ('BBBUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert _inbox(paths) == []
        assert (paths.finished / 'first_package.zip').exists()
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
        _zip(paths.finished / 'week45.zip', {'other.txt': 'something else'})
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == ['week45.zip']
        with zipfile.ZipFile(paths.finished / 'week45.zip') as archive:
            assert archive.namelist() == ['other.txt']


class TestAZipRefusedAsAWhole:
    """Nothing of a zip is written when its files cannot be imported as they stand."""

    @pytest.mark.parametrize('members', [
        {_export_name('AAAUSD', '2026.01.15 10:00:00'): _export_text('AAAUSD', '2026.01.15 10:00:00'),
         'notes.txt': 'not a tick export'},
        {'nested/' + _export_name('AAAUSD', '2026.01.15 10:00:00'):
            _export_text('AAAUSD', '2026.01.15 10:00:00')},
    ], ids=['a foreign file', 'a file in a folder'])
    def test_a_zip_holding_anything_but_flat_raw_files(self, tmp_path, members):
        paths = _paths(tmp_path)
        _zip(paths.inbox / 'mixed.zip', members)

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _inbox(paths) == []
        assert (paths.refused / 'mixed.zip').exists()
        assert not _tick_files(paths)
        assert 'ZIP REFUSED mixed.zip' in _log(coordinator)

    def test_a_name_that_also_lies_loose_in_the_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        loose = _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _zip(paths.inbox / 'week45.zip',
             {loose.name: _export_text('AAAUSD', '2026.01.15 10:00:00')})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        # The loose file is imported; the zip is refused untouched
        assert len(_tick_files(paths)) == 1
        assert (paths.finished / loose.name).exists()
        assert (paths.refused / 'week45.zip').exists()
        assert 'also lies loose' in _note(paths, 'week45.zip')

    def test_a_name_twice_in_one_zip(self, tmp_path):
        paths = _paths(tmp_path)
        name = _export_name('AAAUSD', '2026.01.15 10:00:00')
        zip_path = paths.inbox / 'twice.zip'
        zip_path.parent.mkdir(parents=True)
        with pytest.warns(UserWarning):
            with zipfile.ZipFile(zip_path, 'w') as archive:
                archive.writestr(name, _export_text('AAAUSD', '2026.01.15 10:00:00'))
                archive.writestr(name, _export_text('AAAUSD', '2026.01.15 10:00:00'))

        coordinator = _run(paths)

        assert (paths.refused / 'twice.zip').exists()
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
        assert (paths.refused / 'damaged.zip').exists()
        assert not _tick_files(paths)
        assert not paths.unpacking.exists()

    def test_a_zip_too_large_for_the_disk_waits_in_the_inbox(self, tmp_path, monkeypatch):
        paths = _paths(tmp_path)
        _tick_zip(paths, 'week45.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        monkeypatch.setattr(import_zip_inbox.shutil, 'disk_usage',
                            lambda path: _DiskUsage(total=100, used=99, free=1))

        coordinator = _run(paths)

        # Not the zip's fault: it stays for the next import, nothing refused
        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.FAILED
        assert _inbox(paths) == ['week45.zip']
        assert not paths.refused.exists()
        assert 'GB unpacked' in _log(coordinator)


# ---------------------------------------------------------------- signal files in the inbox

_SIGNAL_MSC = 1768464000000


def _envelope_line(pipeline_id: str, offset: int = 0) -> str:
    """One minimal envelope of a pipeline, as a JSONL line."""
    return json.dumps({'collected_msc': _SIGNAL_MSC + offset, 'schema_version': '1.0',
                       'pipeline_id': pipeline_id, 'status': 'success', 'result': []})


def _signal_zip(paths: ImportPaths, zip_name: str, pipeline_id: str, *days: str) -> Path:
    """A zip of one pipeline's day files, flat, as a signal package holds them."""
    return _zip(paths.inbox / zip_name,
                {f'{day}.jsonl': _envelope_line(pipeline_id) for day in days})


def _signal_files(paths: ImportPaths, pipeline_id: str) -> List[str]:
    """The signal parquets the import wrote for one pipeline."""
    folder = paths.signal_target / pipeline_id
    return sorted(path.name for path in folder.glob('*.parquet')) if folder.exists() else []


class TestASignalZip:
    """A zip of one pipeline's signal files goes through the signal import, and moves on like a
    tick zip does."""

    def test_its_files_reach_the_signal_archive_and_the_zip_moves_to_finished(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _signal_zip(paths, 'pipe_a-0001-20261115.zip', 'pipe_a',
                               '2026-11-10', '2026-11-11')
        digest = _sha256(zip_path)

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        # The pipeline comes from the envelopes: the zip's members carry no folder
        assert _signal_files(paths, 'pipe_a') == ['2026-11-10.parquet', '2026-11-11.parquet']
        assert _inbox(paths) == []
        assert _sha256(paths.finished / 'pipe_a-0001-20261115.zip') == digest
        assert not list(paths.finished.glob('*.jsonl'))
        assert not paths.unpacking.exists()
        assert _status(coordinator, ImportStepId.SIGNALS_UNPACK) == ImportStepStatus.DONE
        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.SKIPPED

    def test_two_pipelines_may_name_their_files_after_the_same_day(self, tmp_path):
        paths = _paths(tmp_path)
        _signal_zip(paths, 'pipe_a-0001-20261115.zip', 'pipe_a', '2026-11-10')
        _signal_zip(paths, 'pipe_b-0001-20261115.zip', 'pipe_b', '2026-11-10')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert _signal_files(paths, 'pipe_a') == ['2026-11-10.parquet']
        assert _signal_files(paths, 'pipe_b') == ['2026-11-10.parquet']
        assert _inbox(paths) == []

    def test_members_named_by_pipeline_and_day_write_the_parquet_of_their_day(self, tmp_path):
        """A producer's package names its members `<pipeline_id>_<YYYY-MM-DD>.jsonl`. A member
        whose name gives another pipeline than its envelopes is refused; the rest is imported."""
        paths = _paths(tmp_path)
        _zip(paths.inbox / 'pipe_a-0001-20261115.zip',
             {'pipe_a_2026-11-10.jsonl': _envelope_line('pipe_a'),
              'pipe_b_2026-11-11.jsonl': _envelope_line('pipe_a')})

        _run(paths)

        assert _signal_files(paths, 'pipe_a') == ['2026-11-10.parquet']
        assert (paths.refused / 'pipe_a-0001-20261115' / 'pipe_b_2026-11-11.jsonl').exists()
        assert (paths.finished / 'pipe_a-0001-20261115.zip').exists()
        assert _inbox(paths) == []

    def test_ticks_and_signals_loose_and_zipped_in_one_import(self, tmp_path):
        paths = _paths(tmp_path)
        _loose(paths, 'AAAUSD', '2026.01.15 10:00:00')
        _tick_zip(paths, 'ticks.zip', ('BBBUSD', '2026.01.15 10:00:00'))
        _signal_zip(paths, 'pipe_a-0001-20261115.zip', 'pipe_a', '2026-11-10')
        (paths.inbox / '2026-11-11.jsonl').write_text(_envelope_line('pipe_b'),
                                                      encoding='utf-8')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 2
        assert _signal_files(paths, 'pipe_a') == ['2026-11-10.parquet']
        assert _signal_files(paths, 'pipe_b') == ['2026-11-11.parquet']
        assert _inbox(paths) == []
        # A loose signal file leaves the inbox flat, like a loose tick export
        assert (paths.finished / '2026-11-11.jsonl').exists()

    def test_a_zip_mixing_ticks_and_signals_is_refused_as_a_whole(self, tmp_path):
        paths = _paths(tmp_path)
        _zip(paths.inbox / 'mixed.zip', {
            _export_name('AAAUSD', '2026.01.15 10:00:00'): _export_text('AAAUSD',
                                                                       '2026.01.15 10:00:00'),
            '2026-11-10.jsonl': _envelope_line('pipe_a')})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert (paths.refused / 'mixed.zip').exists()
        assert 'one zip holds one kind' in _note(paths, 'mixed.zip')
        assert not _tick_files(paths) and not _signal_files(paths, 'pipe_a')

    def test_a_refused_signal_file_goes_into_a_folder_named_after_its_zip(self, tmp_path):
        paths = _paths(tmp_path)
        mixed_pipelines = '\n'.join([_envelope_line('pipe_a'), _envelope_line('pipe_b', 600000)])
        _zip(paths.inbox / 'pipe_a-0001-20261115.zip', {
            '2026-11-10.jsonl': _envelope_line('pipe_a'),
            '2026-11-11.jsonl': mixed_pipelines})

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert (paths.finished / 'pipe_a-0001-20261115.zip').exists()
        refused_copy = paths.refused / 'pipe_a-0001-20261115' / '2026-11-11.jsonl'
        assert refused_copy.read_text(encoding='utf-8') == mixed_pipelines
        note = refused_copy.with_name(f'2026-11-11.jsonl{REFUSAL_NOTE_SUFFIX}')
        assert "mixed 'pipeline_id'" in note.read_text(encoding='utf-8')
        assert not (paths.refused / '2026-11-11.jsonl').exists()

    def test_only_one_kind_leaves_the_other_kinds_zip_in_the_inbox(self, tmp_path):
        paths = _paths(tmp_path)
        _tick_zip(paths, 'ticks.zip', ('AAAUSD', '2026.01.15 10:00:00'))
        _signal_zip(paths, 'pipe_a-0001-20261115.zip', 'pipe_a', '2026-11-10')

        ticks_only = ImportCoordinator(paths=paths, only=ImportSection.TICKS, show_display=False)
        ticks_only.run()

        assert len(_tick_files(paths)) == 1
        assert _inbox(paths) == ['pipe_a-0001-20261115.zip']

        signals_only = ImportCoordinator(paths=paths, only=ImportSection.SIGNALS,
                                         show_display=False)
        signals_only.run()

        assert _signal_files(paths, 'pipe_a') == ['2026-11-10.parquet']
        assert _inbox(paths) == []

    def test_a_member_whose_size_differs_from_its_manifest_refuses_the_zip(self, tmp_path):
        paths = _paths(tmp_path)
        zip_path = _signal_zip(paths, 'pipe_a-0001-20261115.zip', 'pipe_a', '2026-11-10')
        member_bytes = _envelope_line('pipe_a').encode('utf-8')
        manifest = {'manifest': {
            'package_id': 'pipe_a-0001-20261115',
            'zip': {'name': zip_path.name, 'sha256': _sha256(zip_path),
                    'size_bytes': zip_path.stat().st_size},
            'members': [{'name': '2026-11-10.jsonl',
                         'sha256': hashlib.sha256(member_bytes).hexdigest(),
                         'size_bytes': len(member_bytes) + 1}]}}
        (paths.inbox / 'pipe_a-0001-20261115.manifest.json').write_text(json.dumps(manifest),
                                                                         encoding='utf-8')

        coordinator = _run(paths)

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert (paths.refused / 'pipe_a-0001-20261115.zip').exists()
        assert 'differ in size from its package manifest' in _note(paths,
                                                                   'pipe_a-0001-20261115.zip')
        assert not _signal_files(paths, 'pipe_a')

