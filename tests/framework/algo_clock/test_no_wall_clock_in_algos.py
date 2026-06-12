"""
§9 wall-clock ban — CI lint.

Decision logic & workers must never read wall-clock directly (`datetime.now()`,
`datetime.utcnow()`, `time.time()`); the single canonical clock is
`DecisionTradingApi.get_current_time()`. A direct wall-clock call breaks backtest
reproducibility and decouples timing from the tick cadence that gates async resolution.

This lint AST-scans the shipped CORE algo surface so a regression fails CI. USER algos
(gitignored, never in CI) are covered at runtime by the startup validator (#359),
which owns the shared scanning core (find_wall_clock_calls).
"""

from pathlib import Path
from typing import List

from python.framework.validators.algo_clock_validator import find_wall_clock_calls

# Repo-relative roots holding the shipped algo surface.
_SCAN_DIRS = (
    'python/framework/decision_logic',
    'python/framework/workers',
)


def test_no_wall_clock_in_decision_logic_or_workers():
    """Decision-logic / worker code must use get_current_time(), never wall-clock."""
    repo_root = Path(__file__).resolve().parents[3]
    violations: List[str] = []
    for rel in _SCAN_DIRS:
        for py in (repo_root / rel).rglob('*.py'):
            violations.extend(find_wall_clock_calls(py))
    assert not violations, (
        'Wall-clock read in decision logic / worker code — use '
        'self.trading_api.get_current_time() (§9):\n  ' + '\n  '.join(violations)
    )
