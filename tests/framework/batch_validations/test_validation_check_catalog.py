"""
The validation check catalog is complete — in both directions.

A finding's `check` id reaches a consumer as a facet, and the catalog is what gives that id a name
and a sentence. It is only worth anything if it is complete: an id the code emits without an entry
is a facet nobody can label, and an entry for a check the code no longer emits describes a finding
nobody can receive. Neither is visible by reading, so both are asserted here.

The emitted ids are found in the SOURCE, not listed here: a keyword `check='…'`, a module-level
`…_CHECK = '…'` constant, and the helpers that pass an id on — `_add` / `_finding` (first
argument) and `_as` (third). A new way of passing an id that none of these catches would make the
first test below vacuous for it, which is why the walk has to find a non-trivial number of ids.
"""

import ast
from pathlib import Path
from typing import Dict, List

from python.framework.validators.validation_check_catalog import (
    VALIDATION_CHECKS,
    VALIDATION_CHECKS_BY_ID,
)

_SOURCE_ROOT = Path('python')
# Helpers that receive a check id and build the finding: name → index of the id argument.
_ID_ARGUMENT = {'_add': 0, '_finding': 0, '_as': 2}


def _emitted_checks() -> Dict[str, List[str]]:
    """
    Every check id the code can put on a finding, with where it is.

    Returns:
        id → the file:line places it is passed
    """
    found: Dict[str, List[str]] = {}
    for path in _SOURCE_ROOT.rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            for value in _ids_in(node):
                found.setdefault(value, []).append(f'{path}:{node.lineno}')
    return found


def _ids_in(node: ast.AST) -> List[str]:
    """
    The check ids one AST node passes, by the three routes an id travels.

    Args:
        node: Any node

    Returns:
        The string ids it carries; empty for a node that carries none
    """
    ids: List[str] = []
    if isinstance(node, ast.Call):
        ids += [kw.value.value for kw in node.keywords
                if kw.arg == 'check' and _is_text(kw.value)]
        name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
        position = _ID_ARGUMENT.get(name)
        if position is not None and len(node.args) > position and _is_text(node.args[position]):
            ids.append(node.args[position].value)
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id.endswith('_CHECK')
            and _is_text(node.value)):
        ids.append(node.value.value)
    return ids


def _is_text(node: ast.AST) -> bool:
    """
    Whether a node is a string literal.

    Args:
        node: Any node

    Returns:
        True for a constant str
    """
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


EMITTED = _emitted_checks()


def test_the_walk_found_the_checks():
    """A walk that came back nearly empty would make both directions below vacuous."""
    assert len(EMITTED) > 30


def test_every_emitted_check_is_declared():
    missing = {check: places[0] for check, places in EMITTED.items()
               if check not in VALIDATION_CHECKS_BY_ID}
    assert not missing, f'checks with no catalog entry: {missing}'


def test_every_declared_check_is_emitted_somewhere():
    stale = sorted(set(VALIDATION_CHECKS_BY_ID) - set(EMITTED))
    assert not stale, f'catalog entries no finding can carry: {stale}'


def test_every_id_is_declared_once():
    ids = [info.check for info in VALIDATION_CHECKS]
    assert len(ids) == len(set(ids))


def test_every_entry_is_a_label_and_one_sentence_for_a_person():
    for info in VALIDATION_CHECKS:
        assert info.title and info.description, info.check
        assert '\n' not in info.title + info.description, info.check
        assert '`' not in info.title + info.description, info.check
        assert info.description.endswith('.'), info.check
