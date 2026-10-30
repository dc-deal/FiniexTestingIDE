"""
Where an AutoTrader session says it came from, and which code it runs (#551).

The AutoTrader header site differs from the simulation's in one way that shapes everything here: the
code identity is captured BEFORE the header and KEPT, because the startup guard asks it whether
real orders would run from uncommitted code — and that question must not depend on a run
directory having been created. So these tests pin four things: the session captures over the
profile's own strategy and hands origin and identity to the header; the CLI declares its channel
and carries `--allow-dirty` through; a host identity that cannot be trusted is a REFUSAL at the
command line, not a crash, and it arrives before any capture; and a capture that fails ends as
STARTUP FAILED with a header, never as a stack trace before the session has a record. The last
two run the REAL session through the REAL command line.

The capture itself is replaced — its git reads are pinned against temporary repositories in
`tests/framework/reporting/test_code_identity.py`; what is under test here is the wiring.
"""

import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from python.cli import autotrader_cli
from python.configuration import host_identity_manager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.startup.autotrader_startup import create_autotrader_loggers
from python.framework.constants.run_layout_constants import RUN_HEADER_ARTIFACT
from python.framework.exceptions.data_home_errors import DirectoryCreationDeniedError
from python.framework.exceptions.host_identity_errors import HostIdentityError
from python.framework.exceptions.persistence_errors import BotAlreadyRunningError
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.reporting.io.run_header_io import read_run_header
from python.framework.types.config_types.host_identity_config_types import TEST_HOST_ID
from python.framework.types.run_origin_types import (
    CONSOLE_CLIENT,
    OPERATOR_PRINCIPAL,
    CodeIdentity,
    RepositoryState,
    RunChannel,
    RunOrigin,
)

PROFILE = 'configs/autotrader_profiles/mock/mock_session_test.json'

_IDENTITY = CodeIdentity(framework=RepositoryState(root='/app', commit='abc1234'))


class _StopAtLoggers(Exception):
    """Raised by the stand-in logger factory: everything this suite asks happens before it."""


@pytest.fixture
def stopped_session(monkeypatch):
    """
    Run a session only up to its logger factory and record what reached it.

    Returns:
        (captured strategy configs, the keyword arguments the logger factory received)
    """
    captures = []
    received = {}

    def fake_capture(strategy_configs):
        captures.append(strategy_configs)
        return _IDENTITY

    def fake_loggers(config, run_timestamp, **kwargs):
        received.update(kwargs)
        raise _StopAtLoggers()

    monkeypatch.setattr(autotrader_main_module, 'capture_code_identity', fake_capture)
    monkeypatch.setattr(autotrader_main_module, 'create_autotrader_loggers', fake_loggers)
    return captures, received


class TestTheSessionStatesItsOrigin:

    def test_a_session_constructed_in_code_is_direct(self, stopped_session):
        _, received = stopped_session
        trader = AutotraderMain(load_autotrader_config(PROFILE))

        with pytest.raises(_StopAtLoggers):
            trader.run()

        origin = received['origin']
        assert origin.channel is RunChannel.DIRECT and origin.allow_dirty is False
        assert origin.client == CONSOLE_CLIENT and origin.principal == OPERATOR_PRINCIPAL
        assert origin.host == TEST_HOST_ID

    def test_the_declared_channel_and_override_reach_the_header(self, stopped_session):
        _, received = stopped_session
        trader = AutotraderMain(load_autotrader_config(PROFILE), channel=RunChannel.CLI,
                                allow_dirty=True)

        with pytest.raises(_StopAtLoggers):
            trader.run()

        assert received['origin'].channel is RunChannel.CLI
        assert received['origin'].allow_dirty is True
        assert received['origin'].first_start is False

    def test_a_declared_first_start_reaches_the_header(self, stopped_session):
        """#598: the one way a real-money session starts without a carry-over is recorded."""
        _, received = stopped_session
        trader = AutotraderMain(load_autotrader_config(PROFILE), first_start=True)

        with pytest.raises(_StopAtLoggers):
            trader.run()

        assert received['origin'].first_start is True

    def test_an_accepted_composite_change_reaches_the_header(self, stopped_session):
        """#626: one start's acceptance of a changed configuration is recorded as typed."""
        _, received = stopped_session
        trader = AutotraderMain(load_autotrader_config(PROFILE),
                                accept_composite_change='7f3a9c2e')

        with pytest.raises(_StopAtLoggers):
            trader.run()

        assert received['origin'].accept_composite_change == '7f3a9c2e'
        assert received['origin'].first_start is False

    def test_the_identity_is_captured_over_the_profile_before_the_header(
            self, stopped_session):
        """Captured BEFORE the header exists — the guard must not depend on one."""
        captures, received = stopped_session
        config = load_autotrader_config(PROFILE)
        trader = AutotraderMain(config)

        with pytest.raises(_StopAtLoggers):
            trader.run()

        assert captures == [[config.strategy_config]]
        assert received['code_identity'] is _IDENTITY


class TestTheLiveHeaderCarriesBoth:

    def test_the_header_written_at_the_start_holds_origin_and_identity(self):
        """A session killed before its close still says who started it and which code it ran."""
        origin = RunOrigin(channel=RunChannel.CLI, client=CONSOLE_CLIENT,
                           principal=OPERATOR_PRINCIPAL, host=TEST_HOST_ID)
        config = load_autotrader_config(PROFILE)

        bundle = create_autotrader_loggers(
            config, datetime.now(timezone.utc), origin=origin, code_identity=_IDENTITY)

        header = read_run_header(bundle.run_dir / RUN_HEADER_ARTIFACT)
        assert header.origin == origin
        assert header.code_identity == _IDENTITY


class _RecordingTrader:
    """Stands in for AutotraderMain at the command line; records how it was constructed."""

    constructed = {}
    failure = None

    def __init__(self, config, **kwargs):
        _RecordingTrader.constructed = kwargs

    def run(self):
        if _RecordingTrader.failure is not None:
            raise _RecordingTrader.failure
        return SimpleNamespace(get_exit_code=lambda: 0)


def _run_cli(monkeypatch, *flags: str) -> int:
    """
    Invoke the AutoTrader CLI's `run` command with a stand-in session.

    Args:
        monkeypatch: pytest's monkeypatch
        flags: Extra command-line flags

    Returns:
        The exit code the CLI ended with
    """
    monkeypatch.setattr(autotrader_cli, 'load_autotrader_config', lambda path: SimpleNamespace())
    monkeypatch.setattr(autotrader_cli, 'AutotraderMain', _RecordingTrader)
    monkeypatch.setattr(sys, 'argv', ['autotrader_cli.py', 'run', '--config', PROFILE, *flags])
    with pytest.raises(SystemExit) as exit_info:
        autotrader_cli.main()
    return exit_info.value.code


class TestTheCommandLineDeclaresItself:

    @pytest.fixture(autouse=True)
    def _reset(self):
        _RecordingTrader.constructed = {}
        _RecordingTrader.failure = None
        yield
        _RecordingTrader.failure = None

    def test_the_cli_declares_its_channel_and_allows_nothing_by_default(self, monkeypatch):
        assert _run_cli(monkeypatch) == 0
        assert _RecordingTrader.constructed['channel'] is RunChannel.CLI
        assert _RecordingTrader.constructed['allow_dirty'] is False
        assert _RecordingTrader.constructed['first_start'] is False
        assert _RecordingTrader.constructed['accept_composite_change'] is None

    def test_accept_composite_change_is_carried_through(self, monkeypatch):
        assert _run_cli(monkeypatch, '--accept-composite-change', '7f3a9c2e') == 0
        assert _RecordingTrader.constructed['accept_composite_change'] == '7f3a9c2e'

    def test_first_start_is_carried_through(self, monkeypatch):
        assert _run_cli(monkeypatch, '--first-start') == 0
        assert _RecordingTrader.constructed['first_start'] is True

    def test_a_second_start_of_a_running_bot_is_a_refusal_not_a_crash(self, monkeypatch, capsys):
        """#598: exit 2, which a supervisor does not restart — the running session goes on."""
        _RecordingTrader.failure = BotAlreadyRunningError(
            "STARTUP REFUSED — bot 'mybot01' on ETHUSD is already running")

        assert _run_cli(monkeypatch) == 2
        output = capsys.readouterr().out
        assert 'is already running' in output and 'Traceback' not in output

    def test_allow_dirty_is_carried_through(self, monkeypatch):
        assert _run_cli(monkeypatch, '--allow-dirty') == 0
        assert _RecordingTrader.constructed['allow_dirty'] is True

    def test_an_untrusted_host_identity_is_a_refusal_not_a_crash(self, monkeypatch, capsys):
        """Same exit code as the other refusals: the session did not start, and nothing broke."""
        _RecordingTrader.failure = HostIdentityError('host identity file cannot be trusted')

        assert _run_cli(monkeypatch) == 2
        output = capsys.readouterr().out
        assert 'host identity file cannot be trusted' in output
        assert 'Traceback' not in output

    def test_a_refused_run_directory_is_a_refusal_not_a_crash(self, monkeypatch, capsys):
        """#621: the run directory holds the record, so its refusal ends the start — exit 2, no trace."""
        _RecordingTrader.failure = DirectoryCreationDeniedError(
            'OS Permission Error — directory creation denied: runs/autotrader/my_profile/run_01')

        assert _run_cli(monkeypatch) == 2
        output = capsys.readouterr().out
        assert 'directory creation denied' in output and 'Traceback' not in output


def _run_real_cli(monkeypatch) -> int:
    """
    Invoke the AutoTrader CLI's `run` command with the REAL session behind it.

    Args:
        monkeypatch: pytest's monkeypatch

    Returns:
        The exit code the CLI ended with
    """
    monkeypatch.setattr(sys, 'argv', ['autotrader_cli.py', 'run', '--config', PROFILE])
    with pytest.raises(SystemExit) as exit_info:
        autotrader_cli.main()
    return exit_info.value.code


class TestARefusedStartThroughTheRealSession:
    """
    The whole path — CLI, the real `AutotraderMain.run()`, its startup handling — for the two
    failures that arrive before the session's loggers exist. Neither may end as a stack trace.
    """

    @pytest.fixture(autouse=True)
    def _fresh_host_cache(self):
        HostIdentityManager.clear_cache()
        yield
        HostIdentityManager.clear_cache()

    def test_a_stored_level_refuses_the_start_and_leaves_the_book_alone(
            self, monkeypatch, capsys, tmp_path):
        """
        Exit 2, which a supervisor does not restart, because the refusal would only repeat. It reads
        as STARTUP FAILED and never as a stack trace, and the book is still there for the operator.
        """
        config = load_autotrader_config(PROFILE)
        assert config.cold_start.enabled, 'fixture: the carry-over is read at all'
        config.cold_start.path = str(tmp_path)
        book = tmp_path / f'{carry_over_key(config.profile_name, config.symbol, config.bot_id)}.json'
        book.write_text(json.dumps({
            'schema_version': 1, 'store_id': 'cold_start_state',
            'saved_at_utc': '2026-10-01T00:00:00+00:00', 'written_by_run_id': 'my_run',
            'profile': config.profile_name, 'symbol': config.symbol, 'bot_id': config.bot_id,
            'carry_over_from': '',
            'snapshot': {'open_positions': [{
                'position_id': 'pos_1', 'symbol': config.symbol, 'direction': 'long', 'lots': 0.01,
                'original_lots': 0.01, 'entry_price': 50000.0,
                'entry_time': '2026-10-01T00:00:00+00:00', 'entry_type': 'market',
                'entry_tick_value': 1.0, 'entry_bid': 49999.0, 'entry_ask': 50001.0,
                'stop_loss': 49000.0, 'take_profit': None, 'broker_ref': 'OABC-123', 'comment': '',
                'status': 'open', 'digits': 1, 'contract_size': 1.0, 'pip_size': 0.1,
                'price_unit': 'USD', 'entry_tick_index': 0, 'mae_pnl': 0.0, 'mfe_pnl': 0.0,
                'mae_price': 0.0, 'mfe_price': 0.0}]},
        }), encoding='utf-8')
        before = book.read_bytes()
        monkeypatch.setattr(autotrader_cli, 'load_autotrader_config', lambda _path: config)

        assert _run_real_cli(monkeypatch) == 2

        output = capsys.readouterr().out
        assert 'STARTUP FAILED' in output and 'stop_loss on the position' in output
        assert 'Traceback' not in output
        assert book.read_bytes() == before

    def test_an_untrusted_host_identity_file_refuses_before_any_capture(
            self, monkeypatch, capsys, tmp_path):
        """
        Refused at the origin, BEFORE the code identity is captured and before the header: a
        header must not state an identity nobody trusts, and no patch is stored for a session
        that never starts.
        """
        broken = tmp_path / 'host_identity.json'
        broken.write_text('{"host_id": ', encoding='utf-8')
        monkeypatch.setattr(host_identity_manager, 'is_config_isolation_active', lambda: False)
        monkeypatch.setattr(host_identity_manager, '_HOST_IDENTITY_PATH', str(broken))
        captured, headers = [], []
        monkeypatch.setattr(autotrader_main_module, 'capture_code_identity',
                            lambda configs: captured.append(configs) or _IDENTITY)
        monkeypatch.setattr(autotrader_main_module, 'create_autotrader_loggers',
                            lambda *args, **kwargs: headers.append(kwargs))

        assert _run_real_cli(monkeypatch) == 2

        output = capsys.readouterr().out
        assert str(broken) in output
        assert 'Traceback' not in output
        assert captured == [] and headers == []

    def test_a_capture_that_fails_ends_as_startup_failed_with_a_record(
            self, monkeypatch, capsys):
        """
        The capture runs before the loggers, so an exception there used to escape `run()` with
        exit 1, a stack trace, no STARTUP FAILED and no header. It is held, the header is written
        without a code identity, and the startup handling refuses the session.
        """
        def disk_full(configs):
            raise OSError(28, 'No space left on device')

        bundles = []
        real_loggers = autotrader_main_module.create_autotrader_loggers

        def recording_loggers(*args, **kwargs):
            bundle = real_loggers(*args, **kwargs)
            bundles.append(bundle)
            return bundle

        monkeypatch.setattr(autotrader_main_module, 'capture_code_identity', disk_full)
        monkeypatch.setattr(autotrader_main_module, 'create_autotrader_loggers',
                            recording_loggers)

        assert _run_real_cli(monkeypatch) == 2

        output = capsys.readouterr().out
        assert 'STARTUP FAILED' in output
        assert 'could not be captured (OSError' in output
        assert 'Traceback' not in output
        [bundle] = bundles
        header = read_run_header(bundle.run_dir / RUN_HEADER_ARTIFACT)
        assert header.origin is not None and header.code_identity is None
        global_log = (bundle.run_dir / 'autotrader_global.log').read_text(encoding='utf-8')
        assert 'Code identity capture failed' in global_log and 'No space left' in global_log
