"""
FiniexTestingIDE - Algo Clock Validation (#359)

A pre-run check that inspects the algo itself (not its configuration): does the
loaded decision logic / worker source read wall-clock directly? §9 forbids
datetime.now() / datetime.utcnow() / time.time() in algo code — the single
canonical clock is DecisionTradingApi.get_current_time(). Member of the algo
pre-flight check family (siblings: #354 state-snapshot serializability,
#249 perf/output cert), run from a single thin call-site in each pipeline
(Simulation Phase 3 + AutoTrader startup).

USER algos live in gitignored user_algos/ and never reach CI — this runtime
scan is the only path that sees them. The shipped CORE surface is additionally
locked by the CI lint (tests/framework/algo_clock/), which reuses
find_wall_clock_calls() as its scanning core.
"""

import ast
import inspect
from pathlib import Path
from typing import Iterable, List

from python.framework.exceptions.algo_clock_errors import AlgoClockViolationError

# (module, attribute) wall-clock calls forbidden in decision-logic / worker code (§9).
FORBIDDEN_WALL_CLOCK_CALLS = {('datetime', 'now'), ('datetime', 'utcnow'), ('time', 'time')}


def find_wall_clock_calls(source_path: Path) -> List[str]:
    """
    Find forbidden wall-clock calls in a Python source file.

    AST-based (not grep) so comments, strings, and docstrings never produce
    false positives.

    Args:
        source_path: File to AST-scan

    Returns:
        List of 'file:line module.attr()' strings (empty when clean)
    """
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    hits: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = getattr(node.func.value, 'id', None)
            if (base, node.func.attr) in FORBIDDEN_WALL_CLOCK_CALLS:
                hits.append(f'{source_path}:{node.lineno} {base}.{node.func.attr}()')
    return hits


def collect_algo_clock_violations(classes: Iterable[type]) -> List[str]:
    """
    Scan the source files of the given algo classes for wall-clock calls.

    Resolves each class to its defining source file via inspect; files are
    deduplicated so co-located classes are scanned once. Classes without a
    resolvable source file (builtins, REPL) are skipped silently — there is
    nothing to scan.

    Args:
        classes: Loaded decision-logic / worker classes to inspect

    Returns:
        List of 'file:line module.attr()' violation strings (empty when clean)
    """
    source_files: List[Path] = []
    seen: set = set()
    for cls in classes:
        try:
            source_file = inspect.getsourcefile(cls)
        except TypeError:
            continue
        if source_file is None or source_file in seen:
            continue
        seen.add(source_file)
        source_files.append(Path(source_file))

    violations: List[str] = []
    for source_path in source_files:
        violations.extend(find_wall_clock_calls(source_path))
    return violations


def validate_algo_clock(classes: Iterable[type]) -> None:
    """
    Assert that none of the given algo classes read wall-clock directly.

    Args:
        classes: Loaded decision-logic / worker classes to inspect

    Returns:
        None — raises AlgoClockViolationError listing every file:line hit
    """
    violations = collect_algo_clock_violations(classes)
    if violations:
        raise AlgoClockViolationError(
            'Wall-clock read in decision logic / worker code — use '
            'self.trading_api.get_current_time() (§9):\n  ' + '\n  '.join(violations)
        )
