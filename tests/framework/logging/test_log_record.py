"""
Log Record Tests.

Pins two contracts of the log buffer: it carries **records**, not rendered lines — and the run's
own time is a COLUMN derived from the canonical clock, not a tick counter baked into the message.

Before the record, the buffer held a pre-rendered console line, with three consequences this
suite still locks out (ANSI codes in the persisted artifact, consumers re-parsing the line, a
display setting deciding what the report sees).

The event-time column replaced a tick index that counted only one of three pass kinds. What
guards the rendering now is not "identical to the past" — the line changed on purpose — but
**identical across both render paths**: console and file go through one formula and may differ
only in colour. That is the guard that would have caught the drift the file half still carried.
"""

import pickle
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.log_level import ColorCodes, LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.run_id_utils import mint_run_id
from python.framework.utils.time_utils import (
    EVENT_TIME_WIDTH,
    format_log_elapsed,
    format_log_event_time,
)

_START = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
_EVENT = datetime(2026, 3, 4, 9, 15, 30, 412000, tzinfo=timezone.utc)
_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _record(level=LogLevel.WARNING, message='data deviation detected', after_s=3.417,
            event_time=None) -> LogRecord:
    return LogRecord(
        level=level, timestamp=_START + timedelta(seconds=after_s), scope='EURUSD_01',
        message=message, event_time=event_time)


class TestTheRecordCarriesTheFact:
    def test_the_message_is_unrendered(self):
        """No colour, no level column, no timestamp — those are the renderer's business."""
        record = _record()
        assert record.message == 'data deviation detected'
        assert not _ANSI.search(record.message)
        assert 'WARNING' not in record.message

    def test_observation_time_and_event_time_are_separate_fields(self):
        """§9's pair: `timestamp` is when we saw it, `event_time` is when it happened."""
        record = _record(event_time=_EVENT)
        assert record.event_time == _EVENT
        assert record.timestamp != record.event_time

    def test_without_a_clock_there_is_no_event_time(self):
        """Not knowing is a state — never a wall-clock substitute (§9)."""
        assert _record().event_time is None

    def test_a_record_survives_the_process_boundary(self):
        """It travels on ProcessResult, so pickling is part of the contract."""
        record = _record(event_time=_EVENT)
        assert pickle.loads(pickle.dumps(record)) == record


class TestOneFormulaForBothSurfaces:
    """
    Console and file render through `format_line`. A second literal for the file is what let the
    two drift apart before, so the test is that they differ in colour and in nothing else.
    """

    def test_file_line_is_the_console_line_without_colour(self):
        for event_column in (False, True):
            record = _record(event_time=_EVENT)
            console = AbstractLogger.render_record(
                record, run_start=_START, event_column=event_column, colored=True)
            file_line = AbstractLogger.render_record(
                record, run_start=_START, event_column=event_column, colored=False)
            assert _ANSI.sub('', console) == file_line
            assert _ANSI.search(console) and not _ANSI.search(file_line)

    def test_a_log_without_the_column_renders_exactly_as_before(self):
        """global.log and the run-level logs are untouched by the column."""
        record = _record()
        expected = (f'{format_log_elapsed(3.417)} {ColorCodes.YELLOW}'
                    f'{LogLevel.WARNING:8}{ColorCodes.RESET} | data deviation detected')
        assert AbstractLogger.render_record(record, run_start=_START) == expected


class TestTheColumnIsARole:
    """
    "This log has no time axis" and "the clock has not started" are different facts. Both would
    read as an absent event_time, so the column is decided at construction, not by the record.
    """

    def test_the_column_is_absent_when_the_role_was_not_declared(self):
        rendered = AbstractLogger.render_record(
            _record(event_time=_EVENT), run_start=_START, event_column=False)
        assert '2026-03-04' not in rendered

    def test_the_column_holds_a_filler_of_its_own_width_before_the_clock_starts(self):
        with_clock = AbstractLogger.render_record(
            _record(event_time=_EVENT), run_start=_START, event_column=True)
        without = AbstractLogger.render_record(
            _record(event_time=None), run_start=_START, event_column=True)
        assert '2026-03-04 09:15:30.412' in with_clock
        assert format_log_event_time(None) in without
        # Fixed width, or the column shifts when the run enters its tick loop.
        assert len(_ANSI.sub('', with_clock)) == len(_ANSI.sub('', without))

    def test_the_filler_is_never_a_wall_clock_substitute(self):
        assert format_log_event_time(None).strip() == '—'
        assert len(format_log_event_time(None)) == EVENT_TIME_WIDTH


class TestADisplaySettingCannotHideAReportInput:
    """
    The buffer used to be filled only when the CONSOLE threshold passed, so raising the console
    level silently removed warnings from the run report. WARNING and ERROR are report input and
    are now captured either way; everything else still follows the console setting. They are
    removed again at DISPLAY time — on both display surfaces, which is the second half.
    """

    @staticmethod
    def _logger(tmp_path: Path) -> ScenarioLogger:
        return ScenarioLogger(
            scenario_set_name='t', scenario_name='s1',
            run_timestamp=datetime.now(timezone.utc),
            run_id=mint_run_id(datetime.now(timezone.utc)), log_root_override=tmp_path)

    def test_warning_and_error_survive_a_silent_console(self, tmp_path, monkeypatch):
        logger = self._logger(tmp_path)
        monkeypatch.setattr(logger, '_should_log_console', lambda level: False)

        logger.warning('a warning nobody wanted to see')
        logger.error('an error nobody wanted to see')

        captured = [record.message for record in logger.get_records()]
        assert captured == ['a warning nobody wanted to see',
                            'an error nobody wanted to see']
        logger.close()

    def test_lower_levels_still_follow_the_console_setting(self, tmp_path, monkeypatch):
        """Only the two levels the report reads are exempt — the rest stays a display decision."""
        logger = self._logger(tmp_path)
        monkeypatch.setattr(logger, '_should_log_console', lambda level: False)

        logger.info('chatter')
        logger.debug('more chatter')

        assert logger.get_records() == []
        logger.close()

    def test_the_level_filter_selects(self, tmp_path):
        logger = self._logger(tmp_path)
        logger.warning('w'), logger.error('e'), logger.warning('w2')

        assert [r.message for r in logger.get_records(LogLevel.WARNING)] == ['w', 'w2']
        assert [r.message for r in logger.get_records(LogLevel.ERROR)] == ['e']
        assert len(logger.get_records()) == 3
        logger.close()

    def test_print_buffer_applies_the_threshold_like_flush_buffer(self, capsys):
        """
        The second display surface. flush_buffer filters; print_buffer did not, so a warning the
        console threshold suppressed still reached the console through the batch flush.
        """
        buffer = [_record(level=LogLevel.WARNING, message='suppressed'),
                  _record(level=LogLevel.ERROR, message='shown')]

        AbstractLogger.print_buffer(buffer, 's1', run_start=_START,
                                    effective_level=LogLevel.ERROR)
        out = capsys.readouterr().out
        assert 'shown' in out and 'suppressed' not in out

    def test_print_buffer_without_a_threshold_prints_everything(self, capsys):
        buffer = [_record(level=LogLevel.WARNING, message='kept')]
        AbstractLogger.print_buffer(buffer, 's1', run_start=_START)
        assert 'kept' in capsys.readouterr().out


class TestTheClockIsPulledNotPushed:
    """
    A pull covers every pass kind that advances the clock — tick, heartbeat, and the timer /
    resolution events #375 adds — with one attachment instead of a call site per kind. The push
    variant is what left the live session log without a time column for as long as it existed.
    """

    def test_a_logger_without_a_clock_records_no_event_time(self, tmp_path):
        logger = ScenarioLogger(
            scenario_set_name='t', scenario_name='s1', log_root_override=tmp_path,
            run_timestamp=datetime.now(timezone.utc),
            run_id=mint_run_id(datetime.now(timezone.utc)), event_time_column=True)
        logger.warning('before the clock exists')
        assert logger.get_records()[0].event_time is None
        logger.close()

    def test_an_attached_clock_stamps_every_later_record(self, tmp_path):
        logger = ScenarioLogger(
            scenario_set_name='t', scenario_name='s1', log_root_override=tmp_path,
            run_timestamp=datetime.now(timezone.utc),
            run_id=mint_run_id(datetime.now(timezone.utc)), event_time_column=True)
        logger.warning('before')
        logger.attach_clock(lambda: _EVENT)
        logger.warning('after')

        before, after = logger.get_records()
        assert before.event_time is None
        assert after.event_time == _EVENT
        logger.close()
