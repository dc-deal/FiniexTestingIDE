"""
The two ends of a run stream, read without its middle (#611): the header, the first record and the
latest one, whatever the stream's length. A last line cut off mid-write is left out and reported; a
complete line that does not parse is a damaged file.
"""

from pathlib import Path

import pytest

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io import run_stream_reader
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.reporting.io.run_stream_reader import read_stream_ends


def _stream(path: Path, records: int) -> Path:
    """
    A stream as the appender writes it: a header, then numbered records.

    Args:
        path: The file
        records: How many records follow the header

    Returns:
        The file
    """
    appender = RunStreamAppender(path, 'my_stream', 1, 'my_run', None, stops='-',
                                 header_fields={'cadence_s': 60.0})
    for n in range(records):
        appender.write({'record_kind': 'frame', 'n': n})
    appender.close()
    return path


def _append(path: Path, text: str) -> None:
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(text)


class TestTheEnds:

    def test_the_header_the_first_and_the_last_record(self, tmp_path):
        ends = read_stream_ends(_stream(tmp_path / 'my_stream.jsonl', 5))
        assert ends.header['stream'] == 'my_stream' and ends.header['cadence_s'] == 60.0
        assert ends.first == {'record_kind': 'frame', 'n': 0}
        assert ends.last == {'record_kind': 'frame', 'n': 4}
        assert ends.truncated_tail is False

    def test_one_record_is_the_first_and_the_last(self, tmp_path):
        ends = read_stream_ends(_stream(tmp_path / 'my_stream.jsonl', 1))
        assert ends.first == ends.last == {'record_kind': 'frame', 'n': 0}

    def test_a_stream_with_only_its_header_holds_no_record(self, tmp_path):
        ends = read_stream_ends(_stream(tmp_path / 'my_stream.jsonl', 0))
        assert ends.header is not None
        assert ends.first is None and ends.last is None

    def test_an_empty_file_holds_nothing(self, tmp_path):
        path = tmp_path / 'my_stream.jsonl'
        path.write_bytes(b'')
        ends = read_stream_ends(path)
        assert (ends.header, ends.first, ends.last) == (None, None, None)
        assert ends.truncated_tail is False

    def test_a_file_without_a_header_starts_with_its_first_record(self, tmp_path):
        path = tmp_path / 'my_stream.jsonl'
        path.write_text('{"n": 0}\n{"n": 1}\n', encoding='utf-8')
        ends = read_stream_ends(path)
        assert ends.header is None
        assert (ends.first, ends.last) == ({'n': 0}, {'n': 1})


class TestTheTail:

    def test_a_line_cut_off_mid_write_is_left_out_and_reported(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 3)
        _append(path, '{"record_kind": "frame", "n": 3, "bal')
        ends = read_stream_ends(path)
        assert ends.last == {'record_kind': 'frame', 'n': 2}
        assert ends.truncated_tail is True

    def test_a_record_whose_newline_was_not_written_is_kept(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 3)
        _append(path, '{"record_kind": "frame", "n": 3}')
        ends = read_stream_ends(path)
        assert ends.last == {'record_kind': 'frame', 'n': 3}
        assert ends.truncated_tail is False

    def test_a_header_followed_by_a_cut_off_line_holds_no_record(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 0)
        _append(path, '{"record_kind": "fra')
        ends = read_stream_ends(path)
        assert (ends.first, ends.last, ends.truncated_tail) == (None, None, True)

    def test_a_last_line_longer_than_one_step_back(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 2)
        _append(path, '{"record_kind": "frame", "n": 2, "pad": "' + 'x' * 500 + '"}\n')
        ends = read_stream_ends(path, block_bytes=16)
        assert ends.last['n'] == 2 and len(ends.last['pad']) == 500


class TestALongStream:

    def test_is_read_at_its_two_ends_only(self, tmp_path, monkeypatch):
        """
        Two hundred thousand records — about 7 MB — and the read touches a few kilobytes of them.
        """
        path = _stream(tmp_path / 'my_stream.jsonl', 0)
        _append(path, ''.join(f'{{"record_kind": "frame", "n": {n}}}\n' for n in range(200_000)))
        read = []

        class _Counting:
            def __init__(self, handle):
                self._handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._handle.close()

            def read(self, size=-1):
                data = self._handle.read(size)
                read.append(len(data))
                return data

            def readline(self):
                data = self._handle.readline()
                read.append(len(data))
                return data

            def seek(self, offset, whence=0):
                return self._handle.seek(offset, whence)

        monkeypatch.setattr(run_stream_reader, 'open',
                            lambda file, mode: _Counting(open(file, mode)), raising=False)
        ends = read_stream_ends(path)
        assert ends.last == {'record_kind': 'frame', 'n': 199_999}
        assert path.stat().st_size > 5_000_000
        assert sum(read) < 200_000, 'the two ends, never the whole stream'


class TestADamagedFile:

    def test_a_complete_line_that_does_not_parse(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 2)
        _append(path, '{"record_kind": "frame", "n": \n')
        with pytest.raises(ReportArtifactUnreadableError, match='the last complete line'):
            read_stream_ends(path)

    def test_a_line_that_is_not_an_object(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 2)
        _append(path, '[1, 2]\n')
        with pytest.raises(ReportArtifactUnreadableError, match='not a JSON object'):
            read_stream_ends(path)

    def test_a_byte_that_is_not_utf8(self, tmp_path):
        path = _stream(tmp_path / 'my_stream.jsonl', 2)
        with open(path, 'ab') as handle:
            handle.write(b'{"n": "\xff"}\n')
        with pytest.raises(ReportArtifactUnreadableError, match='not UTF-8'):
            read_stream_ends(path)
