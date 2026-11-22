"""
A file written atomically is old or new, never half; written durably, it also survives a crash of
the machine (#476): its bytes reach the disk before the rename, and the rename after it. A folder
that refuses to be synced — some mounts do — leaves the write standing. Whether a write is durable
is the store's to say, not the caller's (#632): a record or a carry-over is, a derived store is not.
"""

import os
import stat
from pathlib import Path
from typing import List

import pytest

from python.framework.types.store_types import StoreId
from python.framework.utils.atomic_file_utils import write_file_atomic, write_text_atomic


def _record_syncs(monkeypatch) -> List[str]:
    """
    Record each fsync and replace, in order, as 'file', 'folder' or 'replace'.

    Returns:
        The list the calls are recorded in
    """
    calls: List[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(descriptor):
        calls.append('folder' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
        return real_fsync(descriptor)

    def replace(source, target):
        calls.append('replace')
        return real_replace(source, target)

    monkeypatch.setattr(os, 'fsync', fsync)
    monkeypatch.setattr(os, 'replace', replace)
    return calls


class TestADurableWrite:

    def test_the_bytes_reach_the_disk_before_the_rename_and_the_rename_after(
            self, tmp_path, monkeypatch):
        calls = _record_syncs(monkeypatch)
        target = tmp_path / 'day.bin'

        write_file_atomic(target, lambda temporary: temporary.write_bytes(b'sealed'),
                          StoreId.RUNS)

        assert calls == ['file', 'replace', 'folder']
        assert target.read_bytes() == b'sealed'

    def test_a_derived_store_is_not_synced(self, tmp_path, monkeypatch):
        calls = _record_syncs(monkeypatch)

        write_text_atomic(tmp_path / 'snapshot.json', '{}', StoreId.BROKER_RUNTIME)

        assert calls == ['replace']

    @pytest.mark.parametrize('store', [StoreId.COLD_START_STATE, StoreId.SESSION_STATE,
                                       StoreId.RUN_CONFIGS, StoreId.HOST_IDENTITY])
    def test_a_carry_over_a_record_and_the_host_identity_are_synced(
            self, tmp_path, monkeypatch, store):
        """The carry-over holds the open position book — an empty one after a power loss is
        a bot that trades beside its own holding."""
        calls = _record_syncs(monkeypatch)

        write_text_atomic(tmp_path / 'state.json', '{}', store)

        assert calls == ['file', 'replace', 'folder']

    def test_a_mode_is_kept(self, tmp_path):
        target = tmp_path / 'patch.diff'

        write_file_atomic(target, lambda temporary: temporary.write_bytes(b'diff'),
                          StoreId.RUN_PATCHES, mode=0o600)

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_a_folder_that_refuses_to_sync_leaves_the_write_standing(self, tmp_path, monkeypatch):
        real_fsync = os.fsync

        def refuse_folders(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(22, 'Invalid argument')
            return real_fsync(descriptor)
        monkeypatch.setattr(os, 'fsync', refuse_folders)
        target = tmp_path / 'day.bin'

        write_file_atomic(target, lambda temporary: temporary.write_bytes(b'sealed'),
                          StoreId.RUNS)

        assert target.read_bytes() == b'sealed'

    def test_a_write_that_fails_leaves_no_temporary_file(self, tmp_path):
        def fail(temporary: Path) -> None:
            temporary.write_bytes(b'half')
            raise OSError(28, 'No space left on device')

        with pytest.raises(OSError):
            write_file_atomic(tmp_path / 'day.bin', fail, StoreId.RUNS)

        assert list(tmp_path.iterdir()) == []
