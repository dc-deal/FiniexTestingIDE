"""
The Watch Over the Free Space Under a Session's Records (#632)

A disk that fills during a thirty-day run stops every record at once; the watch reads the free space
on a timer and tells the operator while there is still room to act. One episode per filesystem: one
alert when its free space falls below the warning floor, none while it stays there, and the all-clear
once it has stayed above the floor for the alerts' quiet period.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from python.framework.autotrader.event_loop.autotrader_storage_watch import AutotraderStorageWatch
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from python.framework.types.config_types.autotrader_defaults_config_types import StorageDefaults
from python.framework.types.machine_resources_types import DiskReading
from python.framework.types.store_types import StoreId
from tests.autotrader.cold_start.conftest import RecordingLogger
from tests.shared.operator_alert_recorder import OperatorAlertRecorder

_GB = 1024 ** 3
_T0 = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
_ROOTS = {StoreId.RUN_LEDGER: Path('/srv/records/runs/ledger'), StoreId.RUNS: Path('/srv/records/runs')}


class _Disk:
    """A machine reader whose one filesystem has whatever room the test sets."""

    def __init__(self):
        self.free_gb = 50.0
        self.stores = [StoreId.RUN_LEDGER, StoreId.RUNS]
        self.fails = None

    def read_disks(self, roots):
        if self.fails is not None:
            raise self.fails
        return [DiskReading(stores=self.stores, device=2049,
                            free_bytes=int(self.free_gb * _GB), total_bytes=100 * _GB,
                            free_pct=self.free_gb)]


class _Clock:
    """The session's canonical clock, moved by the test."""

    def __init__(self):
        self.now = _T0

    def get_current_time(self):
        return self.now


def _watch():
    disk, clock, alerts, logger = _Disk(), _Clock(), OperatorAlertRecorder(), RecordingLogger()
    watch = AutotraderStorageWatch(
        StorageDefaults(warn_free_gb=2.0, check_interval_s=60.0), _ROOTS, clock, alerts,
        all_clear_after_s=300.0, logger=logger, reader=disk)
    return watch, disk, clock, alerts, logger


class TestTheWatch:

    def test_a_disk_with_room_says_nothing(self):
        watch, disk, clock, alerts, logger = _watch()
        watch.check()
        assert alerts.kinds == [] and logger.warnings == []

    def test_below_the_floor_it_alerts_once_naming_the_stores(self):
        watch, disk, clock, alerts, logger = _watch()
        disk.free_gb = 1.4

        watch.check()
        clock.now += timedelta(seconds=60)
        watch.check()

        assert alerts.kinds == [OperatorAlertKind.DISK_LOW]
        (warning,) = logger.warnings
        assert '1.4 GB free' in warning and 'run_ledger, runs' in warning and '2 GB' in warning
        assert '/srv/records/runs/ledger' in warning, 'the operator learns where to free space'

    def test_the_all_clear_waits_for_the_quiet_period(self):
        watch, disk, clock, alerts, logger = _watch()
        disk.free_gb = 1.4
        watch.check()

        disk.free_gb = 5.2
        clock.now += timedelta(seconds=60)
        watch.check()
        assert alerts.kinds == [OperatorAlertKind.DISK_LOW], 'not yet'

        clock.now += timedelta(seconds=300)
        watch.check()
        assert alerts.kinds == [OperatorAlertKind.DISK_LOW, OperatorAlertKind.DISK_RECOVERED]

    def test_an_episode_belongs_to_its_filesystem_when_its_stores_change(self):
        watch, disk, clock, alerts, logger = _watch()
        disk.free_gb = 1.4
        watch.check()
        disk.stores = [StoreId.RUNS]  # the ledger's root moved to another disk
        disk.free_gb = 5.2
        clock.now += timedelta(seconds=60)
        watch.check()
        clock.now += timedelta(seconds=300)
        watch.check()
        assert alerts.kinds == [OperatorAlertKind.DISK_LOW, OperatorAlertKind.DISK_RECOVERED]

    def test_a_failed_reading_is_said_once_and_never_stops_the_session(self):
        watch, disk, clock, alerts, logger = _watch()
        disk.fails = OSError(116, 'Stale file handle')
        watch.check()
        watch.check()
        (warning,) = logger.warnings
        assert 'Disk unreadable' in warning and 'Stale file handle' in warning

        disk.fails = None
        disk.free_gb = 1.4
        watch.check()
        assert alerts.kinds == [OperatorAlertKind.DISK_LOW], 'the watch reads again'

    def test_its_interval_is_the_configured_one(self):
        watch = _watch()[0]
        assert watch.get_interval_s() == 60.0


@pytest.mark.parametrize('field, value', [
    ('check_interval_s', 0.0), ('min_free_gb_to_start', -1.0), ('warn_free_gb', -1.0)])
def test_the_storage_block_refuses_values_that_mean_nothing(field, value):
    """An interval of zero would read the disk on every pass."""
    with pytest.raises(ValidationError):
        StorageDefaults(**{field: value})


def test_the_storage_block_has_its_defaults():
    storage = StorageDefaults()
    assert (storage.min_free_gb_to_start, storage.warn_free_gb, storage.check_interval_s) == (
        1.0, 2.0, 60.0)


def test_a_watch_without_an_alert_channel_still_logs():
    disk, clock, logger = _Disk(), _Clock(), RecordingLogger()
    watch = AutotraderStorageWatch(StorageDefaults(), _ROOTS, clock, None, 300.0, logger, reader=disk)
    disk.free_gb = 0.5
    watch.check()
    assert any('Disk low' in warning for warning in logger.warnings)

