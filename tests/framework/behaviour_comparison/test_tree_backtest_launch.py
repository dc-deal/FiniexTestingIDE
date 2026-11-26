"""
The one launch both code-stand comparisons start a backtest with (#641, shared with #512's A/A):
the tree as working directory and first on the import path, the data home named, the caller's
variables added — and the lines a failed run is quoted by.
"""

import subprocess
import sys
from pathlib import Path

from python.framework.utils import tree_backtest_utils
from python.framework.utils.tree_backtest_utils import (
    RAN_EXIT_CODES,
    STRATEGY_RUNNER,
    error_lines,
    run_backtest_in_tree,
)


class TestTheLaunch:

    def test_the_tree_and_the_home_are_where_the_run_looks(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(command, **kwargs):
            seen.update(command=command, **kwargs)
            return subprocess.CompletedProcess(command, 0, '', '')

        monkeypatch.setattr(tree_backtest_utils.subprocess, 'run', fake_run)
        tree, home, set_path = tmp_path / 'tree', tmp_path / 'home', tmp_path / 'set.json'
        run_backtest_in_tree(tree, home, set_path, {'HOME': str(tmp_path)})
        assert seen['command'] == [sys.executable, str(STRATEGY_RUNNER), 'run', str(set_path)]
        assert seen['cwd'] == tree
        environment = seen['env']
        assert environment['FINIEX_DATA_HOME'] == str(home)
        assert environment['PYTHONPATH'].split(':') == [str(tree), str(home)]
        assert environment['HOME'] == str(tmp_path)

    def test_a_run_with_logged_errors_still_ran(self):
        assert RAN_EXIT_CODES == (0, 3)


class TestTheErrorLines:

    def test_the_last_error_lines_are_quoted_without_colour(self):
        output = '\n'.join(['starting', '\x1b[31mError: first\x1b[0m', 'Error: second',
                            'Error: third', 'Error: fourth', 'summary'])
        assert error_lines(output) == 'Error: second · Error: third · Error: fourth'

    def test_without_an_error_line_the_last_lines_are_quoted(self):
        assert error_lines('a\nb\n\nc\nd') == 'b · c · d'

    def test_the_runner_is_a_path_inside_the_tree(self):
        assert not STRATEGY_RUNNER.is_absolute() and STRATEGY_RUNNER.parts[0] == 'python'
        assert (Path(__file__).resolve().parents[3] / STRATEGY_RUNNER).is_file()
