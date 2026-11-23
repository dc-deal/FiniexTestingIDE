"""
The entry hold over the money record (#632).

While the order events or the trades cannot be written, the session holds its entries — reason
`record_unwritable` on #499's gate: it manages and closes what it has and opens nothing it could not
record. The streams keep what they could not write, the record's cadence writes it again, and the
hold lifts — observed — the moment nothing is kept any more; one stream still failing keeps it.
"""

import errno
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from python.framework.autotrader.run_record.money_record_streams import MoneyRecordStreams
from python.framework.reporting.io import jsonl_stream_writer
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.trading_env.entry_holds import EntryHolds
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldLift, EntryHoldReason
from tests.shared.operator_alert_recorder import OperatorAlertRecorder
from tests.shared.recording_logger import RecordingLogger

_AT = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)


class _Disk:
    """The writer's view of the operating system, with a disk the test fills."""

    def __init__(self):
        self.full = False

    def write(self, fd, data):
        if self.full:
            raise OSError(errno.ENOSPC, 'No space left on device')
        return os.write(fd, data)

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.fixture
def disk(monkeypatch):
    fake = _Disk()
    monkeypatch.setattr(jsonl_stream_writer, 'os', fake)
    return fake


@pytest.fixture
def session(tmp_path):
    holds = EntryHolds()
    executor = SimpleNamespace(get_entry_holds=lambda: holds,
                               get_current_time_if_set=lambda: _AT)
    alerts, logger = OperatorAlertRecorder(), RecordingLogger()
    record = MoneyRecordStreams(executor, alerts, logger, sync_interval_s=1.0)
    streams = {}
    for name in ('order-event', 'trade'):
        streams[name] = RunStreamAppender(
            tmp_path / f'{name}.jsonl', name, 1, 'my_run', logger,
            stops='Entries are held until it is written again', keeps_unwritten=True,
            on_failure=record.failure_listener(name))
        record.add(name, streams[name])
    return SimpleNamespace(holds=holds, alerts=alerts, logger=logger, record=record,
                           streams=streams)


class TestTheHold:

    def test_a_failed_write_holds_the_entries_at_once(self, session, disk):
        disk.full = True
        session.streams['order-event'].write({'seq': 7})

        (hold,) = session.holds.get_active()
        assert hold.reason is EntryHoldReason.RECORD_UNWRITABLE and hold.held_at == _AT
        assert 'the order-event stream cannot be written' in hold.detail
        assert 'No space left on device' in hold.detail
        assert session.alerts.kinds == [OperatorAlertKind.ENTRIES_HELD]

    def test_a_second_failing_stream_holds_nothing_twice(self, session, disk):
        disk.full = True
        session.streams['order-event'].write({'seq': 7})
        session.streams['trade'].write({'trade': 1})
        assert len(session.holds.get_active()) == 1
        assert session.alerts.kinds == [OperatorAlertKind.ENTRIES_HELD]

    def test_the_hold_stands_while_the_disk_is_full(self, session, disk):
        disk.full = True
        session.streams['order-event'].write({'seq': 7})
        session.record.sync()
        session.record.sync()
        assert session.holds.is_held(EntryHoldReason.RECORD_UNWRITABLE)


class TestTheLift:

    def test_lifts_observed_once_every_kept_line_is_on_the_disk(self, session, disk):
        disk.full = True
        session.streams['order-event'].write({'seq': 7})
        session.streams['trade'].write({'trade': 1})
        disk.full = False
        session.record.sync()

        (hold,) = session.holds.get_all()
        assert (hold.lifted_by, hold.lifted_at) == (EntryHoldLift.OBSERVED, _AT)
        assert session.alerts.kinds == [OperatorAlertKind.ENTRIES_HELD,
                                        OperatorAlertKind.ENTRIES_LIFTED]
        assert 'Entries lifted: the money record is written again' in session.logger.text_at('info')

    def test_one_stream_still_failing_keeps_the_hold(self, session, disk, monkeypatch):
        disk.full = True
        session.streams['order-event'].write({'seq': 7})
        disk.full = False
        monkeypatch.setattr(session.streams['trade'], 'is_failing', lambda: True)
        session.record.sync()
        assert session.holds.is_held(EntryHoldReason.RECORD_UNWRITABLE)

    def test_a_session_whose_record_writes_holds_nothing(self, session, disk):
        session.streams['order-event'].write({'seq': 1})
        session.record.sync()
        assert session.holds.get_all() == () and session.alerts.kinds == []


def test_its_cadence_is_the_configured_one(session):
    assert session.record.get_interval_s() == 1.0
