"""
The divergence gate's code path over real commits (#512).

A repository in `tmp_path` holds a release commit laid out like this one's backtest path — its
entry points, a data-preparation module, a served model — and each test commits one change as
the backtest's commit. Both trees are read back through git, the way the gate reads them, and the
path is judged file by file. The gate's own declarations are held to their vocabulary here too.
"""

import dataclasses
import re
from pathlib import Path
from typing import Dict, Optional

import pytest

from python.framework.constants.divergence_gate_constants import ALLOWED_CHANGES_BY_RELEASE
from python.framework.types.divergence_gate_types import GateChangeCategory, GateFileChange
from python.framework.utils.git_info_utils import read_tree_at_commit
from python.framework.utils.import_closure_utils import syntax_digest
from python.framework.validators.divergence_gate_validator import (
    CODE_PATH_CHECK,
    CODE_UNKNOWN_CHECK,
    COMMIT_UNREADABLE_CHECK,
    SESSIONS_DISAGREE_CHECK,
    UNCOMMITTED_CHECK,
    check_divergence_gate,
    judge_code_path,
)
from tests.shared.divergence_gate_pair import (
    code_identity,
    gate_backtest,
    gate_session,
    gate_window,
)
from tests.shared.git_test_repos import make_repo, run_git, write_files

_ENGINE = 'python/framework/core/engine.py'
_LEAVING = 'python/framework/core/leaving.py'
_UNUSED = 'python/framework/core/unused.py'
_RECORDER = 'python/framework/core/recorder.py'
_PREP = 'python/framework/data_preparation/prep.py'
_SERVED = 'python/framework/types/api/served.py'

_RELEASE_FILES = {
    'python/framework/process/process_main.py': 'from python.framework.core.engine import run\n',
    'python/framework/process/process_executor.py': 'from python.framework.core import engine\n',
    _PREP: ('from python.framework.core.leaving import gone\n'
            'from python.framework.types.api.served import Model\n'),
    _ENGINE: '"""The engine."""\n\n\ndef run():\n    """Run it."""\n    return 1\n',
    _LEAVING: 'gone = 1\n',
    _UNUSED: 'UNUSED = 1\n',
    _SERVED: 'Model = 1\n',
    'docs/readme.md': 'not python\n',
}


class _Repository:
    """A release commit, and a backtest commit made of one change on top of it."""

    def __init__(self, root: Path):
        self.root = make_repo(root, _RELEASE_FILES)
        self.release = run_git(self.root, 'rev-parse', '--short', 'HEAD')

    def backtest(self, files: Dict[str, str]) -> str:
        write_files(self.root, files)
        run_git(self.root, 'add', '-A')
        run_git(self.root, 'commit', '-q', '--allow-empty', '-m', 'backtest')
        return run_git(self.root, 'rev-parse', '--short', 'HEAD')

    def read(self, commit: str) -> Optional[Dict[str, str]]:
        return read_tree_at_commit(str(self.root), commit, 'python', '.py')

    def judge(self, files: Dict[str, str], declarations: Optional[Dict] = None):
        backtest = self.backtest(files)
        verdicts = judge_code_path('9.9.9', self.read(self.release), self.read(backtest),
                                   declarations or {})
        return {verdict.path: verdict for verdict in verdicts}


@pytest.fixture
def repository(tmp_path) -> _Repository:
    return _Repository(tmp_path / 'repository')


def _declared(source: str, *categories: str) -> Dict:
    return {'categories': categories or ('recording_only',), 'syntax_digest': syntax_digest(source)}


class TestReadingATreeAtACommit:

    def test_it_reads_every_file_of_the_directory_with_the_suffix(self, repository):
        tree = repository.read(repository.release)
        assert set(tree) == {path for path in _RELEASE_FILES if path.endswith('.py')}
        assert tree[_ENGINE] == _RELEASE_FILES[_ENGINE]

    def test_a_commit_the_repository_does_not_hold_is_none(self, repository):
        assert repository.read('0123456789ab') is None


class TestTheCodePathFileByFile:

    def test_an_unchanged_path_refuses_nothing(self, repository):
        verdicts = repository.judge({'docs/readme.md': 'changed, not python\n'})
        assert {v.change for v in verdicts.values()} == {GateFileChange.UNCHANGED}
        assert not any(v.is_refused() for v in verdicts.values())

    def test_the_path_holds_what_its_entry_points_reach_and_nothing_served(self, repository):
        verdicts = repository.judge({})
        assert set(verdicts) == {'python/framework/process/process_main.py',
                                 'python/framework/process/process_executor.py',
                                 _PREP, _ENGINE, _LEAVING}

    def test_a_docstring_change_passes(self, repository):
        engine = _RELEASE_FILES[_ENGINE].replace('Run it.', 'Run the engine once.')
        verdict = repository.judge({_ENGINE: engine})[_ENGINE]
        assert verdict.change is GateFileChange.DOCSTRINGS_ONLY and not verdict.is_refused()

    def test_a_code_change_is_refused_undeclared(self, repository):
        verdict = repository.judge({_ENGINE: _RELEASE_FILES[_ENGINE].replace('1', '2')})[_ENGINE]
        assert verdict.change is GateFileChange.CHANGED and verdict.is_refused()

    def test_a_code_change_passes_declared_at_its_tree(self, repository):
        engine = _RELEASE_FILES[_ENGINE].replace('1', '2')
        verdict = repository.judge({_ENGINE: engine}, {_ENGINE: _declared(engine, 'algo_view')})
        assert not verdict[_ENGINE].is_refused()
        assert verdict[_ENGINE].declaration.categories == (GateChangeCategory.ALGO_VIEW,)

    def test_a_declared_file_that_changed_again_is_refused(self, repository):
        declared = _RELEASE_FILES[_ENGINE].replace('1', '2')
        changed_again = _RELEASE_FILES[_ENGINE].replace('1', '3')
        verdict = repository.judge({_ENGINE: changed_again},
                                   {_ENGINE: _declared(declared)})[_ENGINE]
        assert verdict.change is GateFileChange.CHANGED and verdict.is_refused()

    def test_a_docstring_fix_keeps_a_declaration(self, repository):
        declared = _RELEASE_FILES[_ENGINE].replace('1', '2')
        with_new_docstring = declared.replace('Run it.', 'Run it, once.')
        verdict = repository.judge({_ENGINE: with_new_docstring},
                                   {_ENGINE: _declared(declared)})[_ENGINE]
        assert not verdict.is_refused()

    def test_a_new_file_entering_the_path_is_refused_undeclared(self, repository):
        recorder = 'RECORDS = True\n'
        files = {_RECORDER: recorder, 'python/framework/process/process_main.py':
                 'from python.framework.core.engine import run\n'
                 'from python.framework.core.recorder import RECORDS\n'}
        undeclared = repository.judge(files)[_RECORDER]
        assert undeclared.change is GateFileChange.ENTERED_PATH and undeclared.is_refused()

    def test_an_existing_file_newly_imported_enters_the_path(self, repository):
        files = {_ENGINE: (_RELEASE_FILES[_ENGINE]
                           + 'from python.framework.core.unused import UNUSED\n')}
        declarations = {_ENGINE: _declared(files[_ENGINE]),
                        _UNUSED: _declared(_RELEASE_FILES[_UNUSED])}
        verdicts = repository.judge(files, declarations)
        assert verdicts[_UNUSED].change is GateFileChange.ENTERED_PATH
        assert not any(v.is_refused() for v in verdicts.values())

    def test_a_file_leaving_the_path_is_refused_undeclared(self, repository):
        prep = 'from python.framework.types.api.served import Model\n'
        verdicts = repository.judge({_PREP: prep}, {_PREP: _declared(prep)})
        assert verdicts[_LEAVING].change is GateFileChange.LEFT_PATH
        assert verdicts[_LEAVING].is_refused()

    def test_a_file_leaving_the_path_passes_declared_whatever_its_tree(self, repository):
        prep = 'from python.framework.types.api.served import Model\n'
        verdicts = repository.judge({_PREP: prep}, {
            _PREP: _declared(prep), _LEAVING: {'categories': ('recording_only',),
                                               'syntax_digest': 'f' * 64}})
        assert not verdicts[_LEAVING].is_refused()

    def test_a_served_model_is_outside_the_path(self, repository):
        verdicts = repository.judge({_SERVED: 'Model = 2\n'})
        assert _SERVED not in verdicts
        assert not any(v.is_refused() for v in verdicts.values())

    def test_a_malformed_declaration_is_a_fault(self, repository):
        with pytest.raises(ValueError, match='malformed'):
            repository.judge({}, {_ENGINE: {'categories': ('no_such_category',),
                                            'syntax_digest': 'f' * 64}})


class TestTheGateOverTheCode:

    @staticmethod
    def _gate(repository, sessions, backtest_commit):
        backtest = gate_backtest(identity=code_identity(framework_commit=backtest_commit))
        return check_divergence_gate(sessions, backtest, gate_window(),
                                     read_tree=repository.read, allowed_changes={})

    @staticmethod
    def _checks(result):
        return {finding.check for finding in result.findings}

    def test_the_release_against_an_unchanged_commit_is_comparable(self, repository):
        result = self._gate(repository, [gate_session(identity=code_identity(
            framework_commit=repository.release))], repository.release)
        assert result.is_comparable(), result.findings
        assert result.code_path is not None and result.code_path.release == '1.5.0'

    def test_an_undeclared_change_refuses_and_names_the_file(self, repository):
        backtest = repository.backtest({_ENGINE: _RELEASE_FILES[_ENGINE].replace('1', '2')})
        result = self._gate(repository, [gate_session(identity=code_identity(
            framework_commit=repository.release))], backtest)
        refusal, = [f for f in result.findings if f.check == CODE_PATH_CHECK]
        assert refusal.scope == _ENGINE and 'not declared' in refusal.message

    def test_a_release_without_declarations_permits_no_change(self, repository):
        backtest = repository.backtest({_ENGINE: _RELEASE_FILES[_ENGINE].replace('1', '2')})
        session = gate_session(release='0.0.1', identity=code_identity(
            framework_commit=repository.release))
        result = check_divergence_gate(
            [session], gate_backtest(identity=code_identity(framework_commit=backtest)),
            gate_window(), read_tree=repository.read)
        assert CODE_PATH_CHECK in self._checks(result)

    def test_an_uncommitted_session_is_refused(self, repository):
        result = self._gate(repository, [gate_session(identity=code_identity(
            framework_commit=repository.release, framework_dirty=True))], repository.release)
        assert UNCOMMITTED_CHECK in self._checks(result) and result.code_path is None

    def test_an_uncommitted_backtest_is_refused(self, repository):
        backtest = gate_backtest(identity=code_identity(framework_commit=repository.release,
                                                        framework_dirty=True))
        result = check_divergence_gate(
            [gate_session(identity=code_identity(framework_commit=repository.release))],
            backtest, gate_window(), read_tree=repository.read)
        assert UNCOMMITTED_CHECK in self._checks(result) and result.code_path is None

    def test_a_commit_this_repository_does_not_hold_is_refused(self, repository):
        result = self._gate(repository, [gate_session(identity=code_identity(
            framework_commit='0123456789ab'))], repository.release)
        assert COMMIT_UNREADABLE_CHECK in self._checks(result)

    def test_sessions_on_two_commits_are_refused(self, repository):
        later = repository.backtest({'docs/readme.md': 'later\n'})
        sessions = [gate_session('s1', identity=code_identity(framework_commit=repository.release)),
                    gate_session('s2', identity=code_identity(framework_commit=later))]
        assert SESSIONS_DISAGREE_CHECK in self._checks(self._gate(repository, sessions, later))

    def test_a_session_whose_code_cannot_be_read_is_refused(self, repository):
        unknown = dataclasses.replace(gate_session(), code_identity=None)
        assert CODE_UNKNOWN_CHECK in self._checks(
            self._gate(repository, [unknown], repository.release))

    def test_a_session_naming_no_release_is_refused(self, repository):
        result = self._gate(repository, [gate_session(release=None, identity=code_identity(
            framework_commit=repository.release))], repository.release)
        assert CODE_UNKNOWN_CHECK in self._checks(result)


class TestTheGatesOwnDeclarations:

    def test_every_declaration_names_known_categories_and_a_full_digest(self):
        known = {category.value for category in GateChangeCategory}
        for release, files in ALLOWED_CHANGES_BY_RELEASE.items():
            assert files, release
            for path, entry in files.items():
                assert set(entry) == {'categories', 'syntax_digest'}, path
                assert entry['categories'] and set(entry['categories']) <= known, path
                assert re.fullmatch('[0-9a-f]{64}', entry['syntax_digest']), path
                assert path.startswith('python/') and path.endswith('.py'), path

    def test_every_category_is_declared_somewhere(self):
        used = {category for files in ALLOWED_CHANGES_BY_RELEASE.values()
                for entry in files.values() for category in entry['categories']}
        assert used == {category.value for category in GateChangeCategory}
