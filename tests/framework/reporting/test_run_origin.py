"""
Run origin and code identity in the header, the index and the ledger (#551).

A run header could not say WHO or WHAT started a run, on WHICH machine, or which code outside
this repository it ran. The first was answerable by memory while there was one operator and one
console; the second was not answerable at all for a strategy living in its own repository. This
suite pins where the two new blocks are written and where they are read:

- both header sites — the scenario set and the live session — always state an origin, and state
  a code identity for a run that reports;
- every entry point DECLARES its channel; code that does not say keeps `direct`;
- the run index projects both blocks into flat columns, identically on append and on rebuild;
- the ledger reads its versions, dirty flag and commit from the header — in the run directory the
  caller holds, never through the derived run index — instead of deriving them again;
- a broken host identity refuses a simulation cleanly, as a configuration error;
- a dirty tree's patch is kept beside its code: this repository's in the run-patch store, a
  strategy repository's inside that repository.

The git reads behind a real capture are pinned against temporary repositories in
`test_code_identity.py`; here the capture is mostly replaced, because what is under test is the
wiring. One test runs a REAL capture into a real live header and reads it back through the ledger,
because the ledger's versions depend on the component resolution and nothing else would go red.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from python.cli.strategy_runner_cli import StrategyRunnerCli
from python.configuration import host_identity_manager
from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.autotrader.autotrader_startup import create_autotrader_loggers
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.exceptions.host_identity_errors import HostIdentityError
from python.framework.optimization.optimization_runner import OptimizationRunner
from python.framework.reporting.io.run_header_io import (
    RUN_HEADER_ARTIFACT,
    read_run_header,
    write_run_header,
)
from python.framework.reporting.store import run_provenance_builder
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_provenance_builder import (
    build_run_provenance,
    build_run_provenance_from_session,
)
from python.framework.store.run_patch_store import FOREIGN_PATCH_DIR
from python.framework.types.api.report_types import RunHeader, RunReporting
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.config_types.host_identity_config_types import TEST_HOST_ID
from python.framework.types.git_info_types import GitInfo
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION
from python.framework.types.run_origin_types import (
    CONSOLE_CLIENT,
    OPERATOR_PERSON,
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
    RunChannel,
    RunOrigin,
)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils import git_info_utils
from python.framework.utils.code_identity_builder import clear_package_digest_cache
from python.framework.utils.git_info_utils import clear_git_caches, get_git_commit, get_git_info
from python.framework.utils.run_origin_builder import build_run_origin, capture_code_identity
from python.framework.workers.core.obv_worker import ObvWorker
from python.scenario import scenario_set as scenario_set_module
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from python.scenario.scenario_strategy_runner import initialize_batch_and_run

_START = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)
_SCENARIO_SET = 'backtesting/multi_position_test.json'
_MINI_GRID = 'tests/fixtures/optimization/btcusd_mini_grid.json'
_DECISION = 'user_algos/my_bot/my_strategy.py'
_STRATEGY = {'decision_logic_type': _DECISION,
             'worker_instances': {'rsi_fast': 'CORE/rsi', 'trend': 'CORE/ma_trend'}}
_PROFILE = 'configs/autotrader_profiles/backtesting/mock_session_test.json'


class _RecordingLogger:
    """Stands in for a run's own logger; keeps what the provenance builder warned about."""

    def __init__(self):
        self.warnings: List[str] = []

    def warning(self, message: str) -> None:
        """
        Args:
            message: The warning as logged
        """
        self.warnings.append(message)


def _identity(algos_dirty: bool = False, framework_commit: str = 'abc1234',
              framework_branch: str = 'feature-x') -> CodeIdentity:
    """
    A code identity as a capture would record it: this repository, one algo repository, the
    decision logic and two workers — one of them unresolved.

    Args:
        algos_dirty: Whether the algo repository was uncommitted
        framework_commit: This repository's commit, None for an unreadable one
        framework_branch: The branch the capture read beside that commit

    Returns:
        The identity
    """
    return CodeIdentity(
        framework=RepositoryState(root='/app', commit=framework_commit, branch=framework_branch),
        repositories=[RepositoryState(
            root='/app/user_algos', commit='2f054f5', dirty=algos_dirty,
            uncommitted_count=1 if algos_dirty else 0,
            changes=['?? my_bot/'] if algos_dirty else [],
            diff_hash='9f2c' * 16 if algos_dirty else None)],
        components=[
            ComponentIdentity(role=ComponentRole.DECISION, name=_DECISION, type=_DECISION,
                              version='0.1.0',
                              source_path='/app/user_algos/my_bot/my_strategy.py',
                              repository='/app/user_algos', package_digest='4b1d' * 16),
            ComponentIdentity(role=ComponentRole.WORKER, name='rsi_fast', type='CORE/rsi',
                              version='1.2.0',
                              source_path='python/framework/workers/core/rsi_worker.py',
                              repository='/app'),
            # Unresolvable — the run fails on it by itself; the ledger must not list a version.
            ComponentIdentity(role=ComponentRole.WORKER, name='trend', type='CORE/ma_trend'),
        ])


def _origin(channel: RunChannel = RunChannel.CLI, allow_dirty: bool = False) -> RunOrigin:
    """An origin as the console states it."""
    return RunOrigin(channel=channel, client=CONSOLE_CLIENT, person=OPERATOR_PERSON,
                     host='h_7k2m9q', allow_dirty=allow_dirty)


def _header(run_id: str, run_type: str = RUN_TYPE_SIMULATION, origin: RunOrigin = None,
            code_identity: CodeIdentity = None) -> RunHeader:
    """A run header, with or without the two #551 blocks."""
    return RunHeader(run_id=run_id, start_time=_START, run_type=run_type, run_name='my_set',
                     app_version='1.4.0', git_commit='abc1234', origin=origin,
                     code_identity=code_identity)


class TestTheOriginIsAlwaysStated:
    """Never empty: `console` / `operator` / the minted host for every channel that exists."""

    def test_the_console_states_its_client_person_and_host(self):
        origin = build_run_origin(RunChannel.CLI)

        assert origin.channel is RunChannel.CLI
        assert origin.client == CONSOLE_CLIENT and origin.person == OPERATOR_PERSON
        # The suite runs isolated, so the declared test identity is stated and nothing minted.
        assert origin.host == TEST_HOST_ID
        assert origin.allow_dirty is False

    def test_an_allowed_dirty_start_is_recorded(self):
        """An override nobody can see afterwards is not one."""
        assert build_run_origin(RunChannel.CLI, allow_dirty=True).allow_dirty is True

    def test_a_broken_host_identity_refuses_before_anything_is_stated(self, tmp_path,
                                                                     monkeypatch):
        """A header must not state an identity nobody can trust — and it is never re-minted."""
        broken = tmp_path / 'host_identity.json'
        broken.write_text('{not json', encoding='utf-8')
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '0')
        monkeypatch.setattr(host_identity_manager, '_HOST_IDENTITY_PATH', str(broken))
        HostIdentityManager.clear_cache()
        try:
            with pytest.raises(HostIdentityError):
                build_run_origin(RunChannel.CLI)
        finally:
            HostIdentityManager.clear_cache()
        assert broken.read_text(encoding='utf-8') == '{not json'

    def test_a_broken_host_identity_refuses_a_simulation_as_a_configuration_error(
            self, tmp_path, monkeypatch, capsys):
        """
        A refusal, not a crash (§33): the message and the startup-abort exit code, no stack trace.

        Isolation is lifted for the host identity manager ALONE, so the real file is read and the
        real refusal raised, while every other path of the run stays in the test tree.
        """
        broken = tmp_path / 'host_identity.json'
        broken.write_text('{not json', encoding='utf-8')
        monkeypatch.setattr(host_identity_manager, 'is_config_isolation_active', lambda: False)
        monkeypatch.setattr(host_identity_manager, '_HOST_IDENTITY_PATH', str(broken))
        config = ScenarioConfigLoader().load_config(_SCENARIO_SET)
        simulation_root = Path(AppConfigManager().get_file_logging_config_object().run_logs.simulation)
        before = _run_directories(simulation_root)
        HostIdentityManager.clear_cache()
        try:
            with pytest.raises(SystemExit) as exit_info:
                initialize_batch_and_run(config, AppConfigManager())
        finally:
            HostIdentityManager.clear_cache()

        # The origin is built before the logger creates the run directory, so the refusal leaves
        # nothing shaped like a run behind — measured in review before: a directory holding only
        # its global log and no header, invisible to the index and orphaned for the pruner.
        assert _run_directories(simulation_root) == before

        output = capsys.readouterr()
        assert exit_info.value.code == 1, 'the strategy runner\'s code for a startup abort'
        assert 'CONFIGURATION ERROR' in output.out and str(broken) in output.out
        assert 'Traceback' not in output.out + output.err
        assert 'CRITICAL ERROR' not in output.out and 'Stack Trace' not in output.out
        assert broken.read_text(encoding='utf-8') == '{not json', 'never re-minted'


def _run_directories(root: Path) -> set:
    """
    Every run directory below a run root — `<set>/<run_id>/`.

    Args:
        root: A run root

    Returns:
        The run directories, as paths
    """
    return {path for path in root.glob('*/*') if path.is_dir()} if root.exists() else set()


class TestTheHeaderCarriesBothBlocks:

    def test_both_round_trip(self, tmp_path):
        header = _header('20260924_080000_a1b2c3d4', origin=_origin(),
                         code_identity=_identity(algos_dirty=True))

        assert read_run_header(write_run_header(header, tmp_path)) == header

    def test_a_header_written_before_the_fields_reads_as_unknown(self, tmp_path):
        """No migration: an older run carries neither block, which reads as unknown, not a guess."""
        (tmp_path / RUN_HEADER_ARTIFACT).write_text(json.dumps({
            'run_id': '20260901_120000_aaaaaaaa', 'start_time': '2026-09-01T12:00:00+00:00',
            'run_type': 'simulation', 'run_name': 'my_set'}), encoding='utf-8')

        header = read_run_header(tmp_path / RUN_HEADER_ARTIFACT)

        assert header.origin is None and header.code_identity is None

    def test_an_unreadable_framework_state_counts_as_dirty(self):
        """A guard reading "not dirty" where git could not answer would pass the run it stops."""
        assert _identity(framework_commit=None).is_dirty() is True
        assert _identity().is_dirty() is False
        assert _identity(algos_dirty=True).is_dirty() is True


class TestTheScenarioSetStatesBoth:
    """The simulation's header site — the one every CLI run, sweep combination and test passes."""

    @pytest.fixture
    def captured(self, monkeypatch):
        calls = []

        def fake_capture(strategy_configs):
            calls.append(strategy_configs)
            return _identity()

        monkeypatch.setattr(scenario_set_module, 'capture_code_identity', fake_capture)
        return calls

    @staticmethod
    def _build(**kwargs) -> RunHeader:
        config = ScenarioConfigLoader().load_config(_SCENARIO_SET)
        scenario_set = ScenarioSet(config, AppConfigManager(), **kwargs)
        return read_run_header(scenario_set.logger.get_log_dir() / RUN_HEADER_ARTIFACT)

    def test_code_that_does_not_declare_a_channel_is_direct(self, captured):
        header = self._build()

        assert header.origin.channel is RunChannel.DIRECT
        assert header.origin.host == TEST_HOST_ID

    def test_a_declared_channel_is_what_the_header_says(self, captured):
        assert self._build(channel=RunChannel.SWEEP).origin.channel is RunChannel.SWEEP

    def test_a_reporting_run_captures_over_every_scenario(self, captured):
        config = ScenarioConfigLoader().load_config(_SCENARIO_SET)

        header = self._build()

        assert header.code_identity == _identity()
        assert captured == [[scenario.strategy_config for scenario in config.scenarios]]

    def test_a_run_that_will_not_report_captures_nothing(self, captured):
        """The capture costs a `git status` (§42); a run nobody reports on has no reader for it."""
        header = self._build(reporting=RunReporting.NONE)

        assert header.code_identity is None and captured == []
        assert header.origin is not None, 'the origin is stated whether or not the run reports'


class TestTheEntryPointsDeclareTheirChannel:
    """Declared, never inferred — a heuristic in a provenance field is a guess recorded as a fact."""

    def test_the_strategy_runner_cli_declares_cli(self, monkeypatch):
        seen = {}
        monkeypatch.setattr('python.cli.strategy_runner_cli.run_scenario_batch',
                            lambda name, channel: seen.setdefault('channel', channel))

        StrategyRunnerCli().cmd_run('my_set.json')

        assert seen['channel'] is RunChannel.CLI

    def test_the_optimization_runner_declares_sweep_for_every_combination(self, monkeypatch):
        channels = []

        def fake_run(scenario_config_data, app_config_loader, sweep_context=None, mount=None,
                     sweep_id=None, channel=None):
            channels.append(channel)
            return None

        monkeypatch.setattr(
            AppConfigManager, 'get_optimization_mount_reuse_enabled', lambda self: False)
        monkeypatch.setattr(
            'python.framework.optimization.optimization_runner.initialize_batch_and_run',
            fake_run)

        OptimizationRunner().run(_MINI_GRID)

        assert channels and all(channel is RunChannel.SWEEP for channel in channels)


class TestTheIndexProjectsBothBlocks:
    """Flat columns, identical on append and on rebuild — the index is derived, never a source."""

    _COLUMNS = ['origin_channel', 'origin_person', 'host_id', 'framework_dirty', 'code_dirty']

    @staticmethod
    def _plant(tmp_path: Path):
        roots = RunLogPaths(simulation=tmp_path / 'simulation', live=tmp_path / 'live')
        index = RunIndex(tmp_path / 'runs_index.parquet', roots)
        planted = [
            (_header('20260924_080000_aaaaaaaa', origin=_origin(),
                     code_identity=_identity(algos_dirty=True)),
             roots.simulation / 'my_set' / '20260924_080000_aaaaaaaa'),
            (_header('20260924_080001_bbbbbbbb', RUN_TYPE_LIVE,
                     origin=_origin(RunChannel.DIRECT), code_identity=_identity()),
             roots.live / 'my_profile' / '20260924_080001_bbbbbbbb'),
            # Commissioned not to report: an origin, and no code identity.
            (_header('20260924_080002_cccccccc', origin=_origin(RunChannel.DIRECT)),
             roots.simulation / 'my_set' / '20260924_080002_cccccccc'),
            # Written before the fields existed.
            (_header('20260924_080003_dddddddd'),
             roots.simulation / 'my_set' / '20260924_080003_dddddddd'),
        ]
        for header, run_dir in planted:
            index.register_run(header, run_dir)
        return index

    @classmethod
    def _rows(cls, index: RunIndex):
        frame = index.read().set_index('run_id')[cls._COLUMNS]
        return {run_id: {column: (None if value is None or value != value else value)
                         for column, value in row.items()}
                for run_id, row in frame.to_dict('index').items()}

    def test_the_append_projects_what_the_header_says(self, tmp_path):
        rows = self._rows(self._plant(tmp_path))

        assert rows['20260924_080000_aaaaaaaa'] == {
            'origin_channel': 'cli', 'origin_person': 'operator', 'host_id': 'h_7k2m9q',
            'framework_dirty': False, 'code_dirty': True}
        assert rows['20260924_080001_bbbbbbbb']['code_dirty'] is False
        assert rows['20260924_080002_cccccccc'] == {
            'origin_channel': 'direct', 'origin_person': 'operator', 'host_id': 'h_7k2m9q',
            'framework_dirty': None, 'code_dirty': None}
        assert set(rows['20260924_080003_dddddddd'].values()) == {None}

    def test_the_rebuild_reproduces_the_append(self, tmp_path):
        index = self._plant(tmp_path)
        appended = self._rows(index)

        (tmp_path / 'runs_index.parquet').unlink()
        index.rebuild()

        assert self._rows(index) == appended

    def test_an_unreadable_framework_is_unknown_there_and_dirty_overall(self, tmp_path):
        index = RunIndex(tmp_path / 'runs_index.parquet')
        index.register_run(_header('20260924_080004_eeeeeeee', origin=_origin(),
                                   code_identity=_identity(framework_commit=None)),
                           tmp_path / 'a_run')

        row = self._rows(index)['20260924_080004_eeeeeeee']

        assert row['framework_dirty'] is None and row['code_dirty'] is True

    def test_the_served_run_list_is_unchanged(self, tmp_path):
        """#551 changes the API by the `caller` route and the meaning of `git_dirty` — `RunInfo` does not grow."""
        served = self._plant(tmp_path).list_runs()[0].model_dump()

        assert not set(self._COLUMNS) & set(served)


class TestTheLedgerReadsItsProvenanceFromTheHeader:
    """One derivation of which code ran, at the start — the ledger row is only its reader."""

    @pytest.fixture(autouse=True)
    def _stub_git(self, monkeypatch):
        """
        The ledger still takes the BRANCH from `get_git_info`; stubbed so these tests do not pay
        this tree's `git status` — the dirty flag they check must come from the HEADER, and the
        stub's own `dirty=False` is what proves it does.
        """
        monkeypatch.setattr(run_provenance_builder, 'get_git_info', lambda: GitInfo(
            branch='dev', commit='abc1234', date=_START, message='m', dirty=False,
            uncommitted_count=0))

    @staticmethod
    def _live_header(run_id: str, tmp_path: Path, code_identity: CodeIdentity = None) -> Path:
        """
        Write a live header into a run directory the run index has never heard of.

        Args:
            run_id: The session's id
            tmp_path: Where the run directory is created
            code_identity: The identity the header records, None for none

        Returns:
            The run directory
        """
        run_dir = tmp_path / run_id
        write_run_header(_header(run_id, RUN_TYPE_LIVE, origin=_origin(),
                                 code_identity=code_identity), run_dir)
        return run_dir

    @staticmethod
    def _config() -> AutoTraderConfig:
        return AutoTraderConfig(name='my_profile', symbol='BTCUSD', broker_type='kraken_spot',
                                strategy_config=dict(_STRATEGY))

    def _provenance(self, run_id: str, run_dir, logger=None):
        """
        The live provenance of one session, built as the report coordinator builds it.

        Args:
            run_id: The session's id
            run_dir: Its run directory, or None
            logger: The session's own logger; a recording stand-in when omitted

        Returns:
            The provenance
        """
        return build_run_provenance_from_session(
            self._config(), run_id, _START, None, run_dir=run_dir,
            logger=logger if logger is not None else _RecordingLogger())

    def test_a_live_row_takes_versions_and_dirty_from_the_header(self, tmp_path):
        run_id = '20260924_090000_a1a1a1a1'
        run_dir = self._live_header(run_id, tmp_path, _identity(algos_dirty=True))

        p = self._provenance(run_id, run_dir)

        assert p.decision_version == '0.1.0'
        # The unresolved worker is left out, exactly as a failed resolution used to leave it.
        assert p.worker_versions == {'rsi_fast': '1.2.0'}
        # Wider than it was: THIS repository is clean, the algo repository is not.
        assert p.git_dirty is True
        assert p.git_commit == 'abc1234'

    def test_the_header_is_read_from_the_run_directory_never_through_the_index(self, tmp_path,
                                                                             monkeypatch):
        """
        The run index is derived and deletable (§44) — a session whose index row is gone, or was
        never written, still has its header beside it, and the ledger must find it there.

        The index is made UNUSABLE for the provenance read, not merely empty: an empty index
        would also pass a reader that asks the index first and falls back to the directory,
        which is the shape this test exists to keep out.
        """
        run_id = '20260924_090005_f6f6f6f6'
        run_dir = self._live_header(run_id, tmp_path, _identity())
        assert RunIndex(AppConfigManager().get_file_logging_config_object().run_index).run_dir(
            run_id) is None, 'the premise: no index row names this session'

        def refuse(*_args, **_kwargs):
            raise AssertionError('the provenance read reached the run index')

        monkeypatch.setattr(RunIndex, '__init__', refuse)
        p = self._provenance(run_id, run_dir)

        assert p.decision_version == '0.1.0' and p.worker_versions == {'rsi_fast': '1.2.0'}
        assert p.git_dirty is False

    def test_a_clean_run_is_clean_in_every_repository(self, tmp_path):
        run_id = '20260924_090001_b2b2b2b2'

        p = self._provenance(run_id, self._live_header(run_id, tmp_path, _identity()))

        assert p.git_dirty is False

    def test_a_run_with_no_recorded_identity_is_unknown_never_clean(self, tmp_path):
        """The old default was `False` for "git could not answer" — which claimed a clean tree."""
        run_id = '20260924_090002_c3c3c3c3'
        logger = _RecordingLogger()

        p = self._provenance(run_id, self._live_header(run_id, tmp_path, None), logger)

        assert p.decision_version == '' and p.worker_versions == {}
        assert p.git_dirty is True
        # The row cannot say "unknown" by itself — the run's OWN log says it for the row.
        assert len(logger.warnings) == 1 and 'UNKNOWN' in logger.warnings[0]

    def test_an_unreadable_header_is_reported_in_the_runs_own_log(self, tmp_path):
        """Provenance must never crash the report phase (§33) — and must not degrade silently."""
        run_id = '20260924_090003_d4d4d4d4'
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        (run_dir / RUN_HEADER_ARTIFACT).write_text('{truncated', encoding='utf-8')
        logger = _RecordingLogger()

        p = self._provenance(run_id, run_dir, logger)

        assert p.git_dirty is True and p.decision_version == ''
        assert len(logger.warnings) == 1
        assert 'could not be read' in logger.warnings[0] and 'UNKNOWN' in logger.warnings[0]

    def test_a_session_without_a_run_directory_degrades_instead_of_failing(self):
        logger = _RecordingLogger()

        p = self._provenance('20260924_090006_a7a7a7a7', None, logger)

        assert p.git_dirty is True and p.decision_version == ''
        assert len(logger.warnings) == 1 and 'no run directory' in logger.warnings[0]

    def test_the_commit_is_the_headers_not_a_second_read(self, tmp_path):
        """
        One derivation (#551): where the header's identity names the framework commit, the row
        takes it — together with the branch the capture read beside it, never the process's own
        pair ('abc1234' / 'dev' here), which describes another moment.
        """
        run_id = '20260924_090007_b8b8b8b8'
        run_dir = self._live_header(run_id, tmp_path, _identity(framework_commit='fedcba9'))

        p = self._provenance(run_id, run_dir)

        assert (p.git_commit, p.git_branch) == ('fedcba9', 'feature-x')

    def test_the_branch_is_the_one_captured_beside_the_commit_not_the_one_read_at_the_end(
            self, tmp_path):
        """
        A live row is written at the session's END. The process read at that moment names
        'dev'; the capture at the START read 'feature-x' beside the same commit. The row takes
        the capture's pair, because after thirty days the checkout can be anywhere.
        """
        run_id = '20260924_090008_c9c9c9c9'

        p = self._provenance(run_id, self._live_header(run_id, tmp_path, _identity()))

        assert (p.git_commit, p.git_branch) == ('abc1234', 'feature-x')

    def test_an_unreadable_framework_state_stays_unknown_rather_than_filled_from_a_later_read(
            self, tmp_path):
        """
        A recorded identity whose framework commit git could not read is still the answer: the
        commit stays missing and the row reads dirty, instead of borrowing the process's later
        read, which describes a tree nobody captured.
        """
        run_id = '20260924_090009_d0d0d0d0'
        run_dir = self._live_header(run_id, tmp_path,
                                    _identity(framework_commit=None, framework_branch=None))

        p = self._provenance(run_id, run_dir)

        assert (p.git_commit, p.git_branch) == (None, None)
        assert p.git_dirty is True

    def test_a_simulation_row_reads_the_header_in_its_own_run_directory(self, tmp_path):
        """
        Versions describe the config the row's snapshot records — scenario[0] — although the
        identity spans every scenario of the run.
        """
        run_id = '20260924_090004_e5e5e5e5'
        write_run_header(_header(run_id, origin=_origin(),
                                 code_identity=_identity(algos_dirty=False)), tmp_path)
        other = {'decision_logic_type': 'CORE/simple_consensus', 'worker_instances': {}}
        scenarios = [
            SingleScenario(name='s0', scenario_index=0, symbol='BTCUSD',
                           data_broker_type='kraken_spot', start_date=_START,
                           strategy_config=dict(_STRATEGY)),
            SingleScenario(name='s1', scenario_index=1, symbol='BTCUSD',
                           data_broker_type='kraken_spot', start_date=_START,
                           strategy_config=other),
        ]
        batch = SimpleNamespace(single_scenario_list=scenarios)
        summary_logger = _RecordingLogger()
        scenario_set = SimpleNamespace(
            logger=SimpleNamespace(get_log_dir=lambda: tmp_path),
            printed_summary_logger=summary_logger,
            run_timestamp=_START, scenario_set_name='my_set')

        p = build_run_provenance(batch, scenario_set, run_id)

        assert p.decision_version == '0.1.0'
        assert p.worker_versions == {'rsi_fast': '1.2.0'}
        assert p.git_dirty is False
        assert summary_logger.warnings == []

    def test_an_unknown_simulation_identity_is_reported_in_its_summary_log(self, tmp_path):
        """The same file the live side writes to — the one an operator opens first (§36)."""
        run_id = '20260924_090010_e1e1e1e1'
        write_run_header(_header(run_id, origin=_origin()), tmp_path)
        scenarios = [SingleScenario(name='s0', scenario_index=0, symbol='BTCUSD',
                                    data_broker_type='kraken_spot', start_date=_START,
                                    strategy_config=dict(_STRATEGY))]
        summary_logger = _RecordingLogger()
        scenario_set = SimpleNamespace(
            logger=SimpleNamespace(get_log_dir=lambda: tmp_path),
            printed_summary_logger=summary_logger,
            run_timestamp=_START, scenario_set_name='my_set')

        p = build_run_provenance(SimpleNamespace(single_scenario_list=scenarios), scenario_set,
                                 run_id)

        assert p.git_dirty is True and p.worker_versions == {}
        assert len(summary_logger.warnings) == 1 and 'UNKNOWN' in summary_logger.warnings[0]


def _git(repo: Path, *args: str) -> str:
    """
    Run one git command in a test repository — test setup only.

    Args:
        repo: The repository directory
        args: The git arguments

    Returns:
        The command's standard output, stripped
    """
    result = subprocess.run(
        ['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@test',
         '-c', 'commit.gpgsign=false', *args],
        capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _committed_repo(path: Path, files: dict) -> Path:
    """
    A repository holding one commit of the given files.

    Args:
        path: Where it is created
        files: Relative path → text

    Returns:
        Its top-level directory, as git reports it
    """
    path.mkdir(parents=True)
    _git(path, 'init', '-q')
    for relative, text in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-q', '-m', 'initial')
    return Path(_git(path, 'rev-parse', '--show-toplevel'))


class TestARealCaptureReachesTheLedger:
    """
    A REAL capture, a REAL live header, read back through the ledger (#551).

    The ledger's versions now depend on the component resolution alone: a regression that left a
    CORE component without a source, or skipped the workers, would record empty versions in every
    row and fail nothing. And the framework commit is derived from ONE root — the repository the
    code is imported from — so a process whose cwd lies in another checkout (the worktree trap)
    cannot pair one tree's commit with another tree's state.

    The framework's repository is redirected at its ONE decision point, `get_framework_root`, to a
    temporary one — this tree's `git status` costs ~1.8 s (§42) and its answer depends on what the
    developer has open.
    """

    # The path worker's declared version — distinct from every CORE version, so a mix-up shows.
    _PATH_WORKER_VERSION = '7.3.1'

    @pytest.fixture(autouse=True)
    def _fresh_caches(self):
        """Every read here is cached per process; clear before AND after (§42)."""
        clear_git_caches()
        clear_package_digest_cache()
        yield
        clear_git_caches()
        clear_package_digest_cache()

    @pytest.fixture
    def repos(self, tmp_path, monkeypatch):
        """
        A framework repository standing in for this one, a checkout the cwd sits in, and an algo
        repository holding a path worker — all committed.

        Returns:
            (framework repo, cwd repo, path worker file)
        """
        framework = _committed_repo(tmp_path / 'framework', {'app.py': 'VALUE = 1\n'})
        elsewhere = _committed_repo(tmp_path / 'worktree', {'other.py': 'VALUE = 2\n'})
        worker_source = Path('python/framework/workers/core/rsi_worker.py').read_text(
            encoding='utf-8')
        assert "version='1.0.0'" in worker_source, 'the copy below rewrites this declaration'
        algos = _committed_repo(tmp_path / 'algos', {
            '.gitignore': '__pycache__/\n',
            'my_worker/my_worker.py': worker_source.replace(
                "version='1.0.0'", f"version='{self._PATH_WORKER_VERSION}'")})

        utils_dir = str(Path(git_info_utils.__file__).resolve().parent)
        real_toplevel = git_info_utils.get_repo_toplevel
        monkeypatch.setattr(git_info_utils, 'get_repo_toplevel',
                            lambda path: str(framework) if path == utils_dir
                            else real_toplevel(path))
        return framework, elsewhere, algos / 'my_worker' / 'my_worker.py'

    def test_versions_and_the_framework_commit_survive_the_round_trip(self, repos, monkeypatch):
        framework, elsewhere, path_worker = repos
        strategy = {'decision_logic_type': 'CORE/simple_consensus',
                    'worker_instances': {'obv_volume': 'CORE/obv', 'user_w': str(path_worker)}}

        # The worktree trap: the first reads happen with the cwd in ANOTHER checkout. They are
        # cached, so a cwd-derived answer would reach the header and the ledger unchanged.
        with monkeypatch.context() as inside_elsewhere:
            inside_elsewhere.chdir(elsewhere)
            assert get_git_commit() is not None and get_git_info() is not None

        config = load_autotrader_config(_PROFILE)
        config.strategy_config = strategy
        identity = capture_code_identity([strategy])
        bundle = create_autotrader_loggers(config, _START, origin=build_run_origin(RunChannel.CLI),
                                           code_identity=identity)
        try:
            header = read_run_header(bundle.run_dir / RUN_HEADER_ARTIFACT)
            p = build_run_provenance_from_session(
                config, bundle.run_id, _START, None, run_dir=bundle.run_dir,
                logger=bundle.summary_logger)
        finally:
            for logger in (bundle.global_logger, bundle.session_logger, bundle.summary_logger):
                logger.close()

        expected_commit = _git(framework, 'rev-parse', '--short', 'HEAD')
        assert expected_commit != _git(elsewhere, 'rev-parse', '--short', 'HEAD')
        assert header.git_commit == expected_commit
        assert header.code_identity.framework.commit == expected_commit
        assert p.git_commit == expected_commit
        assert p.git_branch == _git(framework, 'rev-parse', '--abbrev-ref', 'HEAD')

        resolved = {component.name: component for component in header.code_identity.components}
        assert all(component.source_path for component in resolved.values())
        assert resolved['user_w'].repository is not None and resolved['user_w'].package_digest

        decision_version = SimpleConsensus.get_metadata().version
        obv_version = ObvWorker.get_metadata().version
        assert decision_version and obv_version, 'CORE components declare a version (§39)'
        assert p.decision_version == decision_version
        assert p.worker_versions == {'obv_volume': obv_version,
                                     'user_w': self._PATH_WORKER_VERSION}
        # Every repository is committed, so the code that ran is reproducible from commits.
        assert p.git_dirty is False


class TestEachPatchStaysWithItsRepository:
    """
    A dirty tree's patch is kept beside the code it describes (#551): this repository's in the
    run-patch store, a strategy repository's INSIDE that repository — so private strategy code
    never enters this project's tree, not even as a copy in its data directory.

    The framework is redirected at `get_framework_root` as above; both repositories are throwaway,
    which is why this is the one place the real foreign patch home is put back.
    """

    @pytest.fixture(autouse=True)
    def _fresh_caches(self):
        """Every read here is cached per process; clear before AND after (§42)."""
        clear_git_caches()
        clear_package_digest_cache()
        yield
        clear_git_caches()
        clear_package_digest_cache()

    @pytest.fixture
    def dirty_repos(self, tmp_path, monkeypatch, real_foreign_patch_homes):
        """
        A framework repository and an algo repository holding a path worker — both dirty.

        Returns:
            (framework repo, algo repo, path worker file)
        """
        framework = _committed_repo(tmp_path / 'framework', {'app.py': 'VALUE = 1\n'})
        worker_source = Path('python/framework/workers/core/rsi_worker.py').read_text(
            encoding='utf-8')
        algos = _committed_repo(tmp_path / 'algos', {
            '.gitignore': '__pycache__/\n', 'my_worker/my_worker.py': worker_source})
        (framework / 'app.py').write_text('VALUE = 2\n', encoding='utf-8')
        worker = algos / 'my_worker' / 'my_worker.py'
        worker.write_text(worker_source + '\n# tuned\n', encoding='utf-8')

        utils_dir = str(Path(git_info_utils.__file__).resolve().parent)
        real_toplevel = git_info_utils.get_repo_toplevel
        monkeypatch.setattr(git_info_utils, 'get_repo_toplevel',
                            lambda path: str(framework) if path == utils_dir
                            else real_toplevel(path))
        return framework, algos, worker

    @staticmethod
    def _capture(worker: Path) -> CodeIdentity:
        """
        Capture a strategy running one CORE decision and the path worker.

        Args:
            worker: The path worker's file

        Returns:
            The captured code identity
        """
        return capture_code_identity([{'decision_logic_type': 'CORE/simple_consensus',
                                       'worker_instances': {'user_w': str(worker)}}])

    def test_the_strategy_repositorys_patch_stays_inside_it(self, dirty_repos):
        framework, algos, worker = dirty_repos

        [state] = self._capture(worker).repositories

        assert state.root == str(algos) and state.dirty is True and state.restorable is True
        assert state.patch_ref.startswith(f'{FOREIGN_PATCH_DIR}/'), 'relative to its own root'
        assert b'+# tuned' in (algos / state.patch_ref).read_bytes()
        store = Path(AppConfigManager().get_run_patches_path())
        assert not (store / Path(state.patch_ref).name).exists(), 'no copy in this project'

    def test_this_repositorys_patch_goes_to_the_run_patch_store(self, dirty_repos):
        framework, algos, worker = dirty_repos

        identity = self._capture(worker)

        kept = Path(identity.framework.patch_ref)
        assert kept.parent == Path(AppConfigManager().get_run_patches_path())
        assert b'+VALUE = 2' in kept.read_bytes()
        assert not (framework / FOREIGN_PATCH_DIR).exists()

    def test_a_foreign_home_that_cannot_be_written_costs_its_patch_and_nothing_else(
            self, dirty_repos):
        framework, algos, worker = dirty_repos
        (algos / FOREIGN_PATCH_DIR).write_text('a file where the directory belongs\n',
                                               encoding='utf-8')

        identity = self._capture(worker)

        [state] = identity.repositories
        assert state.dirty is True and state.diff_hash, 'the run stays identifiable'
        assert state.patch_ref is None and state.restorable is False
        assert identity.framework.patch_ref is not None, 'the other repository is unaffected'
