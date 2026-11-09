"""
The End-of-Run Consoles Compute Nothing (#557).

A console of the end-of-run report formats and loops; every figure it prints is derived once, on
the model, so the file, the CSV and the API serve the same number. This suite is the lock on that
rule: it walks every console module the two report coordinators reach — through their imports,
not through a list kept here — and refuses an aggregate call in one.

What a console may still do with `max` / `min` is size a column: a call over string lengths and
constants only. Anything else — a `sum`, a `max` over values, a mean, a `Counter` — is a figure,
and belongs on the model.
"""

import ast
from pathlib import Path
from typing import List, Set

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_CONSOLE_PACKAGE = 'python.framework.reporting.console'

# The two places the end-of-run report is put together — the rule's scope is what they render
_COORDINATORS = (
    _ROOT / 'python/framework/batch/batch_report_coordinator.py',
    _ROOT / 'python/framework/autotrader/run_record/autotrader_report_coordinator.py',
)

# Aggregates a console must not call, whatever their arguments
_ALWAYS_FORBIDDEN = {'sum', 'Counter', 'mean', 'fmean', 'median', 'pstdev', 'stdev'}
# Aggregates a console may call to size a column — over lengths and constants only
_SIZING = {'max', 'min'}


def _console_imports(path: Path) -> Set[str]:
    """
    The console modules a file imports.

    Args:
        path: A Python file

    Returns:
        The dotted names of the console modules it imports
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            and node.module.startswith(_CONSOLE_PACKAGE + '.')}


def _end_of_run_consoles() -> List[Path]:
    """
    Every console module the report coordinators reach, transitively through their imports.

    Returns:
        The module files, sorted
    """
    seen: Set[str] = set()
    frontier = set().union(*(_console_imports(path) for path in _COORDINATORS))
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        frontier |= _console_imports(_ROOT / (module.replace('.', '/') + '.py')) - seen
    return sorted(_ROOT / (module.replace('.', '/') + '.py') for module in seen)


_CONSOLES = _end_of_run_consoles()


def _call_name(node: ast.Call) -> str:
    """
    The name a call is made by — `max`, `statistics.mean` read as `mean`.

    Args:
        node: The call

    Returns:
        The called name, '' where it is not a plain name or attribute
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ''


def _only_lengths(node: ast.AST) -> bool:
    """
    Whether an expression yields nothing but string lengths and constants.

    Args:
        node: The expression

    Returns:
        True for a constant, a `len(...)`, and sequences, comprehensions and sums of those
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == 'len':
            return True
        if name in _SIZING:
            return all(_only_lengths(arg) for arg in node.args)
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_only_lengths(element) for element in node.elts)
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
        return _only_lengths(node.elt)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _only_lengths(node.left) and _only_lengths(node.right)
    return False


def _offences(path: Path) -> List[str]:
    """
    The aggregate calls a console module makes.

    Args:
        path: The module

    Returns:
        One line per offending call: its line and its source
    """
    source = path.read_text(encoding='utf-8')
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _ALWAYS_FORBIDDEN or (name in _SIZING and not _only_lengths(node)):
            found.append(f'{path.name}:{node.lineno}: {ast.get_source_segment(source, node)}')
    return found


def test_the_walk_finds_the_consoles():
    # The executive summary and the session summary are each reached by one coordinator only
    names = {path.name for path in _CONSOLES}
    assert {'sim_executive_summary.py', 'autotrader_session_summary.py',
            'run_console_renderer.py', 'trade_history_summary.py'} <= names


@pytest.mark.parametrize('path', _CONSOLES, ids=lambda path: path.name)
def test_an_end_of_run_console_computes_no_figure(path):
    offences = _offences(path)
    assert not offences, (
        'an end-of-run console computes a figure — derive it on the model:\n  '
        + '\n  '.join(offences))


class TestTheLockItself:
    """The lock refuses what it should and lets a column width through."""

    def _check(self, code: str, tmp_path) -> List[str]:
        module = tmp_path / 'sample_console.py'
        module.write_text(code, encoding='utf-8')
        return _offences(module)

    def test_a_sum_is_refused(self, tmp_path):
        assert self._check('total = sum(row.net_pnl for row in rows)\n', tmp_path)

    def test_a_max_over_values_is_refused(self, tmp_path):
        assert self._check('worst = max(rows, key=lambda r: r.loss)\n', tmp_path)

    def test_a_column_width_passes(self, tmp_path):
        assert not self._check('width = max(max(len(p.name) for p in phases), 20)\n', tmp_path)
        assert not self._check('width = max([len("file")] + [len(r.file) for r in rows])\n',
                               tmp_path)
