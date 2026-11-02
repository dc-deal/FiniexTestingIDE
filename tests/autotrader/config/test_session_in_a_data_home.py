"""
A whole session with the code tree and the data home apart, the way a deploy runs one.

The units of the data home have their own tests; what only a whole boot shows is that every step
of it looks a strategy up in the same place. Measured on the first real host: a worker named
relative to its strategy file failed the type check, and a strategy importing its own sibling
failed to load, while the suite, whose working directory is the home, stayed green.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ALGO = PROJECT_ROOT / 'tests' / 'fixtures' / 'data_home_session' / 'my_algo'


def _run_session_in_a_home(home: Path) -> subprocess.CompletedProcess:
    """
    Run a short mock session whose strategy and stores live in the home, from the code tree.

    Args:
        home: The directory to use as the data home

    Returns:
        The finished process
    """
    shutil.copytree(FIXTURE_ALGO, home / 'user_algos' / 'my_algo')
    (home / 'data').mkdir()
    (home / 'data' / 'processed').symlink_to(PROJECT_ROOT / 'data' / 'processed')
    shutil.copytree(PROJECT_ROOT / 'data' / 'runtime' / 'brokers', home / 'data' / 'runtime' / 'brokers')
    profile = home / 'my_profile.json'
    profile.write_text(json.dumps({
        'profile_name': 'my_profile',
        'run_purpose': 'fixture',
        'bot_id': 'myhome01',
        'symbol': 'BTCUSD',
        'broker_type': 'kraken_spot',
        'adapter_type': 'mock',
        'deployment': {'continuous': False},
        'tick_source': {'type': 'mock'},
        'display': {'enabled': False},
        'scenario_settings': {
            'start_date': '2026-01-25T02:19:46+00:00',
            'max_ticks': 200,
            'balances': {'USD': 10000.0, 'BTC': 0.0},
        },
        'strategy_config': {
            'decision_logic_type': 'user_algos/my_algo/my_decision.py',
            'worker_instances': {'my_worker': 'user_algos/my_algo/my_worker.py'},
            'workers': {'my_worker': {'periods': {'M5': 1}}},
            'decision_logic_config': {},
        },
    }))
    env = {
        **os.environ,
        'FINIEX_DATA_HOME': str(home),
        'PYTHONPATH': f'{PROJECT_ROOT}{os.pathsep}{home}',
    }
    return subprocess.run(
        [sys.executable, 'python/cli/autotrader_cli.py', 'run', '--config', str(profile)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=240)


def test_a_session_runs_from_the_tree_on_a_strategy_in_the_home(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    result = _run_session_in_a_home(home)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert list((home / 'runs' / 'autotrader' / 'my_profile').glob('*/header.json'))
