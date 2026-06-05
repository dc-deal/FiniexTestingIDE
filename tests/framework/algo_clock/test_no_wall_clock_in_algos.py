"""
§9 wall-clock ban — CI lint.

Decision logic & workers must never read wall-clock directly (`datetime.now()`,
`datetime.utcnow()`, `time.time()`); the single canonical clock is
`DecisionTradingApi.get_current_time()`. A direct wall-clock call breaks backtest
reproducibility and decouples timing from the tick cadence that gates async resolution.

This lint AST-scans the shipped CORE algo surface so a regression fails CI. USER algos
(gitignored, never in CI) are covered at runtime by the startup validator (#359).
"""

import ast
from pathlib import Path
from typing import List

# (module, attribute) wall-clock calls forbidden in decision-logic / worker code.
_FORBIDDEN = {('datetime', 'now'), ('datetime', 'utcnow'), ('time', 'time')}

# Repo-relative roots holding the shipped algo surface.
_SCAN_DIRS = (
    'python/framework/decision_logic',
    'python/framework/workers',
)


def _wall_clock_calls(source_path: Path) -> List[str]:
    """
    Find forbidden wall-clock calls in a Python source file.

    Args:
        source_path: File to AST-scan

    Returns:
        List of 'file:line call' strings (empty when clean)
    """
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    hits: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = getattr(node.func.value, 'id', None)
            if (base, node.func.attr) in _FORBIDDEN:
                hits.append(f'{source_path}:{node.lineno} {base}.{node.func.attr}()')
    return hits


def test_no_wall_clock_in_decision_logic_or_workers():
    """Decision-logic / worker code must use get_current_time(), never wall-clock."""
    repo_root = Path(__file__).resolve().parents[3]
    violations: List[str] = []
    for rel in _SCAN_DIRS:
        for py in (repo_root / rel).rglob('*.py'):
            violations.extend(_wall_clock_calls(py))
    assert not violations, (
        'Wall-clock read in decision logic / worker code — use '
        'self.trading_api.get_current_time() (§9):\n  ' + '\n  '.join(violations)
    )
