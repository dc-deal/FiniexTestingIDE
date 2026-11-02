"""
One bot runs once (#598): a session holds a kernel lock beside its carry-over for as long as it runs,
and a second start of the same bot is refused while the first lives — whichever code tree it was
started from, as long as both share one data home.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.data_home import DATA_HOME_ENV
from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.startup.bot_instance_lock import (
    bot_lock_path,
    record_holder_run,
    take_bot_lock,
)
from python.framework.exceptions.persistence_errors import BotAlreadyRunningError
from python.framework.utils.file_lock import is_lock_held

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MOCK_PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'

# A process that takes the lock of the bot below, says so, and then waits to be killed.
_HOLDER = (
    'import sys, time\n'
    'from types import SimpleNamespace\n'
    'from python.framework.autotrader.startup.bot_instance_lock import take_bot_lock\n'
    'class Config:\n'
    '    cold_start = SimpleNamespace(path=sys.argv[1])\n'
    '    bot_id = "mybot01"\n'
    '    symbol = "ETHUSD"\n'
    '    def get_unit_name(self):\n'
    '        return "my_profile"\n'
    'lock = take_bot_lock(Config())\n'
    'print("HOLDING", flush=True)\n'
    'time.sleep(120)\n'
)


class _Config:
    """The parts of a resolved profile the lock reads."""

    def __init__(self, cold_start_path: str, bot_id: str = 'mybot01', symbol: str = 'ETHUSD'):
        self.cold_start = SimpleNamespace(path=cold_start_path)
        self.bot_id = bot_id
        self.symbol = symbol

    def get_unit_name(self) -> str:
        return 'my_profile'


class TestOneSessionPerBot:

    def test_a_second_start_is_refused_while_the_first_holds_it(self, tmp_path):
        config = _Config(str(tmp_path))
        lock = take_bot_lock(config)
        try:
            with pytest.raises(BotAlreadyRunningError) as refused:
                take_bot_lock(config)
            message = str(refused.value)
            assert "bot 'mybot01' on ETHUSD is already running" in message
            assert f'process {os.getpid()}' in message
            assert str(bot_lock_path(config)) in message
        finally:
            lock.release()

    def test_the_refusal_names_the_holders_run_once_it_is_minted(self, tmp_path):
        config = _Config(str(tmp_path))
        lock = take_bot_lock(config)
        try:
            record_holder_run(lock, '20261020_120000_abcd1234')
            with pytest.raises(BotAlreadyRunningError, match='20261020_120000_abcd1234'):
                take_bot_lock(config)
        finally:
            lock.release()

    def test_another_bot_on_the_same_machine_starts(self, tmp_path):
        lock = take_bot_lock(_Config(str(tmp_path)))
        other = take_bot_lock(_Config(str(tmp_path), bot_id='mybot02'))
        other.release()
        lock.release()

    def test_the_lock_is_free_once_the_session_releases_it(self, tmp_path):
        config = _Config(str(tmp_path))
        take_bot_lock(config).release()
        take_bot_lock(config).release()

    def test_the_lock_file_is_readable_by_its_owner_alone(self, tmp_path):
        config = _Config(str(tmp_path))
        lock = take_bot_lock(config)
        try:
            assert bot_lock_path(config).stat().st_mode & 0o777 == 0o600
        finally:
            lock.release()


class TestAHolderInAnotherProcess:

    def test_is_refused_while_it_lives_and_free_the_moment_it_is_killed(self, tmp_path):
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        holder = subprocess.Popen([sys.executable, '-c', _HOLDER, str(tmp_path)], cwd=PROJECT_ROOT,
                                  env=env, stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == 'HOLDING'
            with pytest.raises(BotAlreadyRunningError, match=f'process {holder.pid}'):
                take_bot_lock(_Config(str(tmp_path)))
            holder.send_signal(signal.SIGKILL)
            holder.wait(timeout=10)
            take_bot_lock(_Config(str(tmp_path))).release()
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)


class TestTwoCodeTrees:

    def test_share_one_lock_through_one_data_home(self, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir()
        tree_a, tree_b = tmp_path / 'v1.5.0', tmp_path / 'v1.5.1'
        tree_a.mkdir()
        tree_b.mkdir()
        monkeypatch.setenv(DATA_HOME_ENV, str(home))
        config = _Config('data/runtime/cold_start_state')

        monkeypatch.chdir(tree_a)
        lock = take_bot_lock(config)
        try:
            monkeypatch.chdir(tree_b)
            with pytest.raises(BotAlreadyRunningError):
                take_bot_lock(config)
            assert bot_lock_path(config).is_relative_to(home)
        finally:
            lock.release()

    def test_without_a_home_each_tree_has_a_lock_of_its_own(self, tmp_path, monkeypatch):
        monkeypatch.delenv(DATA_HOME_ENV, raising=False)
        tree_a, tree_b = tmp_path / 'v1.5.0', tmp_path / 'v1.5.1'
        tree_a.mkdir()
        tree_b.mkdir()
        config = _Config('data/runtime/cold_start_state')

        monkeypatch.chdir(tree_a)
        lock = take_bot_lock(config)
        try:
            monkeypatch.chdir(tree_b)
            take_bot_lock(config).release()
        finally:
            lock.release()


class TestASessionHoldsIt:
    """The real session: the lock is taken before anything is read, and given back at its end."""

    def test_held_while_the_session_starts_and_free_once_it_has_ended(self, monkeypatch):
        config = load_autotrader_config(MOCK_PROFILE)
        held_during_the_start = []

        def failing_capture(strategy_configs, **_):
            # Refused at its start — cheap, and a path that must still give the lock back
            held_during_the_start.append(is_lock_held(bot_lock_path(config)))
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(autotrader_main_module, 'capture_code_identity', failing_capture)
        AutotraderMain(config).run()
        assert held_during_the_start == [True]
        assert not is_lock_held(bot_lock_path(config))

    def test_a_second_start_is_refused_before_anything_is_read(self, monkeypatch):
        config = load_autotrader_config(MOCK_PROFILE)
        read = []
        monkeypatch.setattr(autotrader_main_module, 'read_carry_over',
                            lambda cfg: read.append(cfg) or None)
        holder = take_bot_lock(config)
        try:
            with pytest.raises(BotAlreadyRunningError):
                AutotraderMain(config).run()
            assert read == [], 'the carry-over was read before the refusal'
        finally:
            holder.release()
