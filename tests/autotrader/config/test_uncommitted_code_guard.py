"""
Real orders from uncommitted code are refused, with one recorded way through (#551).

The thirty-day live run is a parity proof: afterwards a backtest over exactly the same period is
run and the divergence measured. That comparison needs the code that ran, and a session started
from a dirty tree ran code no commit describes — the strategy may even sit untracked in its own
repository, where this one's commit says nothing about it. So a session whose EFFECTIVE dry_run
resolves to false refuses to start while any repository it loads code from is dirty, unversioned
or unreadable, unless the operator types `--allow-dirty`. The override is not silent: the session
log says so before the first order, and the post-run validation reports it as a Tier-1 warning.

What these tests pin, in the order the consequences matter:
- every combination of effective dry_run x code state x `--allow-dirty` — the refusal fires in
  exactly one cell and the override is reported in exactly one other;
- a mock session never fires, whatever it is told;
- the refusal names every repository with its state and both ways forward, promising a patch
  only where one restores the code;
- the override reaches the session channel and the Tier-1 channel, and nothing else;
- the guard over a REAL capture: a dirty repository refuses, a missing or refusing git reads as
  unknown and never as "not under version control";
- code edited between the capture and the load refuses real orders, `--allow-dirty` included.

Built with the SimpleNamespace + patched MarketConfigManager pattern of
`test_dry_run_resolution.py`, because the effective dry_run is the input and that file pins how it
is resolved. Most code identities are hand-built; the real-capture tests run
`build_code_identity` over temporary repositories — never this working tree — and the capture's
own rules are pinned in `tests/framework/reporting/test_code_identity.py`.
"""

import os
import pwd
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Tuple
from unittest.mock import patch

import pytest

from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.code_identity_errors import (
    CodeChangedDuringStartupError,
    UncommittedCodeError,
)
from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.types.api.report_types import WarningTier
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.store.run_patch_store import RunPatchStore
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
)
from python.framework.types.validation_types import Severity, ValidationDomain
from python.framework.utils import code_identity_builder, git_info_utils
from python.framework.utils.code_identity_builder import (
    build_code_identity,
    clear_package_digest_cache,
)
from python.framework.utils.git_info_utils import clear_git_caches
from python.framework.validators.session_post_run_validator import SessionPostRunValidator
from python.framework.validators.uncommitted_code_validator import (
    UNCOMMITTED_CODE_CHECK,
    validate_code_unchanged_since_capture,
    validate_committed_code,
)

BROKER = 'kraken_spot'
PROFILE_PATH = Path('configs/autotrader_profiles/production/my_bot_live.json')
_RUN_ID = '20260924_120000_a1b2c3d4'

# A foreign repository keeps its patch inside itself, under a reference relative to its root —
# and every message shows it joined to that root.
_ALGO_PATCH = f'.finiex_run_patches/{"4e01" * 16}.patch'
_ALGO_PATCH_SHOWN = f'user_algos/{_ALGO_PATCH}'

CLEAN = CodeIdentity(
    framework=RepositoryState(root='/app', commit='151c9889'),
    repositories=[RepositoryState(root='/app/user_algos', commit='2f054f5')])

DIRTY = CodeIdentity(
    framework=RepositoryState(root='/app', commit='151c9889'),
    repositories=[RepositoryState(
        root='/app/user_algos', commit='2f054f5', dirty=True, uncommitted_count=2,
        changes=[' M shared/params.json', '?? my_bot/'],
        diff_hash='4e01' * 16, patch_ref=_ALGO_PATCH, restorable=True)])

# git could not answer for this repository: nothing says it is clean.
UNKNOWN = CodeIdentity(framework=RepositoryState(root='/app', commit=None))

_IDENTITIES = {'clean': CLEAN, 'dirty': DIRTY, 'unknown': UNKNOWN, 'not_captured': None}

# (profile dry_run, broker default) → does the session send real orders? Two routes to each
# answer, so a guard reading the PROFILE field instead of the resolved value would fail one.
_DRY_RUN_ROUTES = [
    (None, True, False, 'dry-by-broker'),
    (True, False, False, 'dry-by-profile'),
    (None, False, True, 'real-by-broker'),
    (False, False, True, 'real-by-both'),
]


def _combinations(include_refused: bool) -> list:
    """
    Every (profile dry_run, broker default, real, state, allow_dirty) cell of the matrix.

    Args:
        include_refused: False leaves out the one cell the guard refuses at startup

    Returns:
        The pytest parameters
    """
    cells = []
    for profile_override, broker_default, real, route in _DRY_RUN_ROUTES:
        for state in _IDENTITIES:
            for allow_dirty in (False, True):
                refused = real and state != 'clean' and not allow_dirty
                if refused and not include_refused:
                    continue
                flag = 'allow-dirty' if allow_dirty else 'strict'
                cells.append(pytest.param(profile_override, broker_default, real, state,
                                          allow_dirty, id=f'{route}-{state}-{flag}'))
    return cells


_MATRIX = 'profile_override, broker_default, real, state, allow_dirty'


class _RecordingLogger:
    """Stands in for a ScenarioLogger and records every line by level."""

    def __init__(self):
        self.lines = []

    def info(self, message: str) -> None:
        self.lines.append(('INFO', message))

    def warning(self, message: str) -> None:
        self.lines.append(('WARNING', message))

    def error(self, message: str) -> None:
        self.lines.append(('ERROR', message))

    def debug(self, message: str) -> None:
        self.lines.append(('DEBUG', message))


def _session(identity, profile_override, adapter_type='live', allow_dirty=False):
    """
    A session reduced to what the guard reads.

    Args:
        identity: The captured code identity, or None
        profile_override: The profile's dry_run field (None = inherit the broker's)
        adapter_type: `live` or `mock`
        allow_dirty: Whether `--allow-dirty` was typed

    Returns:
        The session
    """
    session = AutotraderMain.__new__(AutotraderMain)
    session._config = SimpleNamespace(
        name='my_bot_live', symbol='BTCUSD', broker_type=BROKER, bot_id='my-bot',
        adapter_type=adapter_type, dry_run=profile_override, config_path=PROFILE_PATH)
    session._code_identity = identity
    session._allow_dirty = allow_dirty
    session._uncommitted_code_allowed = False
    session._session_logger = _RecordingLogger()
    session._global_logger = _RecordingLogger()
    return session


def _guard(session, broker_default):
    """
    Run the guard with the broker's dry-run default patched.

    Args:
        session: A session from `_session`
        broker_default: What market_config.json says for the broker

    Returns:
        The patched MarketConfigManager, for assertions on whether it was asked
    """
    with patch('python.framework.autotrader.autotrader_main.MarketConfigManager') as manager:
        manager.return_value.get_dry_run.return_value = broker_default
        session._guard_uncommitted_code()
    return manager


def _post_run_findings(session):
    """
    Hand the session's verdict to the post-run validation, as `_shutdown` does.

    Args:
        session: A session the guard has run on

    Returns:
        The findings the session's validation channel received
    """
    result = AutoTraderResult()
    config = AutoTraderConfig(name='my_bot_live', symbol='BTCUSD', broker_type=BROKER)
    SessionPostRunValidator(
        result, config,
        uncommitted_code_allowed=session._uncommitted_code_allowed,
        code_identity=session._code_identity).validate()
    return [finding for validation in result.session_validation_result
            for finding in validation.findings]


class TestEveryCombination:
    """Effective dry_run x code state x `--allow-dirty`: one cell refuses, one cell reports."""

    @pytest.mark.parametrize(_MATRIX, _combinations(include_refused=True))
    def test_the_guard_decides_on_the_resolved_value(
            self, profile_override, broker_default, real, state, allow_dirty):
        session = _session(_IDENTITIES[state], profile_override, allow_dirty=allow_dirty)
        uncommitted = state != 'clean'

        if real and uncommitted and not allow_dirty:
            with pytest.raises(UncommittedCodeError):
                _guard(session, broker_default)
            return

        _guard(session, broker_default)
        assert session._uncommitted_code_allowed is (real and uncommitted and allow_dirty)

    @pytest.mark.parametrize(_MATRIX, _combinations(include_refused=False))
    def test_the_tier_1_warning_exists_only_where_the_override_was_used(
            self, profile_override, broker_default, real, state, allow_dirty):
        """A flag typed on a clean tree or a dry run overrode nothing, so nothing is reported."""
        session = _session(_IDENTITIES[state], profile_override, allow_dirty=allow_dirty)
        uncommitted = state != 'clean'

        _guard(session, broker_default)

        checks = [finding.check for finding in _post_run_findings(session)]
        expected = [UNCOMMITTED_CODE_CHECK] if (real and uncommitted and allow_dirty) else []
        assert checks == expected


class TestMockNeverFires:
    """No code state and no profile field can make a mock session send a real order."""

    @pytest.mark.parametrize('state', ['dirty', 'unknown', 'not_captured'])
    @pytest.mark.parametrize('profile_override', [None, False])
    def test_a_mock_session_starts_from_any_tree(self, state, profile_override):
        session = _session(_IDENTITIES[state], profile_override, adapter_type='mock')

        manager = _guard(session, broker_default=False)

        assert session._uncommitted_code_allowed is False
        manager.return_value.get_dry_run.assert_not_called()
        assert _post_run_findings(session) == []


class TestTheRefusalMessage:
    """An abort that does not say what to do next gets worked around — usually unsafely."""

    @staticmethod
    def _message(identity, profile_path=PROFILE_PATH) -> str:
        with pytest.raises(UncommittedCodeError) as caught:
            validate_committed_code(identity, real_orders=True, allow_dirty=False,
                                    profile_path=profile_path)
        return str(caught.value)

    def test_every_repository_is_named_with_its_state(self):
        lines = self._message(DIRTY).splitlines()
        framework = next(line for line in lines if line.strip().startswith('framework'))
        algo = next(line for line in lines if line.strip().startswith('algo'))
        assert '/app' in framework and framework.endswith('clean')
        assert 'user_algos/' in algo and algo.endswith('2 changes · untracked: my_bot/')

    def test_it_says_real_orders_and_why_that_matters(self):
        message = self._message(DIRTY)
        assert 'REAL ORDERS (dry_run resolves to false)' in message
        assert 'parity' in message

    def test_both_ways_forward_are_offered(self):
        message = self._message(DIRTY)
        assert 'commit the changes in user_algos/, then start again' in message
        assert 'the normal path' in message
        assert (f'autotrader_cli.py run --config {PROFILE_PATH} --allow-dirty') in message
        assert 'diff hash and patch' in message and 'Tier-1 warning' in message

    def test_a_clean_repository_is_not_asked_to_be_committed(self):
        assert 'commit the changes in /app' not in self._message(DIRTY)

    def test_an_unversioned_package_is_named_and_promised_no_patch(self):
        identity = CodeIdentity(
            framework=RepositoryState(root='/app', commit='151c9889'),
            repositories=[RepositoryState(root='/srv/my_bot', in_repository=False)])
        message = self._message(identity)
        assert 'not under version control' in message
        assert 'put /srv/my_bot under version control' in message
        assert 'nothing can restore the code afterwards in' in message
        assert '/srv/my_bot (no repository to diff against)' in message
        assert 'diff hash and patch' not in message

    def test_git_unavailable_is_named_as_unknown_not_as_clean(self):
        message = self._message(UNKNOWN)
        assert 'state unknown (git unavailable or refused)' in message
        assert 'make git able to read /app' in message
        assert 'git config --global --add safe.directory /app' in message
        assert 'cannot be determined' in message

    def test_git_that_could_not_run_is_unknown_never_unversioned(self):
        """`in_repository` None is "nobody knows" — only an answering git can say "absent"."""
        identity = CodeIdentity(
            framework=RepositoryState(root='/app', in_repository=None),
            repositories=[RepositoryState(root='/app/user_algos', in_repository=None)])
        message = self._message(identity)
        assert message.count('state unknown (git unavailable or refused)') == 2
        assert 'not under version control' not in message
        assert 'safe.directory /app/user_algos' in message
        assert 'nothing can restore the code afterwards' in message

    def test_a_patch_that_was_not_kept_is_not_promised(self):
        """The override may promise a patch only where one restores the code (`restorable`)."""
        identity = DIRTY.model_copy(deep=True)
        identity.repositories[0].patch_ref = None
        identity.repositories[0].restorable = False
        message = self._message(identity)
        assert 'diff hash and patch' not in message
        assert 'user_algos/ (its patch could not be kept)' in message

    def test_a_patch_that_cannot_cover_a_nested_repository_is_not_promised(self):
        identity = DIRTY.model_copy(deep=True)
        identity.repositories[0].restorable = False
        message = self._message(identity)
        assert 'diff hash and patch' not in message
        assert 'its patch cannot cover a nested repository' in message

    def test_an_identity_that_was_not_captured_refuses_too(self):
        message = self._message(None)
        assert 'code identity not captured' in message
        assert 'find why the code identity was not captured' in message
        assert 'make git able to read' not in message

    def test_an_identity_without_a_framework_state_names_no_question_mark(self):
        """The capture always records this repository; a hand-built identity still renders."""
        lines = self._message(CodeIdentity()).splitlines()
        framework = next(line for line in lines if line.strip().startswith('framework'))
        assert 'this repository' in framework and '?' not in framework

    def test_a_missing_profile_path_renders_a_placeholder(self):
        assert '--config <profile> --allow-dirty' in self._message(DIRTY, profile_path=None)

    def test_it_renders_inside_the_startup_failed_block(self, capsys):
        """The existing STARTUP FAILED path indents the first line; the rest carries its own."""
        session = _session(DIRTY, None)
        session._print_startup_error(self._message(DIRTY))

        block = capsys.readouterr().out.strip().splitlines()[1:-1]
        assert block[0] == '  ❌ STARTUP FAILED'
        assert all(line.startswith('  ') for line in block), block


class TestTheOverrideIsLoud:
    """`--allow-dirty` is the development path, and it is deliberately not silent."""

    def test_the_session_channel_names_the_patch_before_the_first_order(self, capsys):
        session = _session(DIRTY, None, allow_dirty=True)
        _guard(session, broker_default=False)

        notices = [message for level, message in session._session_logger.lines
                   if 'REAL ORDERS FROM UNCOMMITTED CODE' in message]
        assert len(notices) == 1 and _ALGO_PATCH_SHOWN in notices[0]
        assert 'REAL ORDERS FROM UNCOMMITTED CODE' in capsys.readouterr().out

    def test_the_notice_is_not_a_second_warning(self):
        """The verdict travels as a Tier-1 finding; a WARNING line would report it twice."""
        session = _session(DIRTY, None, allow_dirty=True)
        _guard(session, broker_default=False)
        assert [level for level, _ in session._session_logger.lines] == ['INFO']

    def test_nothing_reaches_the_global_channel(self):
        """§35: the global log never reaches the summary."""
        session = _session(DIRTY, None, allow_dirty=True)
        _guard(session, broker_default=False)
        assert session._global_logger.lines == []

    def test_a_patch_that_was_not_kept_says_so(self):
        identity = DIRTY.model_copy(deep=True)
        identity.repositories[0].patch_ref = None
        session = _session(identity, None, allow_dirty=True)
        _guard(session, broker_default=False)

        notice = session._session_logger.lines[0][1]
        assert 'patch NOT kept' in notice and '4e014e014e01' in notice

    def test_a_strict_start_from_a_clean_tree_says_nothing(self):
        session = _session(CLEAN, None)
        _guard(session, broker_default=False)
        assert session._session_logger.lines == []


class TestTheTier1Warning:

    def test_the_finding_names_the_code_and_where_to_restore_it(self):
        session = _session(DIRTY, None, allow_dirty=True)
        _guard(session, broker_default=False)

        [finding] = _post_run_findings(session)
        assert finding.severity is Severity.WARNING
        assert finding.domain is ValidationDomain.SETUP and finding.scope == 'run'
        assert '--allow-dirty' in finding.message and _ALGO_PATCH_SHOWN in finding.message

    def test_it_reaches_the_report_as_a_tier_1_row(self):
        session = _session(DIRTY, None, allow_dirty=True)
        _guard(session, broker_default=False)
        result = AutoTraderResult()
        config = AutoTraderConfig(name='my_bot_live', symbol='BTCUSD', broker_type=BROKER)
        SessionPostRunValidator(result, config, uncommitted_code_allowed=True,
                                code_identity=DIRTY).validate()

        report = build_warnings_errors_report_from_session(_RUN_ID, result, config.name,
                                                           config.symbol)
        major = [row for row in report.warnings if row.tier == WarningTier.VALIDATOR_PRODUCED]
        assert [row.check for row in major] == [UNCOMMITTED_CODE_CHECK]

    def test_the_shutdown_hands_the_verdict_to_the_post_run_validation(self, monkeypatch):
        """The call site, not only the check: the verdict must survive to the session's end."""
        session = AutotraderMain(AutoTraderConfig(
            name='my_bot_live', symbol='BTCUSD', broker_type=BROKER))
        session._uncommitted_code_allowed = True
        session._code_identity = DIRTY
        session._global_logger = _RecordingLogger()
        session._session_logger = _RecordingLogger()
        monkeypatch.setattr(session, '_persist_cold_start_carry_over', lambda: True)
        monkeypatch.setattr(session, '_collect_results', lambda ticks, clipped: AutoTraderResult())
        monkeypatch.setattr(session, '_generate_reports', lambda result: None)

        result = session._shutdown(0, 0)

        checks = [finding.check for validation in result.session_validation_result
                  for finding in validation.findings]
        assert checks == [UNCOMMITTED_CODE_CHECK]


class TestTheGuardIsPartOfStartup:
    """It runs inside `_validate_startup`, after the carry-over identity and before anything else."""

    @staticmethod
    def _startup_session(monkeypatch, identity, calls):
        session = _session(identity, None)
        session._decision_logic = SimpleNamespace()
        session._worker_orchestrator = SimpleNamespace(workers={})

        def refuse_swap_check(symbol):
            calls.append('swap')
            raise RuntimeError('reached the swap-mode check')

        session._executor = SimpleNamespace(broker=SimpleNamespace(adapter=SimpleNamespace(
            get_symbol_specification=refuse_swap_check)))
        monkeypatch.setattr(autotrader_main_module, 'validate_algo_clock',
                            lambda classes: calls.append('clock'))
        monkeypatch.setattr(autotrader_main_module, 'validate_bot_id',
                            lambda name, symbol, bot_id: calls.append('bot_id'))
        monkeypatch.setattr(autotrader_main_module, 'validate_carry_over_identity_unique',
                            lambda path, name, symbol, bot_id: calls.append('unique'))
        return session

    def test_a_dirty_real_money_start_is_refused_there(self, monkeypatch):
        calls = []
        session = self._startup_session(monkeypatch, DIRTY, calls)
        with patch('python.framework.autotrader.autotrader_main.MarketConfigManager') as manager:
            manager.return_value.get_dry_run.return_value = False
            with pytest.raises(UncommittedCodeError):
                session._validate_startup()
        assert calls == ['clock', 'bot_id', 'unique']

    def test_a_clean_start_passes_on_to_the_next_check(self, monkeypatch):
        calls = []
        session = self._startup_session(monkeypatch, CLEAN, calls)
        with patch('python.framework.autotrader.autotrader_main.MarketConfigManager') as manager:
            manager.return_value.get_dry_run.return_value = False
            with pytest.raises(RuntimeError, match='swap-mode'):
                session._validate_startup()
        assert calls == ['clock', 'bot_id', 'unique', 'swap']


# =============================================================================
# Against the REAL capture — temporary repositories, never this working tree
# =============================================================================

# A real CORE decision logic, copied into a temporary package so it loads from a PATH the way a
# user strategy does.
_LOGIC_SOURCE = Path('python/framework/decision_logic/core/simple_consensus.py')


def _git(repo: Path, *args: str) -> str:
    """
    Run one git command in a test repository.

    Args:
        repo: The repository directory
        args: The git arguments

    Returns:
        The command's standard output, stripped
    """
    result = subprocess.run(
        ['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@test', *args],
        capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _repo(path: Path, files: Dict[str, str], commit: bool = True) -> Path:
    """
    Create a repository holding the given files, committed or left untracked.

    Args:
        path: Where the repository is created
        files: Relative path → text
        commit: Whether the files are part of a first commit (a `.gitignore` always is)

    Returns:
        The repository's top-level directory, as git reports it
    """
    path.mkdir(parents=True)
    _git(path, 'init', '-q')
    (path / '.gitignore').write_text('__pycache__/\n', encoding='utf-8')
    _git(path, 'add', '.gitignore')
    _git(path, 'commit', '-q', '-m', 'initial')
    for relative, content in files.items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(content, encoding='utf-8')
    if commit and files:
        _git(path, 'add', '-A')
        _git(path, 'commit', '-q', '-m', 'files')
    return Path(_git(path, 'rev-parse', '--show-toplevel'))


def _strategy_package(tmp_path: Path, commit: bool) -> Tuple[Path, Dict]:
    """
    An algo repository holding one strategy package, and the strategy_config naming it by path.

    Args:
        tmp_path: The test's temporary directory
        commit: Whether the package is committed

    Returns:
        (the package directory, the strategy_config)
    """
    repo = _repo(tmp_path / 'algos', {
        'my_strategy/my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8'),
        'my_strategy/helpers.py': 'THRESHOLD = 0.6\n'}, commit=commit)
    package = repo / 'my_strategy'
    return package, {'decision_logic_type': str(package / 'my_strategy.py'),
                     'worker_instances': {}}


@pytest.fixture
def real_capture(tmp_path, monkeypatch):
    """
    Point the capture's FRAMEWORK repository at a clean temporary one, with fresh caches.

    Every git read and package digest is cached per process (§42), so a scripted repository must
    never see a stale answer, and its fake answer must never reach a later test.

    Returns:
        The temporary framework repository
    """
    framework = _repo(tmp_path / 'framework', {'app.py': 'VALUE = 1\n'})
    clear_git_caches()
    clear_package_digest_cache()
    monkeypatch.setattr(code_identity_builder, 'get_framework_root', lambda: str(framework))
    yield framework
    clear_git_caches()
    clear_package_digest_cache()


class TestTheGuardOverARealCapture:
    """The capture and the guard together: a dirty tree the builder READ is what refuses."""

    def test_a_dirty_framework_repository_refuses_real_orders(self, real_capture, tmp_path):
        (real_capture / 'app.py').write_text('VALUE = 2\n', encoding='utf-8')
        (real_capture / 'new.py').write_text('NEW = 1\n', encoding='utf-8')
        store = RunPatchStore(tmp_path / 'run_patches')

        identity = build_code_identity(
            [{'decision_logic_type': 'CORE/simple_consensus', 'worker_instances': {}}],
            patch_sink=lambda root, key, patch: store.put(key, patch))

        with pytest.raises(UncommittedCodeError) as caught:
            validate_committed_code(identity, real_orders=True, allow_dirty=False,
                                    profile_path=PROFILE_PATH)
        message = str(caught.value)
        framework = next(line for line in message.splitlines()
                         if line.strip().startswith('framework'))
        assert str(real_capture) in framework
        assert framework.endswith('2 changes · untracked: new.py')
        assert f'commit the changes in {real_capture}, then start again' in message
        assert 'diff hash and patch' in message, 'a stored complete patch may be promised'

    def test_a_committed_tree_starts(self, real_capture, tmp_path):
        _, strategy = _strategy_package(tmp_path, commit=True)

        identity = build_code_identity([strategy])

        assert validate_committed_code(identity, real_orders=True, allow_dirty=False,
                                       profile_path=PROFILE_PATH) is False


def _fail_every_git_call(monkeypatch) -> None:
    """
    Make every git subprocess the capture starts fail as a missing binary would.

    The module's `subprocess` is replaced by a namespace rather than `subprocess.run` patched
    globally, so nothing else in the process loses its subprocesses for the test's duration.

    Args:
        monkeypatch: pytest's monkeypatch
    """
    def no_git(*args, **kwargs):
        raise FileNotFoundError(2, 'No such file or directory', 'git')

    monkeypatch.setattr(git_info_utils, 'subprocess', SimpleNamespace(
        run=no_git, TimeoutExpired=subprocess.TimeoutExpired,
        CalledProcessError=subprocess.CalledProcessError,
        CompletedProcess=subprocess.CompletedProcess))


class TestGitThatCannotAnswer:
    """Unknown is its own state: never "not under version control", never a question mark."""

    @pytest.fixture(autouse=True)
    def _fresh_caches(self):
        clear_git_caches()
        clear_package_digest_cache()
        yield
        clear_git_caches()
        clear_package_digest_cache()

    def test_no_git_binary_renders_every_repository_as_unknown(self, tmp_path, monkeypatch):
        """The shape the REAL capture produces without git, not a hand-built one."""
        package = tmp_path / 'loose' / 'my_strategy'
        package.mkdir(parents=True)
        shutil.copy(_LOGIC_SOURCE, package / 'my_strategy.py')
        _fail_every_git_call(monkeypatch)

        identity = build_code_identity([
            {'decision_logic_type': 'CORE/simple_consensus', 'worker_instances': {}},
            {'decision_logic_type': str(package / 'my_strategy.py'), 'worker_instances': {}}])

        assert identity.framework.in_repository is None and identity.framework.root
        assert [(state.root, state.in_repository) for state in identity.repositories] == [
            (str(package), None)]
        with pytest.raises(UncommittedCodeError) as caught:
            validate_committed_code(identity, real_orders=True, allow_dirty=False,
                                    profile_path=PROFILE_PATH)
        message = str(caught.value)
        rows = [line for line in message.splitlines()
                if line.strip().startswith(('framework', 'algo'))]
        assert len(rows) == 2
        assert all(row.endswith('state unknown (git unavailable or refused)') for row in rows)
        assert all('?' not in row for row in rows)
        assert 'not under version control' not in message
        assert f'safe.directory {identity.framework.root}' in message
        assert 'cannot be determined' in message

    @pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() != 0,
                        reason='a checkout owned by another user needs root to create')
    def test_a_checkout_git_refuses_is_unknown_and_named_for_safe_directory(
            self, real_capture, tmp_path):
        """
        "detected dubious ownership": git RUNS and refuses the checkout. `rev-parse` fails exactly
        as it does in a directory with no repository, and only one of the two is a statement
        about the code. The checkout holding the `.git` is recorded, because it is what a
        `safe.directory` entry has to name.
        """
        package, strategy = _strategy_package(tmp_path, commit=True)
        repo = package.parent
        nobody = pwd.getpwnam('nobody').pw_uid
        for path in [repo, *repo.rglob('*')]:
            os.chown(path, nobody, -1, follow_symlinks=False)
        probe = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            pytest.skip('git trusts this checkout (a safe.directory entry covers it)')

        identity = build_code_identity([strategy])

        assert [(state.root, state.in_repository) for state in identity.repositories] == [
            (str(repo), None)]
        with pytest.raises(UncommittedCodeError) as caught:
            validate_committed_code(identity, real_orders=True, allow_dirty=False,
                                    profile_path=PROFILE_PATH)
        message = str(caught.value)
        assert 'not under version control' not in message
        assert f'git config --global --add safe.directory {repo}' in message


class TestCodeThatMovedDuringStartup:
    """
    The capture reads the packages before `setup_pipeline` loads them from disk again. An edit
    in between runs code the header does not describe — `--allow-dirty` cannot vouch for it,
    because the patch it records is the stale one.
    """

    def test_real_orders_are_refused_even_with_allow_dirty(self, real_capture, tmp_path):
        package, strategy = _strategy_package(tmp_path, commit=False)
        identity = build_code_identity([strategy])
        (package / 'helpers.py').write_text('THRESHOLD = 0.9\n', encoding='utf-8')
        session = _session(identity, None, allow_dirty=True)

        with pytest.raises(CodeChangedDuringStartupError) as caught:
            _guard(session, broker_default=False)

        message = str(caught.value)
        assert str(package / 'my_strategy.py') in message
        assert '--allow-dirty does not help here' in message
        assert 'then start again' in message

    def test_a_dry_run_keeps_running_and_warns_in_the_session_channel(self, real_capture,
                                                                        tmp_path):
        """No money moves; the WARNING enters the error pot, so the run's report says its
        header is wrong."""
        package, strategy = _strategy_package(tmp_path, commit=True)
        identity = build_code_identity([strategy])
        (package / 'helpers.py').write_text('THRESHOLD = 0.9\n', encoding='utf-8')
        session = _session(identity, True)

        _guard(session, broker_default=False)

        [(level, line)] = session._session_logger.lines
        assert level == 'WARNING' and 'CODE CHANGED DURING STARTUP' in line
        assert str(package / 'my_strategy.py') in line
        assert session._uncommitted_code_allowed is False

    def test_an_unchanged_tree_says_nothing(self, real_capture, tmp_path):
        _, strategy = _strategy_package(tmp_path, commit=True)
        session = _session(build_code_identity([strategy]), None)

        _guard(session, broker_default=False)

        assert session._session_logger.lines == []

    def test_the_refusal_renders_inside_the_startup_failed_block(self, capsys):
        moved = [ComponentIdentity(role=ComponentRole.DECISION, name='my_strategy',
                                   type='my_strategy.py', source_path='/srv/my_strategy.py')]
        with pytest.raises(CodeChangedDuringStartupError) as caught:
            validate_code_unchanged_since_capture(moved, real_orders=True)
        session = _session(None, None)
        session._print_startup_error(str(caught.value))

        block = capsys.readouterr().out.strip().splitlines()[1:-1]
        assert all(line.startswith('  ') for line in block), block
        assert validate_code_unchanged_since_capture([], real_orders=True) is None
