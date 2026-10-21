"""
The data home (#598): every operator-owned path resolves under the directory FINIEX_DATA_HOME
names, and with the variable unset every path stays exactly as configured.

Two guards hold its reach complete. A walk over the store catalog in a fresh process, with the
variable set, finds every root under the home but the committed stores and the machine-wide list
of what runs; and no module but the data home itself builds a path into the workspace.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List

import pytest

from python.configuration.data_home import (
    DATA_HOME_ENV,
    WORKSPACE_DIR,
    describe_data_home,
    get_data_home,
    resolve_in_data_home,
    resolve_strategy_file,
    workspace_path,
)
from python.framework.exceptions.data_home_errors import DataHomeError
from python.framework.types.store_types import StoreId

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The stores whose roots belong to the code tree (committed) or to the machine (#603) — never a home.
OUTSIDE_THE_HOME = {StoreId.CERTIFICATES, StoreId.GENERATOR_PROFILES, StoreId.ACTIVITIES}

# Prints every registered store's root and index file, as one JSON line after whatever the
# configuration prints while it loads.
_CATALOG_PROBE = (
    'import json\n'
    'from python.framework.store.store_catalog import StoreCatalog\n'
    'print(json.dumps({d.store_id.value: [str(d.root), str(d.index_path) if d.index_path else None]'
    ' for d in StoreCatalog().all()}))\n'
)


def _last_line_of_fresh_process(code: str, env_update: Dict[str, str]) -> str:
    """
    Run code in a fresh interpreter at the project root — no fixture of this session reaches it.

    Args:
        code: The program
        env_update: Variables set on top of this process's environment

    Returns:
        The last line it printed
    """
    env = dict(os.environ, **env_update)
    env['PYTHONPATH'] = str(PROJECT_ROOT)
    result = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip().splitlines()[-1]


class TestWithoutAHome:

    def test_a_path_stays_exactly_as_configured(self, monkeypatch):
        monkeypatch.delenv(DATA_HOME_ENV, raising=False)
        assert get_data_home() is None
        assert resolve_in_data_home('runs/ledger') == 'runs/ledger'
        assert resolve_in_data_home(Path('logs')) == Path('logs')
        assert workspace_path('app_config.json') == Path(WORKSPACE_DIR) / 'app_config.json'

    def test_the_boot_line_names_the_working_directory(self, monkeypatch):
        monkeypatch.delenv(DATA_HOME_ENV, raising=False)
        assert f'{DATA_HOME_ENV} is not set' in describe_data_home()


class TestWithAHome:

    @pytest.fixture
    def home(self, tmp_path, monkeypatch) -> Path:
        monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path))
        return tmp_path

    def test_a_relative_path_moves_under_it_and_keeps_its_type(self, home):
        assert resolve_in_data_home('runs/ledger') == str(home / 'runs' / 'ledger')
        assert resolve_in_data_home(Path('logs')) == home / 'logs'

    def test_an_absolute_path_stands_for_itself(self, home):
        assert resolve_in_data_home('/srv/elsewhere/runs') == '/srv/elsewhere/runs'

    def test_the_workspace_lies_under_it(self, home):
        assert workspace_path('credentials') == home / WORKSPACE_DIR / 'credentials'

    def test_a_strategy_file_is_looked_up_in_the_home_first_then_in_the_tree(self, home):
        strategy = home / 'user_algos' / 'my_algo' / 'my_strategy.py'
        strategy.parent.mkdir(parents=True)
        strategy.write_text('')
        assert resolve_strategy_file('user_algos/my_algo/my_strategy.py') == strategy.resolve()
        assert (resolve_strategy_file('tests/fixtures/not_in_the_home.py')
                == (Path.cwd() / 'tests/fixtures/not_in_the_home.py').resolve())

    def test_the_boot_line_names_it(self, home):
        line = describe_data_home()
        assert str(home) in line and DATA_HOME_ENV in line


class TestARefusedHome:

    def test_a_missing_directory_is_refused_and_not_created(self, tmp_path, monkeypatch):
        absent = tmp_path / 'absent'
        monkeypatch.setenv(DATA_HOME_ENV, str(absent))
        with pytest.raises(DataHomeError, match='not an existing directory'):
            get_data_home()
        assert not absent.exists()

    def test_a_relative_value_is_refused(self, monkeypatch):
        monkeypatch.setenv(DATA_HOME_ENV, 'relative/home')
        with pytest.raises(DataHomeError, match='relative path'):
            resolve_in_data_home('runs')


class TestTheStoresLieInTheHome:

    def test_every_store_root_but_the_committed_ones_and_the_machine_list(self, tmp_path):
        home = tmp_path / 'home'
        home.mkdir()
        roots = json.loads(_last_line_of_fresh_process(
            _CATALOG_PROBE, {DATA_HOME_ENV: str(home), 'FINIEX_CONFIG_ISOLATION': '1'}))
        assert roots, 'the catalog registered no store'

        outside = []
        for store_id, (root, index_path) in roots.items():
            in_the_home = all(Path(path).is_relative_to(home)
                              for path in (root, index_path) if path is not None)
            if (StoreId(store_id) in OUTSIDE_THE_HOME) == in_the_home:
                outside.append(f'{store_id}: {root}')
        assert not outside, (
            'a store root on the wrong side of the data home — a deploy into a new code tree '
            f'would start it empty, or a committed store would leave the tree: {outside}')

    def test_the_workspace_override_is_read_from_the_home(self, tmp_path):
        home = tmp_path / 'home'
        (home / WORKSPACE_DIR).mkdir(parents=True)
        (home / WORKSPACE_DIR / 'app_config.json').write_text(
            json.dumps({'activities': {'session_env_var': 'NAMED_IN_THE_HOME'}}), encoding='utf-8')
        value = _last_line_of_fresh_process(
            'from python.configuration.app_config_manager import AppConfigManager\n'
            'print(AppConfigManager().get_activity_session_env_var())\n',
            {DATA_HOME_ENV: str(home), 'FINIEX_CONFIG_ISOLATION': '0'})
        assert value == 'NAMED_IN_THE_HOME'


def _workspace_literals(source: str) -> Iterator[int]:
    """
    The lines where a string constant spells a path into the workspace.

    Docstrings are prose, and an enum member's value is a label naming where a file was found,
    not a path that is opened — both are passed over.

    Args:
        source: A module's source

    Returns:
        The line of every such constant
    """
    tree = ast.parse(source)
    passed_over = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                passed_over.add(id(first.value))
        if isinstance(node, ast.ClassDef) and any(
                getattr(base, 'id', getattr(base, 'attr', '')).endswith('Enum') for base in node.bases):
            passed_over.update(id(stmt.value) for stmt in node.body if isinstance(stmt, ast.Assign))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in passed_over
                and (node.value == WORKSPACE_DIR or node.value.startswith(WORKSPACE_DIR + '/'))):
            yield node.lineno


class TestNoSecondWorkspacePath:

    def test_only_the_data_home_builds_a_path_into_the_workspace(self):
        declaring = PROJECT_ROOT / 'python' / 'configuration' / 'data_home.py'
        modules: List[Path] = [
            path for path in sorted((PROJECT_ROOT / 'python').rglob('*.py'))
            if 'experiments' not in path.parts and path != declaring]
        assert modules, 'no module scanned'
        found = [f'{path.relative_to(PROJECT_ROOT)}:{line}'
                 for path in modules
                 for line in _workspace_literals(path.read_text(encoding='utf-8'))]
        assert not found, (
            f'a path into `{WORKSPACE_DIR}/` built outside the data home — it would stay in the code '
            f'tree when FINIEX_DATA_HOME names a home of its own: {found}')
