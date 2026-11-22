"""
Where a Session Records, and Whether It Can (#632)

Before its first order a session proves that every root it writes into can be written — by writing a
probe, because a permission bit answers yes to root on a read-only folder — and that every filesystem
those roots live on has room above the floor. The roots come from the store catalog, the two
carry-overs from the profile; the machine reader measures each filesystem once, its stores listed,
as the room a process that is not root may use.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.framework.store.session_store_roots import SESSION_STORES, session_store_roots
from python.framework.types.machine_resources_types import DiskReading
from python.framework.types.store_types import StoreId
from python.framework.utils import machine_resources_reader as reader_module
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.validators.session_storage_validator import (
    WRITE_PROBE_BYTES,
    WRITE_PROBE_NAME,
    check_session_storage,
)

_GB = 1024 ** 3


class _Reader:
    """A machine reader whose filesystems have the room it is given."""

    def __init__(self, free_gb: float):
        self._free = int(free_gb * _GB)

    def read_disks(self, roots):
        return [DiskReading(stores=sorted(roots, key=lambda s: s.value), device=2049,
                            free_bytes=self._free,
                            total_bytes=100 * _GB, free_pct=self._free / (100 * _GB) * 100)]


def _roots(tmp_path: Path):
    return {StoreId.RUNS: tmp_path / 'runs', StoreId.COLD_START_STATE: tmp_path / 'carry'}


class TestTheRootsASessionWrites:

    def test_every_written_store_has_its_root(self):
        roots = session_store_roots()
        assert set(roots) == set(SESSION_STORES)

    def test_the_global_log_is_written_into_its_folder(self):
        assert session_store_roots()[StoreId.GLOBAL_LOG].suffix == ''

    def test_the_profile_places_the_carry_overs(self, tmp_path):
        roots = session_store_roots(str(tmp_path / 'cold'), str(tmp_path / 'state'))
        assert (roots[StoreId.COLD_START_STATE], roots[StoreId.SESSION_STATE]) == (
            tmp_path / 'cold', tmp_path / 'state')


class TestTheStartCheck:

    def test_writable_roots_with_room_pass_and_leave_no_probe(self, tmp_path):
        roots = _roots(tmp_path)
        assert check_session_storage(roots, 1.0, _Reader(free_gb=50)) is None
        for root in roots.values():
            assert root.is_dir() and not (root / WRITE_PROBE_NAME).exists()

    def test_a_probe_a_crash_left_behind_is_replaced_and_removed(self, tmp_path):
        roots = _roots(tmp_path)
        roots[StoreId.RUNS].mkdir()
        (roots[StoreId.RUNS] / WRITE_PROBE_NAME).write_bytes(b'left')
        assert check_session_storage(roots, 1.0, _Reader(free_gb=50)) is None
        assert not (roots[StoreId.RUNS] / WRITE_PROBE_NAME).exists()

    def test_a_root_that_cannot_be_written_refuses_and_names_it(self, tmp_path, monkeypatch):
        """Injected: the suite runs as root, where a read-only permission bit refuses nothing."""
        real = Path.write_bytes

        def refuse_the_carry_over(self, data):
            if self.parent.name == 'carry':
                raise PermissionError(13, 'Permission denied')
            return real(self, data)
        monkeypatch.setattr(Path, 'write_bytes', refuse_the_carry_over)

        refusal = check_session_storage(_roots(tmp_path), 1.0, _Reader(free_gb=50))

        assert 'cold_start_state store' in refusal and 'PermissionError' in refusal

    def test_the_probe_writes_data_not_only_a_folder_entry(self, tmp_path, monkeypatch):
        """A block quota or a disk with no free blocks refuses data, not an empty file."""
        written = []
        real = Path.write_bytes

        def record(self, data):
            written.append(len(data))
            return real(self, data)
        monkeypatch.setattr(Path, 'write_bytes', record)

        check_session_storage(_roots(tmp_path), 1.0, _Reader(free_gb=50))

        assert written == [WRITE_PROBE_BYTES, WRITE_PROBE_BYTES]

    def test_every_failure_is_named_in_one_refusal(self, tmp_path, monkeypatch):
        """The operator fixes them all before the next start, not one per start."""
        def refuse(self, data):
            raise PermissionError(13, 'Permission denied')
        monkeypatch.setattr(Path, 'write_bytes', refuse)

        refusal = check_session_storage(_roots(tmp_path), 1.0, _Reader(free_gb=0.6))

        assert 'the cold_start_state store' in refusal and 'the runs store' in refusal
        assert '0.6 GB free' in refusal
        assert refusal.count('; ') == 2

    def test_a_root_that_cannot_be_created_refuses(self, tmp_path, monkeypatch):
        def refuse(self, mode=0o777, parents=False, exist_ok=False):
            raise OSError(30, 'Read-only file system')
        monkeypatch.setattr(Path, 'mkdir', refuse)

        refusal = check_session_storage(_roots(tmp_path), 1.0, _Reader(free_gb=50))

        assert 'cannot be written' in refusal

    def test_a_disk_below_the_floor_refuses_and_names_its_stores(self, tmp_path):
        refusal = check_session_storage(_roots(tmp_path), 1.0, _Reader(free_gb=0.6))

        assert '0.6 GB free' in refusal and 'min_free_gb_to_start (1 GB)' in refusal
        assert 'cold_start_state, runs' in refusal
        assert f"under {tmp_path / 'carry'}" in refusal, 'the operator learns where to free space'


class TestTheReaderMeasuresEachFilesystemOnce:

    def test_roots_on_one_filesystem_are_one_reading(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reader_module.shutil, 'disk_usage',
                            lambda path: SimpleNamespace(total=200 * _GB, used=0, free=50 * _GB))
        (tmp_path / 'runs').mkdir()

        (reading,) = MachineResourcesReader().read_disks({
            StoreId.RUNS: tmp_path / 'runs',
            StoreId.RUN_LEDGER: tmp_path / 'runs' / 'ledger',  # not there yet: its parent is
        })

        assert reading.stores == [StoreId.RUN_LEDGER, StoreId.RUNS]
        assert reading.device == os.stat(tmp_path / 'runs').st_dev
        assert (reading.free_bytes, reading.total_bytes, reading.free_pct) == (
            50 * _GB, 200 * _GB, 25.0)

    def test_the_room_is_what_a_process_that_is_not_root_may_use(self, tmp_path):
        """`shutil.disk_usage` reports f_bavail as free — not f_bfree, which counts root's share."""
        (reading,) = MachineResourcesReader().read_disks({StoreId.RUNS: tmp_path})
        stat = os.statvfs(tmp_path)
        assert reading.free_bytes == stat.f_bavail * stat.f_frsize


@pytest.mark.parametrize('store', SESSION_STORES)
def test_every_session_store_is_measured(store):
    roots = session_store_roots()
    readings = MachineResourcesReader().read_disks(roots)
    assert any(store in reading.stores for reading in readings)
