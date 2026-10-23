"""
The run directory is the run's record (#621): the scenario logger creates it whether or not log
files are written, and the log switch decides the log files alone.
"""

from datetime import datetime, timezone

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.utils.run_id_utils import mint_run_id


def _logger(tmp_path) -> ScenarioLogger:
    """
    A run-level logger below a temporary root.

    Args:
        tmp_path: The root the run directory is created in

    Returns:
        The logger
    """
    now = datetime.now(timezone.utc)
    return ScenarioLogger(scenario_set_name='my_set', scenario_name='summary',
                          run_timestamp=now, run_id=mint_run_id(now), log_root_override=tmp_path)


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
