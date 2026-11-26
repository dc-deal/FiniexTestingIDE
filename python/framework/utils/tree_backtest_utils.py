"""
One backtest run from a code tree against a data home, as a process of its own.

A comparison of two code stands needs each stand's backtest to run on that stand's code: its tree
first on the import path, its data home named by FINIEX_DATA_HOME, its own interpreter process so
nothing of the caller's imports leaks in. The A/A of #512 and the behaviour comparison (#641) both
start their stands this way, so the launch, the exit codes that mean "it ran" and the lines a
failed run is quoted by live here once.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from python.configuration.data_home import DATA_HOME_ENV

# The backtest command a stand runs, relative to its tree
STRATEGY_RUNNER = Path('python') / 'cli' / 'strategy_runner_cli.py'

# A backtest that finished with logged errors still computed its events (exit 3); any other
# non-zero exit means it did not run
RAN_EXIT_CODES = (0, 3)

# The console's colour codes, removed from a failed run's lines before they are quoted
_ANSI = re.compile(r'\x1b\[[0-9;]*m')

# How many of a failed run's error lines a refusal quotes
_ERROR_LINES_QUOTED = 3


def run_backtest_in_tree(tree: Path, home: Path, set_path: Path,
                         extra_environment: Optional[Dict[str, str]] = None
                         ) -> subprocess.CompletedProcess:
    """
    Run one scenario set from a code tree against a data home and wait for it.

    Args:
        tree: The code tree; it is the working directory and first on the import path
        home: The data home the run reads and writes; second on the import path, for its
            strategy directories
        set_path: The scenario set
        extra_environment: Variables a caller adds for its stands — a scratch HOME, a switch
            against bytecode; None adds nothing. The data home and the import path stay the
            launch's own: a caller cannot point a stand at another home by accident

    Returns:
        The finished process, its output captured as text
    """
    environment = {**os.environ, **(extra_environment or {}), DATA_HOME_ENV: str(home),
                   'PYTHONPATH': f'{tree}{os.pathsep}{home}'}
    return subprocess.run(
        [sys.executable, str(STRATEGY_RUNNER), 'run', str(set_path)],
        cwd=tree, env=environment, capture_output=True, text=True, check=False)


def error_lines(output: str) -> str:
    """
    The lines a failed backtest named its errors in, to quote in its refusal.

    Args:
        output: What the backtest printed

    Returns:
        Its last lines naming an error — the batch's summary names the cause last — or its last
        lines where none does
    """
    lines = [' '.join(_ANSI.sub('', line).split()) for line in output.splitlines()]
    lines = [line for line in lines if line]
    errors = [line for line in lines if line.startswith('Error:')]
    return ' · '.join((errors or lines)[-_ERROR_LINES_QUOTED:])
