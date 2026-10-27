"""
create_directory (#621) — the one place the IDE creates a directory, and what a refusal says.
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.data_home_errors import DirectoryCreationDeniedError
from python.framework.logging import scenario_logger
from python.framework.utils.file_utils import create_directory
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_strategy_runner import initialize_batch_and_run

_HOLDS = "the run's record: header, reports and streams"


class TestCreatingADirectory:
    """The directory exists afterwards, with its parents; what was there stays."""

    def test_the_directory_and_its_parents_are_created(self, tmp_path):
        target = tmp_path / 'runs' / 'autotrader' / 'my_profile' / 'run_01'
        assert create_directory(target, holds=_HOLDS) == target
        assert target.is_dir()

    def test_an_existing_directory_is_left_as_it_is(self, tmp_path):
        target = tmp_path / 'io'
        target.mkdir()
        (target / 'portfolio.json').write_text('{}', encoding='utf-8')
        create_directory(target, holds=_HOLDS)
        assert (target / 'portfolio.json').read_text(encoding='utf-8') == '{}'

    def test_a_new_directory_gets_the_mode_asked_for(self, tmp_path):
        target = tmp_path / 'activities'
        create_directory(target, holds='the list of what runs on this machine', mode=0o700)
        assert target.stat().st_mode & 0o777 == 0o700


class TestARefusalIsAHardErrorThatSaysWhy:
    """The message opens with the cause, then names the path, what it was to hold and where to look."""

    def test_a_permission_refusal_names_the_cause_first(self, tmp_path, monkeypatch):
        target = tmp_path / 'runs' / 'run_01'

        def refuse(self, *args, **kwargs):
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(Path, 'mkdir', refuse)
        with pytest.raises(DirectoryCreationDeniedError) as caught:
            create_directory(target, holds=_HOLDS, setting='file_logging.run_logs')
        message = str(caught.value)
        assert message.startswith(f'OS Permission Error — directory creation denied: {target}\n')
        assert '[Errno 13] Permission denied' in message
        assert _HOLDS in message and 'file_logging.run_logs' in message
        assert isinstance(caught.value.__cause__, PermissionError)

    def test_any_other_refusal_names_the_os_error(self, tmp_path):
        # A file standing where a parent directory should be: a real refusal, nothing patched.
        blocker = tmp_path / 'runs'
        blocker.write_text('not a directory', encoding='utf-8')
        target = blocker / 'run_01'
        with pytest.raises(DirectoryCreationDeniedError) as caught:
            create_directory(target, holds=_HOLDS)
        assert str(caught.value).startswith(f'OS Error — directory creation denied: {target}\n')

    def test_a_caller_that_carries_on_still_catches_it_as_an_os_error(self, tmp_path):
        # The activity list and the liveness stamp warn and carry on; they catch OSError.
        blocker = tmp_path / 'activities'
        blocker.write_text('', encoding='utf-8')
        with pytest.raises(OSError):
            create_directory(blocker / 'sub', holds='the list of what runs on this machine')


class TestARunWhoseDirectoryIsRefusedStops:
    """A backtest refused its run directory ends as a configuration error — the message, no trace."""

    def test_a_backtest_ends_before_any_scenario_runs(self, monkeypatch, capsys):
        def refuse(path, holds, setting='', mode=0o777):
            raise DirectoryCreationDeniedError(
                f'OS Permission Error — directory creation denied: {path}')

        monkeypatch.setattr(scenario_logger, 'create_directory', refuse)
        config = ScenarioConfigLoader().load_config('backtesting/multi_position_test.json')
        with pytest.raises(SystemExit) as exit_info:
            initialize_batch_and_run(config, AppConfigManager())

        output = capsys.readouterr().out
        assert exit_info.value.code == 1, "the strategy runner's code for a startup abort"
        assert 'CONFIGURATION ERROR' in output and 'directory creation denied' in output
        assert 'Traceback' not in output
