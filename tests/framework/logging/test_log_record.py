"""
Log Record Tests.

The console buffer carries `LogRecord`s — the fact — and rendering happens at the surface that
prints them. Before that, the buffer held a pre-rendered line, which had three consequences
these tests lock out:

- the rendered line carried ANSI colour codes into `warnings_errors.json` and out over the API
- consumers had to take the line apart again with `split(' | ')` to recover the message
- the buffer was filled only when the CONSOLE threshold passed, so a display setting decided
  what the run report was allowed to see

The buffer crosses the process boundary on `ProcessResult`, so a record must also pickle.
"""

import pickle
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.log_level import ColorCodes, LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.time_utils import format_log_elapsed, format_timestamp

_START = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _record(level=LogLevel.WARNING, message='data deviation detected', after_s=3.417,
            tick_index=None, tick_time=None) -> LogRecord:
    return LogRecord(
        level=level, timestamp=_START + timedelta(seconds=after_s), scope='EURUSD_01',
        message=message, tick_index=tick_index, tick_time=tick_time)


class TestTheRecordCarriesTheFact:
    def test_the_message_is_unrendered(self):
        """No colour, no level column, no timestamp — those are the renderer's business."""
        record = _record()
        assert record.message == 'data deviation detected'
        assert not _ANSI.search(record.message)
        assert 'WARNING' not in record.message

    def test_observation_time_and_event_time_are_separate_fields(self):
        """§9's pair: `timestamp` is when we saw it, `tick_time` is when it happened."""
        tick_time = datetime(2026, 3, 4, 9, 15, 30, tzinfo=timezone.utc)
        record = _record(tick_index=12345, tick_time=tick_time)
        assert record.tick_time == tick_time
        assert record.timestamp != record.tick_time

    def test_outside_the_tick_loop_there_is_no_tick(self):
        record = _record()
        assert record.tick_index is None and record.tick_time is None

    def test_a_record_survives_the_process_boundary(self):
        """It travels on ProcessResult, so pickling is part of the contract."""
        record = _record(tick_index=7, tick_time=_START)
        assert pickle.loads(pickle.dumps(record)) == record


class TestRenderingMovedButDidNotChange:
    """The formatting left the capture path; the printed line must be the same as before."""

    @staticmethod
    def _as_before(record: LogRecord, run_start: datetime) -> str:
        """The pre-refactor formula, reproduced here so the comparison is against code, not prose."""
        elapsed = (record.timestamp - run_start).total_seconds()
        seconds = int(elapsed)
        stamp = f'[{seconds:3d}s {int((elapsed - seconds) * 1000):3d}ms]'
        color = {LogLevel.VERBOSE: ColorCodes.PURPLE, LogLevel.DEBUG: ColorCodes.GRAY,
                 LogLevel.INFO: ColorCodes.BLUE, LogLevel.WARNING: ColorCodes.YELLOW,
                 LogLevel.ERROR: ColorCodes.RED}[record.level]
        message = record.message
        if record.tick_index is not None:
            message = f'{record.tick_index:5}| {format_timestamp(record.tick_time)} | {message}'
        return f'{stamp} {color}{record.level:8}{ColorCodes.RESET} | {message}'

    def test_plain_line_is_character_identical(self):
        for level in (LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR):
            record = _record(level=level)
            assert (AbstractLogger.render_record(record, run_start=_START)
                    == self._as_before(record, _START))

    def test_tick_prefix_is_rebuilt_identically(self):
        """The prefix is presentation — dropped from the message, restored by the renderer."""
        record = _record(tick_index=12345,
                         tick_time=datetime(2026, 3, 4, 9, 15, 30, tzinfo=timezone.utc))
        rendered = AbstractLogger.render_record(record, run_start=_START)
        assert rendered == self._as_before(record, _START)
        assert '12345|' in rendered

    def test_without_a_run_start_the_absolute_form_is_used(self):
        """The parent process prints without a run start when it has none."""
        assert '2026-08-29 12:00:03' in AbstractLogger.render_record(_record())

    def test_the_elapsed_form_stays_a_fixed_width_column(self):
        assert format_log_elapsed(3.417) == '[  3s 416ms]'
        assert format_log_elapsed(0.005) == '[  0s   5ms]'


class TestADisplaySettingCannotHideAReportInput:
    """
    The buffer used to be filled only when the CONSOLE threshold passed, so raising the console
    level silently removed warnings from the run report. WARNING and ERROR are report input and
    are now captured either way; everything else still follows the console setting.
    """

    @staticmethod
    def _logger(tmp_path: Path) -> ScenarioLogger:
        return ScenarioLogger(
            scenario_set_name='t', scenario_name='s1',
            run_timestamp=datetime.now(timezone.utc), log_root_override=tmp_path)

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
