"""
The run directory is the run's record (#621): the scenario logger creates it whether or not log
files are written, and the log switch decides the log files alone. Which file a logger writes is
its member of the run's declaration; a logger without one writes none.
"""

from datetime import datetime, timezone

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.run_layout_constants import (
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.run_id_utils import mint_run_id


def _logger(tmp_path, **file_args) -> ScenarioLogger:
    """
    A run-level logger below a temporary root.

    Args:
        tmp_path: The root the run directory is created in
        file_args: Which declared log it writes; the backtest summary when none is given

    Returns:
        The logger
    """
    now = datetime.now(timezone.utc)
    file_args = file_args or {'log_member': RunEntryMemberId.BACKTEST_SUMMARY_LOG,
                              'run_type': RUN_TYPE_SIMULATION}
    return ScenarioLogger(scenario_set_name='my_set', scenario_name='summary',
                          run_timestamp=now, run_id=mint_run_id(now), log_root_override=tmp_path,
                          **file_args)


def _relative_files(logger: ScenarioLogger) -> list:
    """The files in the logger's run directory, relative to it, with forward slashes."""
    return sorted(path.relative_to(logger.run_dir).as_posix()
                  for path in logger.run_dir.rglob('*') if path.is_file())


class TestTheRunDirectoryIsTheRecord:
    """The directory exists either way; only the log file depends on the switch."""

    def test_with_log_files_on_the_directory_holds_the_log(self, tmp_path):
        logger = _logger(tmp_path)
        assert logger.run_dir is not None and logger.run_dir.is_dir()
        assert [p.name for p in logger.run_dir.iterdir() if p.name.endswith('_summary.log')]
        logger.close()

    def test_with_log_files_off_the_directory_is_still_created(self, tmp_path, monkeypatch):
        real = AppConfigManager().get_file_logging_config_object()
        switched_off = real.model_copy(
            update={'scenario': real.scenario.model_copy(update={'enabled': False})})
        monkeypatch.setattr(AppConfigManager, 'get_file_logging_config_object',
                            lambda self: switched_off)
        logger = _logger(tmp_path)
        assert logger.run_dir is not None and logger.run_dir.is_dir()
        assert list(logger.run_dir.iterdir()) == [], 'no log file without the switch'
        assert logger.file_logger is None
        logger.close()


class TestTheFileIsTheDeclaredOne:
    """A logger writes the file its member names, and none without a member."""

    @pytest.mark.parametrize('member_id, run_type, names', [
        (RunEntryMemberId.BACKTEST_SUMMARY_LOG, RUN_TYPE_SIMULATION, {}),
        (RunEntryMemberId.SCENARIO_LOG, RUN_TYPE_SIMULATION,
         {'scenario_no': '01', 'scenario': 'window_01'}),
        (RunEntryMemberId.SESSION_BOOT_LOG, RUN_TYPE_AUTOTRADER, {}),
    ])
    def test_the_file_matches_its_member(self, tmp_path, member_id, run_type, names):
        logger = _logger(tmp_path, log_member=member_id, run_type=run_type, log_names=names)
        logger.info('one line')
        logger.close()

        [written] = _relative_files(logger)
        assert RUN_ENTRY_MEMBERS_BY_ID[member_id].matches(written)

    def test_a_logger_without_a_member_writes_no_file(self, tmp_path):
        logger = _logger(tmp_path, log_member=None)
        logger.info('one line')
        logger.close()

        assert logger.run_dir.is_dir() and _relative_files(logger) == []

    def test_a_log_of_the_other_pipeline_is_refused(self, tmp_path):
        with pytest.raises(EntryMemberRefusedError):
            _logger(tmp_path, log_member=RunEntryMemberId.SESSION_BOOT_LOG,
                    run_type=RUN_TYPE_SIMULATION)
