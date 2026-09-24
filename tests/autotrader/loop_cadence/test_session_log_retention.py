"""
Loop Cadence — Session-Log Retention (#357)

The live loop rotates its session log at the trading-day boundary and, until this landed,
kept every rotated day for as long as the session ran. A thirty-day run is thirty files that
nobody removes, and the pressure arrives in the middle of the one run the project exists to
complete rather than at its start.

What these cases pin is mostly what must NOT happen. A retention rule is a deletion that
runs unattended, so the interesting assertions are the ones about files that stay: the active
day, anything inside the window, and — above all — every file whose name this rule was not
written for.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.session_log_retention import (
    SESSION_LOGS_SUBDIR,
    prune_rotated_session_logs,
)

CURRENT_DAY = '20260924'


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A run directory with its session_logs/ subdirectory. Returns: the run directory."""
    (tmp_path / SESSION_LOGS_SUBDIR).mkdir()
    return tmp_path


def _write(run_dir: Path, *names: str) -> None:
    for name in names:
        (run_dir / SESSION_LOGS_SUBDIR / name).write_text('log\n')


def _remaining(run_dir: Path) -> set:
    return {p.name for p in (run_dir / SESSION_LOGS_SUBDIR).iterdir()}


def _log(name: str) -> str:
    return f'autotrader_session_{name}.log'


class TestRetentionWindow:
    """What goes and what stays, measured against the session's own trading day."""

    def test_older_than_the_window_goes_recent_stays(self, run_dir):
        _write(run_dir,
               _log('20260801'), _log('20260820'), _log('20260901'),
               _log('20260923'), _log(CURRENT_DAY))

        removed = prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, MagicMock())

        assert set(removed) == {_log('20260801'), _log('20260820')}
        assert _remaining(run_dir) == {
            _log('20260901'), _log('20260923'), _log(CURRENT_DAY)}

    def test_the_boundary_day_is_kept(self, run_dir):
        """
        Exactly `retention_days` old is INSIDE the window. Stated as its own case because
        an off-by-one here deletes a day the operator was promised — and the promise is
        'keep 30 days', not 'keep 29'.
        """
        _write(run_dir, _log('20260825'), _log('20260824'))

        removed = prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, MagicMock())

        assert removed == [_log('20260824')], 'the 30-day-old file was not the only one removed'
        assert _log('20260825') in _remaining(run_dir)

    def test_the_active_file_is_never_removed(self, run_dir):
        """Even at a retention of one day, and even when it is the only file there."""
        _write(run_dir, _log(CURRENT_DAY))

        assert prune_rotated_session_logs(run_dir, CURRENT_DAY, 1, MagicMock()) == []
        assert _remaining(run_dir) == {_log(CURRENT_DAY)}

    def test_a_file_dated_after_the_current_day_is_left_alone(self, run_dir):
        """
        A forex session's trading day can move backwards relative to a file written under a
        different anchor. Whatever produced it, a future-dated file is not something this
        rule understands — and what it does not understand, it does not delete.
        """
        _write(run_dir, _log('20261001'))

        assert prune_rotated_session_logs(run_dir, CURRENT_DAY, 1, MagicMock()) == []
        assert _remaining(run_dir) == {_log('20261001')}

    def test_zero_keeps_everything(self, run_dir):
        """The documented off switch — `0` must not read as 'keep nothing'."""
        _write(run_dir, _log('20240101'), _log('20260801'))

        assert prune_rotated_session_logs(run_dir, CURRENT_DAY, 0, MagicMock()) == []
        assert len(_remaining(run_dir)) == 2


class TestNothingElseIsTouched:
    """The half that matters most: this rule deletes rotated session logs and nothing else."""

    def test_foreign_files_survive_however_old_they_look(self, run_dir):
        _write(run_dir,
               'autotrader_summary.log', 'events.csv', 'field_study.jsonl',
               'autotrader_session_20260801.log.gz', 'session_20260801.log',
               'autotrader_session_2026080.log', _log('20260801'))

        removed = prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, MagicMock())

        assert removed == [_log('20260801')]
        assert _remaining(run_dir) == {
            'autotrader_summary.log', 'events.csv', 'field_study.jsonl',
            'autotrader_session_20260801.log.gz', 'session_20260801.log',
            'autotrader_session_2026080.log'}

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        """A session that never rotated has no directory, and that is not a failure."""
        assert prune_rotated_session_logs(tmp_path, CURRENT_DAY, 30, MagicMock()) == []


class TestItSaysWhatItDid:
    """A silent deletion is the one nobody can explain afterwards (§35)."""

    def test_a_removal_is_reported_with_the_names(self, run_dir):
        logger = MagicMock()
        _write(run_dir, _log('20260801'), _log('20260802'))

        prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, logger)

        message = ' '.join(str(c) for c in logger.info.call_args_list)
        assert '20260801' in message and '20260802' in message
        assert '30' in message, 'the window that caused the deletion is not in the line'

    def test_nothing_removed_says_nothing(self, run_dir):
        """A daily line saying it deleted nothing would train the reader to skip it."""
        logger = MagicMock()
        _write(run_dir, _log(CURRENT_DAY))

        prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, logger)

        logger.info.assert_not_called()

    def test_a_file_that_cannot_be_removed_reaches_the_pot(self, run_dir, monkeypatch):
        """
        The failure must not end the session — the remaining files are still worth removing
        — but it must not disappear either, or the disk fills with nobody noticing.
        """
        logger = MagicMock()
        _write(run_dir, _log('20260801'), _log('20260802'))

        real_unlink = Path.unlink

        def refuse_one(self, *args, **kwargs):
            if self.name == _log('20260801'):
                raise OSError('device busy')
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'unlink', refuse_one)
        removed = prune_rotated_session_logs(run_dir, CURRENT_DAY, 30, logger)

        assert removed == [_log('20260802')], 'the second file was not removed after the first failed'
        warning = ' '.join(str(c) for c in logger.warning.call_args_list)
        assert '20260801' in warning
