"""
A money-record stream keeps what it could not write (#632).

A failed write keeps its line and every later one in memory, in their order, and says the first
failure of the stretch on the session's channel and to whoever holds the entries over it. `sync`
writes them again after cutting the file back to where its last forced line ends — a line half on
the disk lies past it — and forces them; a failed force counts the lines written since the last good
one as lost and writes them again too. While it writes, `sync` forces new lines, and only new ones.
"""

import errno
import json
import os
from pathlib import Path

import pytest

from python.framework.reporting.io import jsonl_stream_writer
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from tests.shared.recording_logger import RecordingLogger


class _Disk:
    """
    The writer's view of the operating system, with a disk the test fills: writes and forces fail
    while `full` is set, and a write can be cut after some of its bytes, the way a full disk takes
    a part of a line.
    """

    def __init__(self):
        self.full = False
        self.force_fails = False
        self.bytes_before_full = None
        self.forces = 0

    def write(self, fd, data):
        if self.bytes_before_full is not None:
            part, self.bytes_before_full = self.bytes_before_full, None
            self.full = True
            return os.write(fd, bytes(data[:part]))
        if self.full:
            raise OSError(errno.ENOSPC, 'No space left on device')
        return os.write(fd, data)

    def fsync(self, fd):
        if self.force_fails:
            raise OSError(errno.EIO, 'Input/output error')
        self.forces += 1
        os.fsync(fd)

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.fixture
def disk(monkeypatch):
    fake = _Disk()
    monkeypatch.setattr(jsonl_stream_writer, 'os', fake)
    return fake


def _stream(path: Path, logger: RecordingLogger, failures: list) -> RunStreamAppender:
    return RunStreamAppender(path, 'my_money', 1, 'my_run', logger,
                             stops='Entries are held until it is written again',
                             keeps_unwritten=True, on_failure=failures.append)


def _records(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()][1:]


class TestAFailedWrite:

    def test_keeps_its_line_and_every_later_one_in_their_order(self, tmp_path, disk):
        logger, failures = RecordingLogger(), []
        stream = _stream(tmp_path / 'money.jsonl', logger, failures)
        assert stream.write({'n': 1})
        disk.full = True
        assert stream.write({'n': 2}, at='seq 2') is False
        assert stream.write({'n': 3}) is False

        assert stream.is_failing()
        assert len(failures) == 1, 'the first failure of the stretch is told, once'
        assert 'at seq 2' in logger.text_at('error')
        assert 'kept in memory' in logger.text_at('error')

        disk.full = False
        assert stream.sync() is True
        assert not stream.is_failing() and stream.get_unwritten_count() == 0
        assert [r['n'] for r in _records(tmp_path / 'money.jsonl')] == [1, 2, 3]

    def test_a_line_half_on_the_disk_is_cut_before_it_is_written_again(self, tmp_path, disk):
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), [])
        stream.write({'n': 1})
        disk.bytes_before_full = 5
        stream.write({'n': 2, 'note': 'a line long enough to be cut'})
        assert (tmp_path / 'money.jsonl').read_text(encoding='utf-8').endswith('{"n":')

        disk.full = False
        stream.sync()
        assert [r['n'] for r in _records(tmp_path / 'money.jsonl')] == [1, 2]

    def test_it_is_written_again_only_once_the_disk_takes_it(self, tmp_path, disk):
        logger, failures = RecordingLogger(), []
        stream = _stream(tmp_path / 'money.jsonl', logger, failures)
        stream.sync()  # the header forced, so only the record waits
        disk.full = True
        stream.write({'n': 1})
        assert stream.sync() is False and stream.sync() is False
        assert stream.get_unwritten_count() == 1
        assert len(failures) == 1 and logger.text_at('error').count('could not be written') == 1

        disk.full = False
        assert stream.sync() is True
        assert [r['n'] for r in _records(tmp_path / 'money.jsonl')] == [1]

    def test_a_second_stretch_is_told_again(self, tmp_path, disk):
        failures = []
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), failures)
        disk.full = True
        stream.write({'n': 1})
        disk.full = False
        stream.sync()
        disk.full = True
        stream.write({'n': 2})
        assert len(failures) == 2


class TestAFailedForce:

    def test_writes_again_every_line_since_the_last_good_one(self, tmp_path, disk):
        """A retried force may answer success over pages the system dropped: the lines go again."""
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), [])
        stream.write({'n': 1})
        assert stream.sync() is True
        stream.write({'n': 2})
        stream.write({'n': 3})
        disk.force_fails = True
        assert stream.sync() is False
        assert stream.get_unwritten_count() == 2

        disk.force_fails = False
        assert stream.sync() is True
        assert [r['n'] for r in _records(tmp_path / 'money.jsonl')] == [1, 2, 3]


class TestTheCadence:

    def test_forces_new_lines_and_only_new_ones(self, tmp_path, disk):
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), [])
        stream.sync()
        forced = disk.forces
        stream.sync()
        assert disk.forces == forced, 'nothing new, nothing forced'
        stream.write({'n': 1})
        stream.sync()
        assert disk.forces == forced + 1

    def test_a_stream_that_keeps_nothing_forces_nothing(self, tmp_path, disk):
        stream = RunStreamAppender(tmp_path / 'frames.jsonl', 'my_frames', 1, 'my_run',
                                   RecordingLogger(), stops='-')
        stream.write({'n': 1})
        assert stream.sync() is True
        assert disk.forces == 0


class TestTheClose:

    def test_makes_one_last_attempt(self, tmp_path, disk):
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), [])
        disk.full = True
        stream.write({'n': 1})
        disk.full = False
        stream.close()
        assert [r['n'] for r in _records(tmp_path / 'money.jsonl')] == [1]

    def test_says_what_is_lost_when_the_disk_stays_full(self, tmp_path, disk):
        logger = RecordingLogger()
        stream = _stream(tmp_path / 'money.jsonl', logger, [])
        stream.sync()  # the header forced, so only the records wait
        disk.full = True
        stream.write({'n': 1})
        stream.write({'n': 2})
        stream.close()
        assert '2 line(s) of the my_money stream could not be written' in logger.text_at('error')

    def test_forces_what_was_written(self, tmp_path, disk):
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), [])
        stream.write({'n': 1})
        forced = disk.forces
        stream.close()
        assert disk.forces == forced + 1


class TestAStreamThatCannotBeOpened:

    def test_starts_its_file_afresh_once_it_can(self, tmp_path, disk):
        failures = []
        disk.full = True
        stream = _stream(tmp_path / 'money.jsonl', RecordingLogger(), failures)
        stream.write({'n': 1})
        assert stream.is_failing() and len(failures) == 1

        disk.full = False
        assert stream.sync() is True
        lines = (tmp_path / 'money.jsonl').read_text(encoding='utf-8').splitlines()
        assert json.loads(lines[0])['stream'] == 'my_money'
        assert [json.loads(line)['n'] for line in lines[1:]] == [1]


class TestTheWriter:

    def test_counts_only_complete_lines(self, tmp_path, disk):
        writer = JsonlStreamWriter(tmp_path / 'a.jsonl')
        writer.write({'n': 1})
        size = writer.get_size()
        disk.bytes_before_full = 3
        with pytest.raises(OSError):
            writer.write({'n': 2})
        assert writer.get_size() == size
        writer.close()

    def test_a_cut_never_lengthens_the_file(self, tmp_path):
        writer = JsonlStreamWriter(tmp_path / 'a.jsonl')
        writer.write({'n': 1})
        on_disk = (tmp_path / 'a.jsonl').stat().st_size
        writer.cut_to(on_disk + 100)
        assert (tmp_path / 'a.jsonl').stat().st_size == on_disk
        assert writer.get_size() == on_disk
        writer.close()

    def test_appends_after_a_cut(self, tmp_path):
        writer = JsonlStreamWriter(tmp_path / 'a.jsonl')
        writer.write({'n': 1})
        keep = writer.get_size()
        writer.write({'n': 2})
        writer.cut_to(keep)
        writer.write({'n': 3})
        writer.close()
        lines = (tmp_path / 'a.jsonl').read_text(encoding='utf-8').splitlines()
        assert [json.loads(line)['n'] for line in lines] == [1, 3]
