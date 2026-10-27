"""
A run stream appended one record at a time (#579 part A, the hot half of #476's day records): a
header line first, every record on the disk before the next, and the first failure said once on the
session's channel — after which nothing more is written, because a line behind a broken one would
damage a readable tail.
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from python.framework.constants.run_layout_constants import STREAM_HEADER_KIND
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from tests.shared.recording_logger import RecordingLogger

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# A process that appends records, says so, and then waits to be killed
_WRITER = (
    'import sys, time\n'
    'from pathlib import Path\n'
    'from python.framework.reporting.io.run_stream_appender import RunStreamAppender\n'
    'appender = RunStreamAppender(Path(sys.argv[1]), "my_stream", 1, "my_run", None, stops="-")\n'
    'for n in range(25):\n'
    '    appender.write({"n": n})\n'
    'print("WRITTEN", flush=True)\n'
    'time.sleep(120)\n'
)


def _lines(path: Path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _appender(path: Path, logger: RecordingLogger) -> RunStreamAppender:
    return RunStreamAppender(path, 'my_stream', 3, 'my_run', logger,
                             stops='Nothing more is recorded', header_fields={'cadence_s': 60.0})


class TestTheStream:

    def test_the_header_names_the_stream_its_version_and_its_run(self, tmp_path):
        path = tmp_path / 'io' / 'my_stream.jsonl'
        _appender(path, RecordingLogger()).close()
        assert _lines(path) == [{'record_kind': STREAM_HEADER_KIND, 'stream': 'my_stream',
                                 'schema_version': 3, 'run_id': 'my_run', 'cadence_s': 60.0}]

    def test_every_record_is_readable_while_the_stream_is_open(self, tmp_path):
        path = tmp_path / 'my_stream.jsonl'
        appender = _appender(path, RecordingLogger())
        assert appender.write({'n': 1, 'empty': None}) is True
        assert _lines(path)[1:] == [{'n': 1}], 'a None value is left out of the line'
        appender.close()

    def test_a_closed_stream_writes_nothing(self, tmp_path):
        path = tmp_path / 'my_stream.jsonl'
        appender = _appender(path, RecordingLogger())
        appender.close()
        assert appender.write({'n': 1}) is False
        assert appender.is_writing() is False
        assert len(_lines(path)) == 1


class TestTheFirstFailure:

    def test_is_said_once_and_ends_the_writing(self, tmp_path, monkeypatch):
        logger = RecordingLogger()
        appender = _appender(tmp_path / 'my_stream.jsonl', logger)

        def disk_full(record):
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(appender._writer, 'write', disk_full)
        assert appender.write({'n': 1}) is False
        assert appender.write({'n': 2}) is False
        errors = logger.text_at('error')
        assert errors.count('could not be written') == 1
        assert 'my_stream' in errors and 'Nothing more is recorded; the session continues' in errors
        assert appender.is_writing() is False
        appender.close()
        assert logger.text_at('error').count('could not be written') == 1

    def test_a_stream_that_cannot_be_opened_is_said_and_the_run_goes_on(self, tmp_path):
        blocked = tmp_path / 'a_file'
        blocked.write_text('', encoding='utf-8')
        logger = RecordingLogger()
        appender = _appender(blocked / 'my_stream.jsonl', logger)
        assert appender.write({'n': 1}) is False
        appender.close()
        assert logger.text_at('error').count('could not be written') == 1


class TestAKill:

    def test_leaves_every_record_written_before_it(self, tmp_path):
        path = tmp_path / 'my_stream.jsonl'
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        writer = subprocess.Popen([sys.executable, '-c', _WRITER, str(path)], cwd=PROJECT_ROOT,
                                  env=env, stdout=subprocess.PIPE, text=True)
        try:
            assert writer.stdout.readline().strip() == 'WRITTEN'
            writer.send_signal(signal.SIGKILL)
            writer.wait(timeout=10)
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait(timeout=10)
            writer.stdout.close()
        lines = _lines(path)
        assert lines[0]['record_kind'] == STREAM_HEADER_KIND
        assert [line['n'] for line in lines[1:]] == list(range(25))
